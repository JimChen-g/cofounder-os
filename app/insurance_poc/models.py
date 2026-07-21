"""Versioned multimodal Evidence Package contracts for the insurance POC."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain import utc_now
from app.services.orchestration import RunSnapshot


class StrictModel(BaseModel):
    """Reject unknown input at the product boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceCategory(str, Enum):
    BUSINESS = "business"
    ACCIDENT = "accident"
    TECHNICAL = "technical"
    FINANCIAL = "financial"
    COMPLIANCE_CONSTRAINT = "compliance/constraint"


class SourceModality(str, Enum):
    TEXT = "text"
    DOCUMENT = "document"
    IMAGE = "image"
    STRUCTURED_DATA = "structured_data"


class PrivacyLevel(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


class ProcessingStatus(str, Enum):
    COMPLETE = "complete"
    FAILED_RECOVERABLE = "failed_recoverable"


class AttachmentUpload(StrictModel):
    """One browser-read file encoded without multipart infrastructure."""

    filename: str = Field(min_length=1, max_length=200)
    content_type: Literal["application/pdf", "image/png"]
    base64_content: str = Field(min_length=4, max_length=12_000_000)
    privacy_level: PrivacyLevel = PrivacyLevel.RESTRICTED

    @field_validator("filename")
    @classmethod
    def filename_must_be_leaf(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ValueError("filename must be a plain leaf filename")
        return value


class EvidenceSource(StrictModel):
    """Processing and integrity evidence for one submitted input."""

    source_file: str
    source_type: str
    modality: SourceModality
    content_type: str
    checksum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)
    privacy_level: PrivacyLevel
    processing_status: ProcessingStatus
    adapter: str
    adapter_mode: Literal["live", "deterministic_fixture", "local_parser"]
    recoverable_error: str | None = None


class EvidenceItem(StrictModel):
    """One source-linked fact consumed by specialist Agents."""

    evidence_id: str = Field(pattern=r"^E-[A-Z0-9-]+$")
    category: EvidenceCategory
    content: str = Field(min_length=1, max_length=4000)
    source_file: str
    source_type: str
    modality: SourceModality
    confidence: float = Field(ge=0, le=1)
    privacy_level: PrivacyLevel
    used_by_agents: list[str] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    processing_status: ProcessingStatus = ProcessingStatus.COMPLETE
    adapter: str
    adapter_mode: Literal["live", "deterministic_fixture", "local_parser"]
    source_checksum_sha256: str | None = Field(
        default=None,
        pattern=r"^[a-f0-9]{64}$",
    )


class EvidencePackage(StrictModel):
    """Normalized state shared by the golden workflow."""

    schema_version: Literal["insurance-evidence-1.0"] = "insurance-evidence-1.0"
    package_id: UUID = Field(default_factory=uuid4)
    scenario_id: str = Field(min_length=1)
    mission: str = Field(min_length=1, max_length=2000)
    synthetic: bool
    authoritative: bool
    created_at: datetime = Field(default_factory=utc_now)
    sources: list[EvidenceSource] = Field(min_length=4)
    evidence: list[EvidenceItem] = Field(min_length=5)
    constraints: list[str] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class EvidencePreviewRequest(StrictModel):
    mission: str = Field(min_length=1, max_length=2000)
    attachments: list[AttachmentUpload] = Field(min_length=3, max_length=8)


class EvidencePreviewResponse(StrictModel):
    evidence_package: EvidencePackage


class RoutingPreviewRequest(StrictModel):
    evidence_package: EvidencePackage
    unavailable_models: list[str] = Field(default_factory=list, max_length=8)


class ExplainableRouteDecision(StrictModel):
    schema_version: Literal["insurance-route-1.0"] = "insurance-route-1.0"
    task_key: str
    task_title: str
    requested_model: str
    selected_model: str
    provider: str
    reason: str
    candidate_models: list[str]
    excluded_models: dict[str, str] = Field(default_factory=dict)
    required_capabilities: list[str]
    input_modalities: list[SourceModality]
    privacy_level: PrivacyLevel
    complexity: Literal["low", "medium", "high"]
    context_length: int = Field(ge=0)
    tool_requirement: str
    latency_budget_ms: float = Field(ge=0)
    cost_budget_usd: float = Field(ge=0)
    estimated_latency_ms: float = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    privacy_decision: str
    fallback_model: str
    fallback_used: bool
    validation_required: bool
    validation_requirement: str
    execution_status: Literal["decision_only"] = "decision_only"


class RoutingPreviewResponse(StrictModel):
    schema_version: Literal["insurance-routing-plan-1.0"] = "insurance-routing-plan-1.0"
    package_id: UUID
    decisions: list[ExplainableRouteDecision] = Field(min_length=3)
    live_model_calls: int = 0
    simulation_disclosure: str


class GoldenWorkflowRequest(EvidencePreviewRequest):
    """Create the frozen golden workflow from validated demo evidence."""

    owner: str = Field(default="Founder", min_length=1, max_length=200)
    unavailable_models: list[str] = Field(default_factory=list, max_length=8)


class ConflictRecord(StrictModel):
    """One structured, evidence-backed cross-Agent disagreement."""

    conflict_id: str
    conflict_type: Literal["scope_budget", "authority_boundary"]
    raised_by: str
    affected_agents: list[str] = Field(min_length=2)
    source_evidence: list[str] = Field(min_length=1)
    proposal_before: dict[str, object]
    constraint: dict[str, object]
    proposal_after: dict[str, object]
    resolution_rule: str
    resolution_status: Literal["resolved"] = "resolved"
    accepted_by: list[str] = Field(min_length=1)


class GoldenWorkflowResponse(StrictModel):
    """Persisted result of the deterministic golden-demo execution."""

    run_id: UUID
    status: str
    approval_id: UUID
    snapshot: RunSnapshot
    evidence_package: EvidencePackage
    routing_plan: RoutingPreviewResponse
    conflicts: list[ConflictRecord] = Field(min_length=2)
    execution_disclosure: str


class DemoEvaluationMetrics(StrictModel):
    """Aggregate metrics for one executable demo strategy."""

    task_completion_rate: float = Field(ge=0, le=1)
    routing_accuracy: float = Field(ge=0, le=1)
    local_model_share: float = Field(ge=0, le=1)
    tool_success_rate: float = Field(ge=0, le=1)
    verifier_correction_count: int = Field(ge=0)
    human_intervention_count: int = Field(ge=0)
    average_latency_ms: float = Field(ge=0)
    measured_harness_latency_ms: float = Field(ge=0)
    estimated_cloud_api_cost_usd: float = Field(ge=0)


class DemoEvaluationResponse(StrictModel):
    """Small, explicitly non-statistical baseline comparison."""

    schema_version: Literal["insurance-demo-evaluation-1.0"]
    label: Literal["demo evaluation"]
    generated_at: datetime
    sample_size: int = Field(ge=5, le=8)
    disclosure: str
    metric_sources: dict[str, str]
    baseline: DemoEvaluationMetrics
    cofounder_os: DemoEvaluationMetrics
    deltas: dict[str, float]
    sample_results: list[dict[str, object]] = Field(min_length=5, max_length=8)


class FixtureResponse(StrictModel):
    scenario_id: str
    mission: str
    attachments: list[AttachmentUpload]
