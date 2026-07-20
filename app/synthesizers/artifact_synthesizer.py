"""Deterministically merge Product and Finance results into five artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.artifacts import FileArtifactStore
from app.domain.finance_models import BudgetScenario, FinanceAgentResultV1
from app.domain.synthesis_models import (
    ArtifactSynthesisRequest,
    ArtifactSynthesisResult,
    SynthesizedArtifact,
)

if TYPE_CHECKING:
    from app.services.orchestration import OrchestrationService


SYNTHESIZER_ID = "artifact-synthesizer"


class ArtifactSynthesizerError(RuntimeError):
    """Raised when the five-artifact bundle cannot be persisted."""


def _decision_posture(finance: FinanceAgentResultV1) -> str:
    """Return a conservative posture from structured financial evidence."""
    base = _base_scenario(finance)
    unit = finance.unit_economics
    if (
        base.monthly_revenue <= base.monthly_cost
        or unit.contribution_margin_per_unit <= 0
        or unit.contribution_margin_ratio <= 0
        or unit.ltv_cac_ratio < 1
    ):
        return (
            "Do not proceed with launch until the base economics recover; "
            "at least one core viability signal is non-positive or below "
            "break-even."
        )
    if unit.ltv_cac_ratio < 3 or unit.payback_months > 12:
        return (
            "Pause the launch decision and validate unit economics before "
            "committing additional budget."
        )
    return (
        "Threshold review required: core metrics pass initial viability "
        "checks, but no launch authorization is granted until every explicit "
        "financial threshold below is verified."
    )


def _memo(request: ArtifactSynthesisRequest) -> str:
    product = request.product
    finance = request.finance
    primary_user = product.target_users[0]
    primary_action = product.recommended_actions[0]
    threshold = finance.decision_thresholds[0]
    return "\n".join([
        "# Executive Decision Memo",
        "",
        "## Objective",
        request.objective,
        "",
        "## Decision Posture",
        _decision_posture(finance),
        "",
        "## Product Case",
        product.problem_statement,
        f"Primary user: **{primary_user.segment}** — {primary_user.description}",
        "",
        "## Financial Case",
        (
            f"Base scenario revenue/cost: "
            f"{finance.unit_economics.currency} "
            f"{_base_scenario(finance).monthly_revenue:g} / "
            f"{_base_scenario(finance).monthly_cost:g} per month."
        ),
        (
            f"Unit contribution margin ratio: "
            f"{finance.unit_economics.contribution_margin_ratio:.1%}."
        ),
        "",
        "## Proposed Product Action",
        f"{primary_action.action} — {primary_action.rationale}",
        "",
        "## Gate",
        f"{threshold.metric}: proceed if {threshold.proceed_if}; "
        f"pause if {threshold.pause_if}; stop if {threshold.stop_if}.",
        "",
    ])


def _base_scenario(finance: FinanceAgentResultV1) -> BudgetScenario:
    return next(
        scenario
        for scenario in finance.budget_scenarios
        if scenario.name == "base"
    )


def _product_brief(request: ArtifactSynthesisRequest) -> str:
    product = request.product
    lines = [
        "# PRD / Product Brief",
        "",
        "## Problem",
        product.problem_statement,
        "",
        "## Scope",
        product.product_scope,
        "",
        "## Target Users",
    ]
    lines.extend(
        f"- **{user.segment}** ({user.priority}): {user.description}"
        for user in product.target_users
    )
    lines.extend(["", "## Requirements"])
    for requirement in product.requirements:
        lines.append(
            f"- **{requirement.priority.upper()}** "
            f"{requirement.requirement} — {requirement.rationale}"
        )
    lines.extend(["", "## Success Metrics"])
    lines.extend(
        f"- **{metric.metric}**: {metric.target}; {metric.measurement}"
        for metric in product.success_metrics
    )
    lines.extend(["", "## Milestones"])
    lines.extend(
        f"- **{milestone.name}**: {milestone.description}"
        for milestone in product.milestones
    )
    lines.append("")
    return "\n".join(lines)


def _budget_summary(request: ArtifactSynthesisRequest) -> str:
    finance = request.finance
    currency = finance.unit_economics.currency
    lines = ["# Budget Summary", "", "## Scenarios"]
    for scenario in finance.budget_scenarios:
        runway = (
            f"; runway {scenario.runway_months:g} months"
            if scenario.runway_months is not None
            else ""
        )
        break_even = (
            f"; break-even month {scenario.break_even_month}"
            if scenario.break_even_month is not None
            else ""
        )
        lines.append(
            f"- **{scenario.name.title()}**: {currency} "
            f"{scenario.monthly_revenue:g} revenue / "
            f"{scenario.monthly_cost:g} cost per month"
            f"{runway}{break_even}"
        )
    unit = finance.unit_economics
    lines.extend([
        "",
        "## Unit Economics",
        f"- Contribution margin ratio: {unit.contribution_margin_ratio:.1%}",
        f"- LTV/CAC: {unit.ltv_cac_ratio:g}",
        f"- Payback: {unit.payback_months:g} months",
        "",
        "## Decision Thresholds",
    ])
    lines.extend(
        f"- **{item.metric}**: proceed {item.proceed_if}; "
        f"pause {item.pause_if}; stop {item.stop_if}"
        for item in finance.decision_thresholds
    )
    lines.append("")
    return "\n".join(lines)


def _risk_register(request: ArtifactSynthesisRequest) -> str:
    lines = [
        "# Risk Register",
        "",
        "| Source | Risk | Probability | Impact | Mitigation |",
        "|---|---|---|---|---|",
    ]
    for product_risk in request.product.risks:
        lines.append(
            f"| Product | {product_risk.risk} | "
            f"{product_risk.probability} | {product_risk.impact} | "
            f"{product_risk.mitigation or 'Unassigned'} |"
        )
    for financial_risk in request.finance.financial_risks:
        lines.append(
            f"| Finance | {financial_risk.risk} | "
            f"{financial_risk.probability} | {financial_risk.impact} | "
            f"{financial_risk.mitigation} |"
        )
    lines.append("")
    return "\n".join(lines)


def _action_plan(request: ArtifactSynthesisRequest) -> str:
    lines = ["# Action Plan", "", "## Product Actions"]
    for index, action in enumerate(request.product.recommended_actions, start=1):
        owner = action.owner or "Founder"
        lines.append(
            f"{index}. **{action.action}** ({action.priority}, owner: {owner}) — "
            f"{action.rationale}"
        )
    lines.extend(["", "## Financial Gates"])
    for threshold in request.finance.decision_thresholds:
        lines.append(
            f"- Measure **{threshold.metric}** {threshold.measurement_period}; "
            f"proceed if {threshold.proceed_if}; pause if {threshold.pause_if}; "
            f"stop if {threshold.stop_if}."
        )
    lines.append("")
    return "\n".join(lines)


_RENDERERS: tuple[tuple[str, str, Callable[[ArtifactSynthesisRequest], str]], ...] = (
    ("executive-decision-memo", "executive-decision-memo.md", _memo),
    ("prd-product-brief", "prd-product-brief.md", _product_brief),
    ("budget-summary", "budget-summary.md", _budget_summary),
    ("risk-register", "risk-register.md", _risk_register),
    ("action-plan", "action-plan.md", _action_plan),
)


class ArtifactSynthesizer:
    """Write the complete five-artifact bundle through the accepted store."""

    def __init__(
        self,
        artifact_store: FileArtifactStore,
        orchestration_service: OrchestrationService,
    ) -> None:
        self.artifact_store = artifact_store
        self.orchestration = orchestration_service

    def synthesize(
        self,
        request: ArtifactSynthesisRequest,
    ) -> ArtifactSynthesisResult:
        from app.services.artifact_write import ArtifactRegistrationService

        writer = ArtifactRegistrationService(
            self.artifact_store,
            self.orchestration,
        )
        stored_artifacts = {}
        domain_artifacts = {}
        provenance = {
            "synthesizer_id": SYNTHESIZER_ID,
            "product_schema_version": request.product.schema_version,
            "finance_schema_version": request.finance.schema_version,
            "product_artifact_id": (
                str(request.product_artifact_id)
                if request.product_artifact_id
                else None
            ),
            "finance_artifact_id": (
                str(request.finance_artifact_id)
                if request.finance_artifact_id
                else None
            ),
        }
        try:
            for logical_name, filename, renderer in _RENDERERS:
                content = renderer(request)
                checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
                identity = ":".join([
                    SYNTHESIZER_ID,
                    "v1.0",
                    str(request.run_id),
                    str(request.task_id),
                    logical_name,
                    checksum,
                ])
                idempotency_key = hashlib.sha256(
                    identity.encode("utf-8")
                ).hexdigest()
                stored, domain, _ = writer.write_text(
                    run_id=request.run_id,
                    task_id=request.task_id,
                    relation="output",
                    logical_name=logical_name,
                    filename=filename,
                    text=content,
                    content_type="text/markdown; charset=utf-8",
                    created_by=SYNTHESIZER_ID,
                    idempotency_key=idempotency_key,
                    correlation_id=request.correlation_id,
                    provenance=provenance,
                )
                stored_artifacts[logical_name] = SynthesizedArtifact(
                    logical_name=stored.logical_name,
                    filename=stored.filename,
                    uri=stored.uri,
                    checksum_sha256=stored.checksum_sha256,
                    size_bytes=stored.size_bytes,
                )
                domain_artifacts[logical_name] = domain
        except Exception as exc:
            raise ArtifactSynthesizerError(
                f"Artifact synthesis failed: {exc}"
            ) from exc
        return ArtifactSynthesisResult(
            stored_artifacts=stored_artifacts,
            domain_artifacts=domain_artifacts,
        )
