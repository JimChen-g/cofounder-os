"""Gateway-backed specialist Agents for the D15 insurance POC.

The Agents are advisory: Engineering may plan but cannot claim repository
execution, and Risk may recommend controls but cannot replace the Policy Gate.
"""

from __future__ import annotations

import json
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from app.clients import GatewayClient, GatewayCompletion
from app.models import ChatMessage, Role

from .models import EvidencePackage, StrictModel


ALLOWED_LIVE_MODELS = frozenset({"cofounder-qwen", "cofounder-step"})
SAFE_DECISION_MODE = "model_recommendation_plus_human_review"


class LiveAgentValidationFailure(RuntimeError):
    """The model failed its strict output contract after bounded repair."""

    def __init__(
        self,
        agent_id: str,
        errors: list[str],
        call_evidence: "LiveAgentCallEvidence | None" = None,
    ) -> None:
        super().__init__(f"{agent_id} output failed validation: {'; '.join(errors)}")
        self.agent_id = agent_id
        self.errors = errors
        self.call_evidence = call_evidence


class LiveAgentCallEvidence(StrictModel):
    """Minimum evidence required before D15 may claim a live model call."""

    requested_virtual_model: Literal["cofounder-qwen", "cofounder-step"]
    selected_provider: str = Field(min_length=1)
    selected_upstream_model: str = Field(min_length=1)
    routing_reason: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    fallback_used: bool
    latency_ms: float = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    call_count: int = Field(default=1, ge=1, le=2)
    repair_performed: bool = False

    @classmethod
    def from_completion(cls, completion: GatewayCompletion) -> "LiveAgentCallEvidence":
        missing = [
            name
            for name, value in {
                "selected_provider": completion.selected_provider,
                "selected_upstream_model": completion.selected_model,
                "routing_reason": completion.routing_reason,
                "request_id": completion.request_id,
            }.items()
            if not value
        ]
        raw_latency = completion.raw_metadata.get("latency_ms")
        if raw_latency is None:
            missing.append("latency_ms")
        usage = completion.usage
        for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if not isinstance(usage.get(name), int):
                missing.append(name)
        if missing:
            raise ValueError(
                "Gateway completion is missing live-call evidence: "
                + ", ".join(sorted(set(missing)))
            )
        if completion.requested_model not in ALLOWED_LIVE_MODELS:
            raise ValueError("A D15 live Agent requires an explicit live virtual model")
        if raw_latency is None:
            raise ValueError("Gateway completion is missing live-call latency")
        requested_model = cast(
            Literal["cofounder-qwen", "cofounder-step"],
            completion.requested_model,
        )
        selected_provider = str(completion.selected_provider)
        requested_provider = (
            "qwen" if requested_model == "cofounder-qwen" else "step"
        )
        return cls(
            requested_virtual_model=requested_model,
            selected_provider=selected_provider,
            selected_upstream_model=str(completion.selected_model),
            routing_reason=str(completion.routing_reason),
            request_id=str(completion.request_id),
            fallback_used=(
                completion.fallback_used or selected_provider != requested_provider
            ),
            latency_ms=float(raw_latency),
            prompt_tokens=int(usage["prompt_tokens"]),
            completion_tokens=int(usage["completion_tokens"]),
            total_tokens=int(usage["total_tokens"]),
        )


class EngineeringWorkstream(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    deliverable: str = Field(min_length=1, max_length=500)
    days: list[int] = Field(min_length=1, max_length=10)
    dependencies: list[str] = Field(default_factory=list, max_length=10)
    acceptance_check: str = Field(min_length=1, max_length=500)


class EngineeringPlanningResult(StrictModel):
    schema_version: Literal["insurance-engineering-llm-1.0"]
    plan_summary: str = Field(min_length=1, max_length=1200)
    workstreams: list[EngineeringWorkstream] = Field(min_length=2, max_length=8)
    two_week_sequence: list[str] = Field(min_length=3, max_length=12)
    reused_capabilities: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=20)
    validation_checks: list[str] = Field(min_length=2, max_length=20)
    source_evidence: list[str] = Field(min_length=1, max_length=50)
    execution_status: Literal["plan_only"]
    code_diff: None = None
    test_result: None = None

    @model_validator(mode="after")
    def days_stay_inside_two_week_window(self) -> "EngineeringPlanningResult":
        if any(day < 1 or day > 10 for item in self.workstreams for day in item.days):
            raise ValueError("Engineering workstream days must be in the 1..10 window")
        return self


