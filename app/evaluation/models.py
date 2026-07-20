"""Strict presentation contracts for deterministic Run evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


EvaluationGrade = Literal["excellent", "good", "attention", "critical"]
DimensionStatus = Literal["pass", "attention", "fail"]
DimensionKey = Literal[
    "workflow",
    "execution",
    "artifacts",
    "governance",
    "auditability",
]


class EvaluationDimension(BaseModel):
    """One explainable, weighted part of a Run score."""

    model_config = ConfigDict(extra="forbid")

    key: DimensionKey
    label: str
    score: float = Field(ge=0, le=100)
    weight: float = Field(gt=0, le=1)
    status: DimensionStatus
    evidence: list[str] = Field(default_factory=list)


class AgentPerformance(BaseModel):
    """Aggregated deterministic execution metrics for one agent."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    tasks: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    retries: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=100)
    average_attempts: float = Field(ge=0)


class RunEvaluation(BaseModel):
    """Read-only score and evidence for one persisted Run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    objective: str
    owner: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime
    duration_seconds: float | None = Field(default=None, ge=0)
    overall_score: float = Field(ge=0, le=100)
    grade: EvaluationGrade
    task_count: int = Field(ge=0)
    completed_tasks: int = Field(ge=0)
    failed_tasks: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    required_artifact_count: int = Field(ge=0)
    verified_artifact_count: int = Field(ge=0)
    pending_approval_count: int = Field(ge=0)
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    dimensions: list[EvaluationDimension] = Field(default_factory=list)
    agent_performance: list[AgentPerformance] = Field(default_factory=list)


class EvaluationSummary(BaseModel):
    """Cross-run dashboard metrics computed from authoritative records."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    generated_at: datetime
    run_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=100)
    average_score: float = Field(ge=0, le=100)
    task_success_rate: float = Field(ge=0, le=100)
    artifact_integrity_rate: float = Field(ge=0, le=100)
    total_retries: int = Field(ge=0)
    status_distribution: dict[str, int] = Field(default_factory=dict)
    grade_distribution: dict[str, int] = Field(default_factory=dict)
    provider_distribution: dict[str, int] = Field(default_factory=dict)
    agent_performance: list[AgentPerformance] = Field(default_factory=list)
    recent_runs: list[RunEvaluation] = Field(default_factory=list)
