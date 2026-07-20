"""Finance Agent application service and artifact production (D07)."""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from app.agents.finance_agent import (
    FINANCE_AGENT_ID,
    FinanceAgent,
    FinanceAgentValidationFailure,
    FinanceGatewayProtocol,
)
from app.artifacts import FileArtifactStore, StoredArtifact
from app.clients.gateway import GatewayClient, GatewayCompletion
from app.domain.finance_models import (
    FINANCE_SCHEMA_VERSION,
    FinanceAgentRequest,
    FinanceAgentResultV1,
    FinanceTaskContext,
)
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService


class FinanceAgentServiceError(RuntimeError):
    """Base Finance Agent service error."""


class FinanceAgentExecutionError(FinanceAgentServiceError):
    """Raised when execution, evidence recording, or persistence fails."""


class FinanceAgentRouteEvidenceError(FinanceAgentServiceError):
    """Raised when actual Gateway routing evidence is incomplete."""


def _idempotency_key(
    result: FinanceAgentResultV1,
    context: FinanceTaskContext,
    logical_name: str,
    filename: str,
    checksum: str,
) -> str:
    identity = ":".join([
        FINANCE_AGENT_ID,
        f"v{result.schema_version}",
        str(context.run_id),
        str(context.task_id),
        "output",
        logical_name,
        filename,
        checksum,
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _render_markdown(result: FinanceAgentResultV1) -> str:
    lines = ["# Finance Brief", "", "## Revenue Assumptions"]
    for revenue in result.revenue_assumptions:
        lines.append(
            f"- **{revenue.stream}**: {revenue.currency} "
            f"{revenue.unit_price:g} × {revenue.monthly_units:g}/month; "
            f"growth {revenue.monthly_growth_rate:.1%}. {revenue.rationale}"
        )
    lines.extend(["", "## Cost Structure"])
    for cost in result.cost_structure:
        lines.append(
            f"- **{cost.name}** ({cost.category}): {cost.currency} "
            f"{cost.amount:g}/{cost.period}. {cost.rationale}"
        )
    unit = result.unit_economics
    lines.extend([
        "",
        "## Unit Economics",
        f"- Average revenue per unit: {unit.currency} {unit.average_revenue_per_unit:g}",
        f"- Variable cost per unit: {unit.currency} {unit.variable_cost_per_unit:g}",
        f"- Contribution margin: {unit.currency} {unit.contribution_margin_per_unit:g}",
        f"- Contribution margin ratio: {unit.contribution_margin_ratio:.1%}",
        f"- CAC: {unit.currency} {unit.customer_acquisition_cost:g}",
        f"- LTV: {unit.currency} {unit.lifetime_value:g}",
        f"- LTV/CAC: {unit.ltv_cac_ratio:g}",
        f"- Payback: {unit.payback_months:g} months",
        "",
        "## Budget Scenarios",
    ])
    for scenario in result.budget_scenarios:
        lines.append(
            f"- **{scenario.name.title()}**: revenue "
            f"{unit.currency} {scenario.monthly_revenue:g}/month; cost "
            f"{unit.currency} {scenario.monthly_cost:g}/month"
        )
    lines.extend(["", "## Financial Risks"])
    for risk in result.financial_risks:
        exposure = ""
        if risk.amount_at_risk is not None:
            exposure = f"; exposure {risk.currency} {risk.amount_at_risk:g}"
        lines.append(
            f"- **{risk.probability}/{risk.impact}**: {risk.risk}{exposure}. "
            f"Mitigation: {risk.mitigation}"
        )
    lines.extend(["", "## Decision Thresholds"])
    for threshold in result.decision_thresholds:
        lines.extend([
            f"### {threshold.metric} ({threshold.measurement_period})",
            f"- Proceed: {threshold.proceed_if}",
            f"- Pause: {threshold.pause_if}",
            f"- Stop: {threshold.stop_if}",
        ])
    lines.append("")
    return "\n".join(lines)


class FinanceAgentService:
    """Run Finance and persist its JSON and Markdown task outputs."""

    def __init__(
        self,
        gateway_client: GatewayClient,
        artifact_store: FileArtifactStore,
        orchestration_service: OrchestrationService,
        protocol: Optional[FinanceGatewayProtocol] = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.orchestration = orchestration_service
        self.agent = FinanceAgent(gateway_client, protocol)

    async def execute(
        self,
        request: FinanceAgentRequest,
        *,
        created_by: str = FINANCE_AGENT_ID,
        correlation_id: Optional[str] = None,
    ) -> tuple[
        FinanceAgentResultV1,
        GatewayCompletion,
        StoredArtifact,
        StoredArtifact,
        Any,
        Any,
    ]:
        try:
            result, completion = await self.agent.execute(request)
            self._record_route_decision(
                request.context,
                completion,
                correlation_id,
            )
            writer = ArtifactRegistrationService(
                self.artifact_store,
                self.orchestration,
            )
            value = result.model_dump(mode="json", exclude_none=True)
            json_bytes = self.artifact_store.canonical_json_bytes(value)
            json_checksum = hashlib.sha256(json_bytes).hexdigest()
            json_stored, json_domain, _ = writer.write_json(
                run_id=request.context.run_id,
                task_id=request.context.task_id,
                relation="output",
                logical_name="finance-brief",
                filename="finance-brief.json",
                value=value,
                created_by=created_by,
                idempotency_key=_idempotency_key(
                    result,
                    request.context,
                    "finance-brief",
                    "finance-brief.json",
                    json_checksum,
                ),
                provenance={"agent_id": FINANCE_AGENT_ID},
                correlation_id=correlation_id,
            )
            markdown = _render_markdown(result)
            markdown_checksum = hashlib.sha256(markdown.encode()).hexdigest()
            md_stored, md_domain, _ = writer.write_text(
                run_id=request.context.run_id,
                task_id=request.context.task_id,
                relation="output",
                logical_name="finance-brief-md",
                filename="finance-brief.md",
                text=markdown,
                content_type="text/markdown; charset=utf-8",
                created_by=created_by,
                idempotency_key=_idempotency_key(
                    result,
                    request.context,
                    "finance-brief-md",
                    "finance-brief.md",
                    markdown_checksum,
                ),
                provenance={"agent_id": FINANCE_AGENT_ID},
                correlation_id=correlation_id,
            )
            return (
                result,
                completion,
                json_stored,
                md_stored,
                json_domain,
                md_domain,
            )
        except FinanceAgentValidationFailure as exc:
            raise FinanceAgentExecutionError(str(exc)) from exc
        except Exception as exc:
            if isinstance(exc, FinanceAgentExecutionError):
                raise
            raise FinanceAgentExecutionError(
                f"Finance Agent execution failed: {exc}"
            ) from exc

    def _record_route_decision(
        self,
        context: FinanceTaskContext,
        completion: GatewayCompletion,
        correlation_id: Optional[str],
    ) -> None:
        if not (
            completion.selected_model
            and completion.selected_provider
            and completion.routing_reason
        ):
            raise FinanceAgentRouteEvidenceError(
                "Gateway completion is missing routing evidence"
            )
        raw_latency = completion.raw_metadata.get("latency_ms")
        try:
            latency_ms = float(raw_latency) if raw_latency is not None else None
        except (TypeError, ValueError):
            latency_ms = None
        self.orchestration.record_route_decision(
            run_id=context.run_id,
            task_id=context.task_id,
            requested_model=completion.requested_model,
            selected_model=completion.selected_model,
            provider=completion.selected_provider,
            reason=completion.routing_reason,
            fallback_used=completion.fallback_used,
            latency_ms=latency_ms,
            correlation_id=correlation_id,
            metadata={
                "agent_id": FINANCE_AGENT_ID,
                "schema_version": FINANCE_SCHEMA_VERSION,
            },
        )
