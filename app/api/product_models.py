"""Transport models for the D11 Product API.

These models are presentation-layer contracts. Durable workflow state remains
owned by the domain models and orchestration services.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain import Approval, Artifact, AuditEvent, RunStatus
from app.services import WorkflowRunResult


class ProductRequestModel(BaseModel):
    """Strict base model for Product API requests."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateRunRequest(ProductRequestModel):
    """Founder objective submitted for bounded planning and execution."""

    objective: str = Field(min_length=1, max_length=2000)
    context: str | None = Field(default=None, max_length=20_000)
    owner: str | None = Field(default=None, max_length=200)
    max_cycles: int = Field(default=100, ge=1, le=1000)


class RetryRunRequest(ProductRequestModel):
    """Bound for one Workflow Controller recovery/replay request."""

    max_cycles: int = Field(default=100, ge=1, le=1000)


class ResolveApprovalRequest(ProductRequestModel):
    """Human decision for one persisted approval."""

    decision: Literal["approved", "rejected"]
    decided_by: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=2000)
    max_cycles: int = Field(default=100, ge=1, le=1000)


class CreateRunResponse(BaseModel):
    """Plan materialization evidence plus the first controller outcome."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: RunStatus
    plan_message_id: str
    ready_task_ids: list[UUID] = Field(default_factory=list)
    approval_id: UUID | None = None
    workflow: WorkflowRunResult


class ApprovalResponse(BaseModel):
    """Resolved approval and the controller outcome after resolution."""

    model_config = ConfigDict(extra="forbid")

    approval: Approval
    workflow: WorkflowRunResult


class EventListResponse(BaseModel):
    """Bounded audit event collection for one Run."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    count: int = Field(ge=0)
    events: list[AuditEvent] = Field(default_factory=list)


class ArtifactResource(BaseModel):
    """Artifact metadata with optional integrity-checked UTF-8 content."""

    model_config = ConfigDict(extra="forbid")

    artifact: Artifact
    content: str | None = None
    content_available: bool = False
    content_omitted_reason: str | None = None


class ArtifactListResponse(BaseModel):
    """Artifact collection for one Run."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    count: int = Field(ge=0)
    artifacts: list[ArtifactResource] = Field(default_factory=list)


class ProductHealthResponse(BaseModel):
    """Readiness of the Product API's local authorities."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["healthy"]
    version: str
    state_store: Literal["ready"]
    artifact_store: Literal["ready"]
    gateway_boundary: Literal["configured"]


class ProductErrorResponse(BaseModel):
    """Stable error envelope that never exposes internal paths or secrets."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str
    request_id: str | None = None
