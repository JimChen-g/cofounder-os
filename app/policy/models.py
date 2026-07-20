"""Strict request and decision models for the D08 Policy Gate."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(str, Enum):
    """Deterministic action risk."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class ToolPermission(str, Enum):
    """Permission class assigned to a tool invocation."""

    READ_ONLY = "read_only"
    GUARDED = "guarded"
    BLOCKED = "blocked"


class PolicyDisposition(str, Enum):
    """Final deterministic policy outcome."""

    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyAction(BaseModel):
    """Normalized action facts; no natural-language policy interpretation."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["1.0"] = "1.0"
    actor: str = Field(min_length=1, max_length=100)
    operation: Literal[
        "read",
        "write",
        "execute",
        "delete",
        "upload",
        "message",
        "configure",
        "transact",
    ]
    tool_name: str = Field(min_length=1, max_length=100)
    target: Optional[str] = Field(default=None, max_length=500)
    command: Optional[str] = Field(default=None, max_length=4000)
    external_write: bool = False
    private_data: bool = False
    production_change: bool = False
    irreversible: bool = False
    material_budget_amount: Optional[float] = Field(default=None, ge=0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    """Complete deterministic explanation of one policy result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    risk_level: RiskLevel
    tool_permission: ToolPermission
    disposition: PolicyDisposition
    approval_required: bool
    reviewer_required: Optional[Literal["founder", "security", "finance"]] = None
    irreversible_action: bool
    rule_ids: List[str] = Field(min_length=1, max_length=20)
    reasons: List[str] = Field(min_length=1, max_length=20)