class RiskFinding(StrictModel):
    risk_id: str = Field(pattern=r"^R-[A-Z0-9-]+$")
    category: Literal["authority", "privacy", "evidence_quality", "delivery"]
    severity: Literal["low", "medium", "high", "critical"]
    finding: str = Field(min_length=1, max_length=700)
    control: str = Field(min_length=1, max_length=700)
    source_evidence: list[str] = Field(min_length=1, max_length=20)


class RiskReviewResult(StrictModel):
    schema_version: Literal["insurance-risk-llm-1.0"]
    review_summary: str = Field(min_length=1, max_length=1200)
    findings: list[RiskFinding] = Field(min_length=2, max_length=12)
    recommended_decision_mode: Literal[
        "autonomous_liability_decision",
        "model_recommendation_plus_human_review",
    ]
    private_upload_allowed: bool
    required_human_approval: bool
    required_controls: list[str] = Field(min_length=2, max_length=20)
    source_evidence: list[str] = Field(min_length=1, max_length=50)


def _engineering_schema_example() -> str:
    return json.dumps(
        {
            "schema_version": "insurance-engineering-llm-1.0",
            "plan_summary": "bounded summary",
            "workstreams": [
                {
                    "name": "workstream one",
                    "deliverable": "bounded deliverable",
                    "days": [1, 2],
                    "dependencies": [],
                    "acceptance_check": "measurable check",
                },
                {
                    "name": "workstream two",
                    "deliverable": "bounded deliverable",
                    "days": [3, 4],
                    "dependencies": ["workstream one"],
                    "acceptance_check": "measurable check",
                },
            ],
            "two_week_sequence": ["Days 1-2", "Days 3-6", "Days 7-10"],
            "reused_capabilities": ["verified capability"],
            "limitations": ["planning only"],
            "validation_checks": ["check one", "check two"],
            "source_evidence": ["E-SUPPLIED-ID"],
            "execution_status": "plan_only",
            "code_diff": None,
            "test_result": None,
        },
        separators=(",", ":"),
    )


def _risk_schema_example() -> str:
    return json.dumps(
        {
            "schema_version": "insurance-risk-llm-1.0",
            "review_summary": "bounded summary",
            "findings": [
                {
                    "risk_id": "R-AUTHORITY-001",
                    "category": "authority",
                    "severity": "high",
                    "finding": "bounded finding",
                    "control": "bounded control",
                    "source_evidence": ["E-SUPPLIED-ID"],
                },
                {
                    "risk_id": "R-PRIVACY-001",
                    "category": "privacy",
                    "severity": "high",
                    "finding": "bounded finding",
                    "control": "bounded control",
                    "source_evidence": ["E-SUPPLIED-ID"],
                },
            ],
            "recommended_decision_mode": SAFE_DECISION_MODE,
            "private_upload_allowed": False,
            "required_human_approval": True,
            "required_controls": ["Policy Gate", "Founder approval"],
            "source_evidence": ["E-SUPPLIED-ID"],
        },
        separators=(",", ":"),
    )


class _LiveSpecialistAgent:
    agent_id: str
    result_type: type[EngineeringPlanningResult] | type[RiskReviewResult]

    def __init__(self, gateway: GatewayClient) -> None:
        self.gateway = gateway

    async def _execute(
        self,
        *,
        virtual_model: str,
        system_prompt: str,
        context: dict[str, Any],
        allowed_evidence_ids: set[str],
        max_tokens: int = 4096,
    ) -> tuple[
        EngineeringPlanningResult | RiskReviewResult,
        LiveAgentCallEvidence,
    ]:
        if virtual_model not in ALLOWED_LIVE_MODELS:
            raise ValueError(f"Unsupported D15 live model: {virtual_model}")
        messages = [
            ChatMessage(role=Role.SYSTEM, content=system_prompt),
            ChatMessage(
                role=Role.USER,
                content=(
                    "/no_think\nUse only this persisted, checksum-verified context. "
                    "Return JSON only.\n"
                    + json.dumps(context, ensure_ascii=False, sort_keys=True)
                ),
            ),
        ]
        completion = await self.gateway.complete(
            messages,
            model=virtual_model,
            temperature=0.1,
            max_tokens=max_tokens,
        )
        call = LiveAgentCallEvidence.from_completion(completion)
        result, errors = self._validate(completion.content, allowed_evidence_ids)
        repair_performed = False
        if result is None:
            repair_performed = True
            repaired = await self.gateway.complete(
                [
                    *messages,
                    ChatMessage(role=Role.ASSISTANT, content=completion.content),
                    ChatMessage(
                        role=Role.USER,
                        content=(
                            "/no_think\nRepair the response. Return one strict RFC 8259 JSON "
                            "object only. "
                            "Use double quotes for every key and string; do not use Markdown, "
                            "comments, trailing commas, or unescaped quotes inside strings. "
                            "Validation errors:\n- " + "\n- ".join(errors)
                        ),
                    ),
                ],
                model=virtual_model,
                temperature=0,
                max_tokens=max_tokens,
            )
            call = LiveAgentCallEvidence.from_completion(repaired).model_copy(
                update={"call_count": 2, "repair_performed": True}
            )
            result, errors = self._validate(repaired.content, allowed_evidence_ids)
            completion = repaired
        if result is None:
            raise LiveAgentValidationFailure(self.agent_id, errors, call)
        call = call.model_copy(
            update={
                "call_count": 2 if repair_performed else 1,
                "repair_performed": repair_performed,
            }
        )
        return result, call

    def _validate(
        self,
        content: str,
        allowed_evidence_ids: set[str],
    ) -> tuple[EngineeringPlanningResult | RiskReviewResult | None, list[str]]:
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
        try:
            raw = json.loads(candidate)
        except (json.JSONDecodeError, ValueError) as exc:
            return None, [f"Invalid JSON: {exc}"]
        if not isinstance(raw, dict):
            return None, ["Response must be a JSON object"]
        try:
            result = self.result_type.model_validate(raw)
        except Exception as exc:
            return None, [f"Schema validation failed: {exc}"]
        citations = set(result.source_evidence)
        if isinstance(result, RiskReviewResult):
            citations.update(
                evidence_id
                for finding in result.findings
                for evidence_id in finding.source_evidence
            )
        unknown = citations - allowed_evidence_ids
        if unknown:
            return None, [f"Unknown Evidence IDs: {sorted(unknown)}"]
        return result, []


