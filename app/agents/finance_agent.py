"""Finance Agent implementation using the frozen Gateway boundary (D07)."""

from __future__ import annotations

import json
from typing import List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.clients.gateway import GatewayClient, GatewayCompletion
from app.domain.finance_models import (
    FINANCE_SCHEMA_VERSION,
    FinanceAgentRequest,
    FinanceAgentResultV1,
    FinanceTaskContext,
)
from app.models import ChatMessage, Role


FINANCE_AGENT_ID = "finance-agent"
FINANCE_DEFAULT_VIRTUAL_MODEL = "cofounder-auto"
FINANCE_MAX_TOKENS = 8192
FINANCE_ALLOWED_VIRTUAL_MODELS = frozenset(
    {"cofounder-auto", "cofounder-qwen", "cofounder-step"}
)


class FinanceAgentError(RuntimeError):
    """Base Finance Agent error."""


class FinanceAgentValidationFailure(FinanceAgentError):
    """Raised when model output remains invalid after bounded repair."""

    def __init__(
        self,
        message: str,
        validation_errors: Optional[List[str]] = None,
    ) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors or []


class FinanceGatewayProtocol(BaseModel):
    """Frozen Gateway invocation settings for Finance."""

    model_config = ConfigDict(extra="forbid")

    virtual_model: str = FINANCE_DEFAULT_VIRTUAL_MODEL
    temperature: float = Field(default=0.1, ge=0, le=2)
    max_tokens: int = Field(default=FINANCE_MAX_TOKENS, ge=1, le=128_000)
    response_format: Literal["json_object"] = "json_object"


def _result_schema_prompt() -> str:
    return """
{
  "schema_version": "1.0",
  "revenue_assumptions": [{
    "stream": "string", "pricing_model": "string", "currency": "USD",
    "unit_price": 0, "monthly_units": 0, "monthly_growth_rate": 0,
    "rationale": "string"
  }],
  "cost_structure": [{
    "name": "string", "category": "fixed|variable|one_time",
    "amount": 0, "currency": "USD", "period": "month|year|one_time",
    "rationale": "string"
  }],
  "unit_economics": {
    "currency": "USD", "average_revenue_per_unit": 0,
    "variable_cost_per_unit": 0, "contribution_margin_per_unit": 0,
    "contribution_margin_ratio": 0, "customer_acquisition_cost": 0,
    "lifetime_value": 0, "ltv_cac_ratio": 0, "payback_months": 0
  },
  "budget_scenarios": [{
    "name": "downside|base|upside", "monthly_revenue": 0,
    "monthly_cost": 0, "runway_months": null, "break_even_month": null,
    "assumptions": ["string"]
  }],
  "financial_risks": [{
    "risk": "string", "probability": "high|medium|low",
    "impact": "high|medium|low", "amount_at_risk": null,
    "currency": null, "mitigation": "string"
  }],
  "decision_thresholds": [{
    "metric": "string", "proceed_if": "string", "pause_if": "string",
    "stop_if": "string", "measurement_period": "string"
  }]
}""".strip()


def _build_system_prompt(
    context: FinanceTaskContext,
    include_founder_context: bool,
) -> str:
    sections = [
        f"You are the CoFounder OS Finance Agent (schema {FINANCE_SCHEMA_VERSION}).",
        "Use explicit assumptions. Never invent precision or execute transactions.",
        f"Objective: {context.objective}",
        f"Task: {context.task_title} — {context.task_description}",
        f"Required deliverable: {context.required_deliverable}",
    ]
    if include_founder_context and context.founder_context:
        sections.append(f"Founder context: {context.founder_context}")
    if context.constraints:
        sections.append("Constraints:\n" + "\n".join(
            f"- {item}" for item in context.constraints
        ))
    if context.dependency_artifact_summaries:
        sections.append(
            "Dependency evidence:\n"
            + "\n".join(
                f"- {summary.artifact_id} ({summary.checksum}): {summary.summary}"
                for summary in context.dependency_artifact_summaries
            )
        )
    sections.extend([
        "Respond with ONLY a JSON object matching this exact schema:",
        _result_schema_prompt(),
        (
            "All six financial sections are required and non-empty. "
            "budget_scenarios must contain exactly one downside, one base, "
            "and one upside scenario. "
            "Use finite numbers only. Do not include unknown fields."
        ),
    ])
    return "\n\n".join(sections)


def _build_user_message(context: FinanceTaskContext) -> str:
    return (
        f"Prepare the bounded financial analysis for {context.task_title}.\n"
        f"Objective: {context.objective}\n"
        f"Deliverable: {context.required_deliverable}"
    )


def _build_repair_prompt(content: str, errors: List[str]) -> str:
    return "\n".join([
        "Repair the previous FinanceAgentResultV1 response.",
        "Return only corrected JSON and do not add commentary.",
        f"Previous response: {content}",
        "Validation errors:",
        *[f"- {error}" for error in errors],
    ])


class FinanceAgent:
    """Validate Finance output with at most one repair call."""

    def __init__(
        self,
        gateway_client: GatewayClient,
        protocol: Optional[FinanceGatewayProtocol] = None,
    ) -> None:
        self.gateway = gateway_client
        self.protocol = protocol or FinanceGatewayProtocol()
        if self.protocol.virtual_model not in FINANCE_ALLOWED_VIRTUAL_MODELS:
            raise ValueError(
                f"Virtual model {self.protocol.virtual_model!r} is not allowed"
            )

    async def execute(
        self,
        request: FinanceAgentRequest,
    ) -> tuple[FinanceAgentResultV1, GatewayCompletion]:
        context = request.context
        system_prompt = _build_system_prompt(
            context,
            request.include_founder_context,
        )
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt),
            ChatMessage(role=Role.USER, content=_build_user_message(context)),
        ]
        completion = await self._call_gateway(messages, request.virtual_model)
        result, errors = self._parse_and_validate(completion.content)
        if result is not None:
            return result, completion

        if request.max_repair_attempts:
            repair_messages = [
                *messages,
                ChatMessage(role=Role.ASSISTANT, content=completion.content),
                ChatMessage(
                    role=Role.USER,
                    content=_build_repair_prompt(completion.content, errors),
                ),
            ]
            repaired = await self._call_gateway(
                repair_messages,
                request.virtual_model,
            )
            result, repair_errors = self._parse_and_validate(repaired.content)
            if result is not None:
                return result, repaired
            raise FinanceAgentValidationFailure(
                "Finance Agent validation failed after repair",
                repair_errors,
            )

        raise FinanceAgentValidationFailure(
            "Finance Agent validation failed",
            errors,
        )

    async def _call_gateway(
        self,
        messages: Sequence[ChatMessage],
        virtual_model: Optional[str],
    ) -> GatewayCompletion:
        model = virtual_model or self.protocol.virtual_model
        if model not in FINANCE_ALLOWED_VIRTUAL_MODELS:
            raise ValueError(f"Virtual model {model!r} is not allowed")
        return await self.gateway.complete(
            messages,
            model=model,
            temperature=self.protocol.temperature,
            max_tokens=self.protocol.max_tokens,
        )

    @staticmethod
    def _parse_and_validate(
        content: str,
    ) -> tuple[Optional[FinanceAgentResultV1], List[str]]:
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            return None, [f"Invalid JSON: {exc}"]
        if not isinstance(value, dict):
            return None, ["Response must be a JSON object"]
        try:
            return FinanceAgentResultV1.model_validate(value), []
        except Exception as exc:
            return None, [f"Validation error: {exc}"]
