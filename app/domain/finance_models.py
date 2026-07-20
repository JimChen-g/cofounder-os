"""Versioned structured contracts for the Finance Agent (D07)."""

from __future__ import annotations

from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.product_models import DependencyArtifactSummary


FINANCE_SCHEMA_VERSION = "1.0"


class RevenueAssumption(BaseModel):
    """One bounded revenue-driver assumption."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    stream: str = Field(min_length=1, max_length=200)
    pricing_model: str = Field(min_length=1, max_length=200)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    unit_price: float = Field(ge=0)
    monthly_units: float = Field(ge=0)
    monthly_growth_rate: float = Field(ge=-1, le=10)
    rationale: str = Field(min_length=1, max_length=500)


class CostItem(BaseModel):
    """One fixed, variable, or one-time cost."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=200)
    category: Literal["fixed", "variable", "one_time"]
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    period: Literal["month", "year", "one_time"]
    rationale: str = Field(min_length=1, max_length=500)


class UnitEconomics(BaseModel):
    """Core unit-economics metrics for the proposed product."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    currency: str = Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
    average_revenue_per_unit: float = Field(ge=0)
    variable_cost_per_unit: float = Field(ge=0)
    contribution_margin_per_unit: float
    contribution_margin_ratio: float = Field(ge=-10, le=1)
    customer_acquisition_cost: float = Field(ge=0)
    lifetime_value: float = Field(ge=0)
    ltv_cac_ratio: float = Field(ge=0)
    payback_months: float = Field(ge=0)


class BudgetScenario(BaseModel):
    """One bounded monthly budget scenario."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: Literal["downside", "base", "upside"]
    monthly_revenue: float = Field(ge=0)
    monthly_cost: float = Field(ge=0)
    runway_months: Optional[float] = Field(default=None, ge=0)
    break_even_month: Optional[int] = Field(default=None, ge=1, le=120)
    assumptions: List[str] = Field(min_length=1, max_length=20)

    @field_validator("assumptions")
    @classmethod
    def non_empty_assumptions(cls, value: List[str]) -> List[str]:
        if any(not item.strip() for item in value):
            raise ValueError("Each scenario assumption must be non-empty")
        return value


class FinancialRisk(BaseModel):
    """One quantified or explicitly unquantified financial risk."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    risk: str = Field(min_length=1, max_length=500)
    probability: Literal["high", "medium", "low"]
    impact: Literal["high", "medium", "low"]
    amount_at_risk: Optional[float] = Field(default=None, ge=0)
    currency: Optional[str] = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    mitigation: str = Field(min_length=1, max_length=500)


class DecisionThreshold(BaseModel):
    """A measurable proceed, pause, and stop threshold."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(min_length=1, max_length=200)
    proceed_if: str = Field(min_length=1, max_length=300)
    pause_if: str = Field(min_length=1, max_length=300)
    stop_if: str = Field(min_length=1, max_length=300)
    measurement_period: str = Field(min_length=1, max_length=100)


class FinanceTaskContext(BaseModel):
    """Structured context for one Finance Agent execution."""

    model_config = ConfigDict(extra="forbid")

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
    dependency_artifact_summaries: List[DependencyArtifactSummary] = Field(
        default_factory=list,
        max_length=20,
    )

    @model_validator(mode="after")
    def validate_dependencies(self) -> "FinanceTaskContext":
        if len(self.dependency_artifact_ids) != len(
            self.dependency_artifact_summaries
        ):
            raise ValueError(
                "dependency_artifact_ids and dependency_artifact_summaries "
                "must have equal length"
            )
        if len(self.dependency_artifact_ids) != len(
            set(self.dependency_artifact_ids)
        ):
            raise ValueError("Duplicate dependency_artifact_ids are not allowed")
        for artifact_id, summary in zip(
            self.dependency_artifact_ids,
            self.dependency_artifact_summaries,
        ):
            if artifact_id != summary.artifact_id:
                raise ValueError(
                    "Dependency summary artifact_id does not match dependency id"
                )
        return self


class FinanceAgentRequest(BaseModel):
    """Request for a bounded Finance Agent invocation."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    context: FinanceTaskContext
    virtual_model: Optional[str] = Field(default=None, max_length=100)
    max_repair_attempts: int = Field(default=1, ge=0, le=1)
    include_founder_context: bool = True


class FinanceAgentResultV1(BaseModel):
    """Canonical Finance Agent output."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal["1.0"] = "1.0"
    revenue_assumptions: List[RevenueAssumption] = Field(
        min_length=1,
        max_length=20,
    )
    cost_structure: List[CostItem] = Field(min_length=1, max_length=50)
    unit_economics: UnitEconomics
    budget_scenarios: List[BudgetScenario] = Field(min_length=3, max_length=3)
    financial_risks: List[FinancialRisk] = Field(min_length=1, max_length=30)
    decision_thresholds: List[DecisionThreshold] = Field(
        min_length=1,
        max_length=20,
    )

    @model_validator(mode="after")
    def complete_budget_scenarios(self) -> "FinanceAgentResultV1":
        names = {scenario.name for scenario in self.budget_scenarios}
        required = {"downside", "base", "upside"}
        if names != required:
            raise ValueError(
                "budget_scenarios must contain exactly downside, base, and upside"
            )
        return self