class EngineeringPlanningAgent(_LiveSpecialistAgent):
    agent_id = "engineering-agent"
    result_type = EngineeringPlanningResult

    async def execute(
        self,
        *,
        virtual_model: str,
        evidence: EvidencePackage,
        product: dict[str, Any],
        finance: dict[str, Any],
        project_status: dict[str, Any],
    ) -> tuple[EngineeringPlanningResult, LiveAgentCallEvidence]:
        result, call = await self._execute(
            virtual_model=virtual_model,
            system_prompt=(
                "You are the CoFounder OS Engineering Planning Agent. Produce a bounded "
                "ten-business-day implementation plan. Never claim code changes, tests, "
                "deployment, or external writes. Cite only supplied Evidence IDs. Return "
                "strict RFC 8259 JSON with double-quoted keys and strings, using this exact "
                f"shape: {_engineering_schema_example()}"
            ),
            context={
                "mission": evidence.mission,
                "constraints": evidence.constraints,
                "evidence": [
                    {"evidence_id": item.evidence_id, "content": item.content}
                    for item in evidence.evidence
                    if "engineering-agent" in item.used_by_agents
                ],
                "product_proposal": product,
                "finance_review": finance,
                "project_status": project_status,
            },
            allowed_evidence_ids={item.evidence_id for item in evidence.evidence},
            max_tokens=8192,
        )
        if not isinstance(result, EngineeringPlanningResult):
            raise TypeError("Engineering Agent returned the wrong result contract")
        return result, call


class RiskReviewAgent(_LiveSpecialistAgent):
    agent_id = "risk-agent"
    result_type = RiskReviewResult

    async def execute(
        self,
        *,
        virtual_model: str,
        evidence: EvidencePackage,
        product: dict[str, Any],
        finance: dict[str, Any],
    ) -> tuple[RiskReviewResult, LiveAgentCallEvidence]:
        result, call = await self._execute(
            virtual_model=virtual_model,
            system_prompt=(
                "You are the CoFounder OS Risk Review Agent. Review authority, privacy, "
                "evidence quality, and delivery risk. Your advice is non-authoritative and "
                "cannot replace the deterministic Policy Gate or Founder approval. Cite only "
                "supplied Evidence IDs. Return strict RFC 8259 JSON with double-quoted keys "
                f"and strings, using this exact shape: {_risk_schema_example()}"
            ),
            context={
                "mission": evidence.mission,
                "authoritative": evidence.authoritative,
                "constraints": evidence.constraints,
                "evidence": [
                    {"evidence_id": item.evidence_id, "content": item.content}
                    for item in evidence.evidence
                    if "risk-agent" in item.used_by_agents
                ],
                "product_proposal": product,
                "finance_review": finance,
            },
            allowed_evidence_ids={item.evidence_id for item in evidence.evidence},
        )
        if not isinstance(result, RiskReviewResult):
            raise TypeError("Risk Agent returned the wrong result contract")
        return result, call
