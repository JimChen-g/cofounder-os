"""Product Agent implementation for CoFounder OS (D06-C).

The Product Agent produces structured product analysis output through the
existing Gateway boundary.  It does not mutate Task state, claim tasks,
or call providers directly.
"""

from __future__ import annotations

import json
from typing import List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.clients.gateway import GatewayClient, GatewayCompletion
from app.domain import (
    ProductAgentRequest,
    ProductAgentResultV1,
    ProductTaskContext,
)
from app.domain.product_models import PRODUCT_SCHEMA_VERSION
from app.models import ChatMessage, Role


# ── Constants ───────────────────────────────────────────────────────────────

PRODUCT_AGENT_ID = "product-agent"
DEFAULT_VIRTUAL_MODEL = "cofounder-auto"
TEMPERATURE = 0.1
MAX_TOKENS = 4096
REPAIR_MAX_TOKENS = 2048

ALLOWED_VIRTUAL_MODELS = frozenset({
    "cofounder-auto",
    "cofounder-qwen",
    "cofounder-step",
})


# ── Error hierarchy ─────────────────────────────────────────────────────────

class ProductAgentError(RuntimeError):
    """Base error for Product Agent operations."""


class ProductAgentResponseError(ProductAgentError):
    """Raised when the Gateway returns an unusable response."""


