"""Versioned Pydantic models for the Product Agent (D06-C).

These models define the strict structured input and output contracts for
the Product Agent.  Core fields are explicitly typed — no arbitrary
output dictionaries for structured data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Dict, List, Literal, Optional
from uuid import UUID


# ── Schema version ─────────────────────────────────────────────────────────

PRODUCT_SCHEMA_VERSION = "1.0"


# ── Structured submodels ────────────────────────────────────────────────────

class TargetUser(BaseModel):
    """A target user segment for the product."""

    model_config = ConfigDict(extra="forbid")

    segment: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1000)
    priority: Literal["primary", "secondary", "tertiary"] = "primary"


class UserPain(BaseModel):
    """A user pain point or unmet need."""

    model_config = ConfigDict(extra="forbid")

    pain: str = Field(min_length=1, max_length=500)
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    frequency: Literal["daily", "weekly", "monthly", "rarely"] = "weekly"
    evidence: Optional[str] = Field(default=None, max_length=500)


class ProductRequirement(BaseModel):
    """A product requirement."""

    model_config = ConfigDict(extra="forbid")

    requirement: str = Field(min_length=1, max_length=500)
    priority: Literal["must", "should", "could", "wont"] = "must"
    rationale: str = Field(min_length=1, max_length=500)
    acceptance_criteria: Optional[str] = Field(default=None, max_length=500)


class SuccessMetric(BaseModel):
    """A measurable success metric."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=100)
    measurement: str = Field(min_length=1, max_length=200)
    timeframe: Optional[str] = Field(default=None, max_length=100)


class Milestone(BaseModel):
    """A product milestone."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)
    target_date: Optional[str] = Field(default=None, max_length=50)
    deliverables: List[str] = Field(default_factory=list, max_length=20)


class ProductRisk(BaseModel):
    """A product risk."""

    model_config = ConfigDict(extra="forbid")

    risk: str = Field(min_length=1, max_length=500)
    probability: Literal["high", "medium", "low"] = "medium"
    impact: Literal["high", "medium", "low"] = "medium"
    mitigation: Optional[str] = Field(default=None, max_length=500)


class RecommendedAction(BaseModel):
    """A recommended action."""

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, max_length=500)
    priority: Literal["immediate", "short-term", "medium-term", "long-term"] = "short-term"
    rationale: str = Field(min_length=1, max_length=500)
    owner: Optional[str] = Field(default=None, max_length=100)


class DependencyArtifactSummary(BaseModel):
    """Strict summary of a dependency artifact for prompt inclusion.

    Only non-sensitive metadata is included: identity, checksum, and a
    short human-readable summary.  Complete artifact bodies, audit logs,
    secrets, and credentials are never included.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_id: UUID
    checksum: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=500)


# ── Context and request models ──────────────────────────────────────────────

class ProductTaskContext(BaseModel):
    """Structured context for a Product Agent execution."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal["1.0"] = "1.0"
    run_id: UUID
    task_id: UUID
    correlation_id: Optional[str] = Field(default=None, max_length=100)
    objective: str = Field(min_length=1, max_length=2000)
    task_title: str = Field(min_length=1, max_length=500)
    task_description: str = Field(min_length=1, max_length=2000)
    required_deliverable: str = Field(min_length=1, max_length=500)
    founder_context: Optional[str] = Field(default=None, max_length=2000)
    constraints: List[str] = Field(default_factory=list, max_length=20)
    dependency_artifact_ids: List[UUID] = Field(default_factory=list, max_length=50)
    dependency_artifact_checksums: Dict[str, str] = Field(default_factory=dict)
    dependency_artifact_summaries: List[DependencyArtifactSummary] = Field(
        default_factory=list, max_length=20
    )


class ProductAgentRequest(BaseModel):
    """Request to execute the Product Agent."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    context: ProductTaskContext
    virtual_model: Optional[str] = Field(default=None, max_length=100)
    max_repair_attempts: int = Field(default=1, ge=0, le=1)
    include_founder_context: bool = True


# ── Result model ────────────────────────────────────────────────────────────

class ProductAgentResultV1(BaseModel):
    """Structured Product Agent result (schema version 1.0).

    This is the canonical validated representation of the Product Agent output.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    schema_version: Literal["1.0"] = "1.0"
    problem_statement: str = Field(min_length=1, max_length=2000)
    target_users: List[TargetUser] = Field(min_length=1, max_length=20)
    user_pains: List[UserPain] = Field(min_length=1, max_length=50)
    assumptions: List[str] = Field(min_length=1, max_length=30)
    product_scope: str = Field(min_length=1, max_length=2000)
    requirements: List[ProductRequirement] = Field(min_length=1, max_length=50)
    success_metrics: List[SuccessMetric] = Field(min_length=1, max_length=30)
    milestones: List[Milestone] = Field(min_length=1, max_length=30)
    risks: List[ProductRisk] = Field(min_length=1, max_length=30)
    open_questions: List[str] = Field(min_length=1, max_length=30)
    recommended_actions: List[RecommendedAction] = Field(min_length=1, max_length=30)

    @field_validator("assumptions", "open_questions", mode="after")
    @classmethod
    def non_empty_strings(cls, value: List[str]) -> List[str]:
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("Each item must be a non-empty string")
        return value
