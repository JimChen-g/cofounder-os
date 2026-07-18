"""Canonical domain records for CoFounder OS orchestration.

These models describe product state. API transport models remain in
``app.models`` and should not be used as durable orchestration records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    """Lifecycle state for a complete CoFounder OS run."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    """Lifecycle state for a unit of work inside a run."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageRole(str, Enum):
    """Semantic role of an agent message."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    AGENT = "agent"


class ApprovalStatus(str, Enum):
    """Decision state for a human or policy approval."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ArtifactKind(str, Enum):
    """High-level classification of an artifact."""

    INPUT = "input"
    OUTPUT = "output"
    REPORT = "report"
    CODE = "code"
    DATA = "data"
    LOG = "log"
    CHECKPOINT = "checkpoint"
    OTHER = "other"


class AuditOutcome(str, Enum):
    """Outcome recorded by an audit event."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    PENDING = "pending"


class DomainRecord(BaseModel):
    """Shared identity, versioning, timestamp, and metadata fields."""

    id: UUID = Field(default_factory=uuid4)
    schema_version: Literal["1.0"] = "1.0"
    created_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        extra = "forbid"
        validate_assignment = True
        use_enum_values = True


class Run(DomainRecord):
    """Top-level execution requested by a user or an external system."""

    objective: str = Field(min_length=1)
    status: RunStatus = RunStatus.QUEUED
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    owner: Optional[str] = None
    task_ids: List[UUID] = Field(default_factory=list)
    artifact_ids: List[UUID] = Field(default_factory=list)


class Task(DomainRecord):
    """A dependency-aware unit of work owned by an agent."""

    run_id: UUID
    title: str = Field(min_length=1)
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    assigned_agent: Optional[str] = None
    dependency_ids: List[UUID] = Field(default_factory=list)
    input_artifact_ids: List[UUID] = Field(default_factory=list)
    output_artifact_ids: List[UUID] = Field(default_factory=list)
    approval_id: Optional[UUID] = None
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=2, ge=1)
    last_error: Optional[str] = None
    claimed_by: Optional[str] = None
    claim_token: Optional[str] = None
    claimed_at: Optional[datetime] = None


class AgentMessage(DomainRecord):
    """Message exchanged by users, agents, tools, or the system."""

    run_id: UUID
    task_id: Optional[UUID] = None
    sender: str = Field(min_length=1)
    recipient: Optional[str] = None
    role: MessageRole = MessageRole.AGENT
    content: str = Field(min_length=1)
    correlation_id: Optional[str] = None
    parent_message_id: Optional[UUID] = None


class RouteDecision(DomainRecord):
    """Auditable model and provider selection for a request."""

    run_id: UUID
    task_id: Optional[UUID] = None
    requested_model: Optional[str] = None
    selected_model: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    candidate_models: List[str] = Field(default_factory=list)
    fallback_used: bool = False
    latency_ms: Optional[float] = Field(default=None, ge=0)


class Approval(DomainRecord):
    """Human or policy decision that gates execution."""

    run_id: UUID
    task_id: Optional[UUID] = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    requested_at: datetime = Field(default_factory=utc_now)
    decided_by: Optional[str] = None
    decision_reason: Optional[str] = None
    decided_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class Artifact(DomainRecord):
    """Addressable input, output, report, code, data, or checkpoint."""

    run_id: UUID
    task_id: Optional[UUID] = None
    kind: ArtifactKind
    name: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    content_type: Optional[str] = None
    checksum_sha256: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    created_by: str = Field(min_length=1)


class AuditEvent(DomainRecord):
    """Immutable-style record of a significant system action."""

    run_id: UUID
    task_id: Optional[UUID] = None
    event_type: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    target_id: Optional[str] = None
    outcome: AuditOutcome
    correlation_id: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)