class ProductAgentValidationFailure(ProductAgentError):
    """Raised when Product Agent validation fails after repair."""

    def __init__(self, message: str, validation_errors: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.validation_errors = validation_errors or []


# ── Gateway Protocol ────────────────────────────────────────────────────────

class ProductGatewayProtocol(BaseModel):
    """Protocol configuration for Product Agent Gateway calls."""

    model_config = ConfigDict(extra="forbid")

    virtual_model: str = DEFAULT_VIRTUAL_MODEL
    temperature: float = Field(default=TEMPERATURE, ge=0.0, le=2.0)
    max_tokens: int = Field(default=MAX_TOKENS, ge=1, le=128_000)
    response_format: Literal["json_object"] = "json_object"


# ── Prompt construction ─────────────────────────────────────────────────────

def _build_system_prompt(context: ProductTaskContext) -> str:
    """Build the system prompt for the Product Agent."""
    lines = [
        f"You are the CoFounder OS Product Agent (schema {PRODUCT_SCHEMA_VERSION}).",
        "",
        "## Objective",
        context.objective,
        "",
        "## Task",
        f"Title: {context.task_title}",
        f"Description: {context.task_description}",
        f"Required deliverable: {context.required_deliverable}",
        "",
    ]

    if context.founder_context:
        lines.extend([
            "## Founder Context",
            context.founder_context,
            "",
        ])

    if context.constraints:
        lines.extend([
            "## Constraints",
            *[f"- {c}" for c in context.constraints],
            "",
        ])

    if context.dependency_artifact_ids:
        lines.extend([
            "## Dependency Artifacts",
        ])
        for dep_id, dep_summary in zip(context.dependency_artifact_ids, context.dependency_artifact_summaries):
            lines.extend([
                f"- {dep_id} (checksum: {dep_summary.checksum}): {dep_summary.summary}",
            ])
        lines.append("")

    lines.extend([
        "## Output Format",
        "Respond with ONLY a valid JSON object matching this exact schema:",
        "",
        "{",
        '  "schema_version": "1.0",',
        '  "problem_statement": "<string, max 2000 chars>",',
        '  "target_users": [',
        "    {",
        '      "segment": "<string, max 200 chars>",',
        '      "description": "<string, max 1000 chars>",',
        '      "priority": "primary|secondary|tertiary"',
        "    }",
        "  ],",
        '  "user_pains": [',
        "    {",
        '      "pain": "<string, max 500 chars>",',
        '      "severity": "critical|high|medium|low",',
        '      "frequency": "daily|weekly|monthly|rarely",',
        '      "evidence": "<optional string, max 500 chars>"',
        "    }",
        "  ],",
        '  "assumptions": ["<string>", ...],',
        '  "product_scope": "<string, max 2000 chars>",',
        '  "requirements": [',
        "    {",
        '      "requirement": "<string, max 500 chars>",',
        '      "priority": "must|should|could|wont",',
        '      "rationale": "<string, max 500 chars>",',
        '      "acceptance_criteria": "<optional string, max 500 chars>"',
        "    }",
        "  ],",
        '  "success_metrics": [',
        "    {",
        '      "metric": "<string, max 200 chars>",',
        '      "target": "<string, max 100 chars>",',
        '      "measurement": "<string, max 200 chars>",',
        '      "timeframe": "<optional string, max 100 chars>"',
        "    }",
        "  ],",
        '  "milestones": [',
        "    {",
        '      "name": "<string, max 200 chars>",',
        '      "description": "<string, max 500 chars>",',
        '      "target_date": "<optional string, max 50 chars>",',
        '      "deliverables": ["<string>", ...]',
        "    }",
        "  ],",
        '  "risks": [',
        "    {",
        '      "risk": "<string, max 500 chars>",',
        '      "probability": "high|medium|low",',
        '      "impact": "high|medium|low",',
        '      "mitigation": "<optional string, max 500 chars>"',
        "    }",
        "  ],",
        '  "open_questions": ["<string>", ...],',
        '  "recommended_actions": [',
        "    {",
        '      "action": "<string, max 500 chars>",',
        '      "priority": "immediate|short-term|medium-term|long-term",',
        '      "rationale": "<string, max 500 chars>",',
        '      "owner": "<optional string, max 100 chars>"',
        "    }",
        "  ]",
        "}",
        "",
        "Rules:",
        "- Respond with ONLY the JSON object, no markdown fences, no commentary.",
        "- All string fields are required unless marked optional.",
        "- Lists must contain at least one item.",
        "- Do not include fields not in this schema.",
        "- Do not use NaN or Infinity.",
    ])

    return "\n".join(lines)


def _build_repair_prompt(
    original_response: str,
    validation_errors: List[str],
) -> str:
    """Build a repair prompt for invalid responses."""
    lines = [
        "Your previous response was invalid. Correct it.",
        "",
        "## Previous Response",
        original_response,
        "",
        "## Validation Errors",
        *[f"- {e}" for e in validation_errors],
        "",
        "## Correction Rules",
        "- Respond with ONLY a valid JSON object matching the ProductAgentResultV1 schema.",
        "- Do not include markdown fences.",
        "- Do not repeat the errors.",
        "- Ensure all required fields are present and correctly typed.",
    ]
    return "\n".join(lines)


def _build_user_message(context: ProductTaskContext) -> str:
    """Build the user message for the Product Agent."""
    lines = [
        f"Analyze the product opportunity for: {context.task_title}",
        "",
        f"Objective: {context.objective}",
        f"Task: {context.task_description}",
        f"Deliverable: {context.required_deliverable}",
    ]

    if context.founder_context:
        lines.extend([
            "",
            "Founder context:",
            context.founder_context,
        ])

    if context.constraints:
        lines.extend([
            "",
            "Constraints:",
            *[f"- {c}" for c in context.constraints],
        ])

    if context.dependency_artifact_ids:
        lines.extend([
            "",
            "Consider these dependency artifacts:",
        ])
        for dep_id, dep_summary in zip(context.dependency_artifact_ids, context.dependency_artifact_summaries):
            lines.extend([
                f"- {dep_id} (checksum: {dep_summary.checksum}): {dep_summary.summary}",
            ])

    return "\n".join(lines)


# ── Product Agent ───────────────────────────────────────────────────────────

class ProductAgent:
    """Structured Product Agent that produces validated product analysis.

    The agent calls the Gateway with JSON-only output, validates the response
    against ProductAgentResultV1, and allows exactly one repair attempt on
    validation failure.
    """

    def __init__(
        self,
        gateway_client: GatewayClient,
        protocol: Optional[ProductGatewayProtocol] = None,
    ) -> None:
        self.gateway = gateway_client
        self.protocol = protocol or ProductGatewayProtocol()

        if self.protocol.virtual_model not in ALLOWED_VIRTUAL_MODELS:
            raise ValueError(
                f"Virtual model {self.protocol.virtual_model!r} is not allowed. "
                f"Allowed: {sorted(ALLOWED_VIRTUAL_MODELS)}"
            )

    async def execute(
        self,
        request: ProductAgentRequest,
    ) -> tuple[ProductAgentResultV1, GatewayCompletion]:
        """Execute the Product Agent and return validated result.

        Returns (result, gateway_completion).

        Raises ProductAgentValidationFailure if validation fails after repair.
        """
        context = request.context
        system_prompt = _build_system_prompt(context)
        user_message = _build_user_message(context)

        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt),
            ChatMessage(role=Role.USER, content=user_message),
        ]

        # First attempt
        completion = await self._call_gateway(messages, request.virtual_model)
        result, validation_errors = self._parse_and_validate(completion.content)

        if result is not None:
            return result, completion

        # Repair attempt (if allowed)
        if request.max_repair_attempts > 0 and validation_errors:
            repair_prompt = _build_repair_prompt(completion.content, validation_errors)
            repair_messages = [
                ChatMessage(role=Role.SYSTEM, content=system_prompt),
                ChatMessage(role=Role.USER, content=user_message),
                ChatMessage(role=Role.ASSISTANT, content=completion.content),
                ChatMessage(role=Role.USER, content=repair_prompt),
            ]

            repair_completion = await self._call_gateway(
                repair_messages, request.virtual_model
            )
            result, repair_errors = self._parse_and_validate(repair_completion.content)

            if result is not None:
                return result, repair_completion

            raise ProductAgentValidationFailure(
                "Product Agent validation failed after repair",
                validation_errors=repair_errors,
            )

        raise ProductAgentValidationFailure(
            "Product Agent validation failed",
            validation_errors=validation_errors,
        )

    async def _call_gateway(
        self,
        messages: Sequence[ChatMessage],
        virtual_model: Optional[str] = None,
    ) -> GatewayCompletion:
        """Call the Gateway with Product Agent parameters."""
        model = virtual_model or self.protocol.virtual_model
        if model not in ALLOWED_VIRTUAL_MODELS:
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
    ) -> tuple[Optional[ProductAgentResultV1], List[str]]:
        """Parse JSON content and validate against ProductAgentResultV1.

        Returns (result, errors).  If result is None, errors contains
        validation failure descriptions.
        """
        errors: List[str] = []

        # Step 1: Parse JSON
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"Invalid JSON: {exc}")
            return None, errors

        if not isinstance(data, dict):
            errors.append("Response must be a JSON object")
            return None, errors

        # Step 2: Check for unknown fields
        allowed_fields = set(ProductAgentResultV1.model_fields.keys())
        unknown = set(data.keys()) - allowed_fields
        if unknown:
            errors.append(f"Unknown fields: {sorted(unknown)}")

        # Step 3: Validate schema version
        schema_version = data.get("schema_version")
        if schema_version != "1.0":
            errors.append(f"Unsupported schema_version: {schema_version!r}")

        # Step 4: Validate required fields
        required_fields = [
            "problem_statement",
            "target_users",
            "user_pains",
            "assumptions",
            "product_scope",
            "requirements",
            "success_metrics",
            "milestones",
            "risks",
            "open_questions",
            "recommended_actions",
        ]
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        if errors:
            return None, errors

        # Step 5: Validate with Pydantic
        try:
            result = ProductAgentResultV1(**data)
            return result, []
        except Exception as exc:
            errors.append(f"Validation error: {exc}")
            return None, errors
