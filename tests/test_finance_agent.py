"""D07 Finance Agent contract and artifact tests."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Sequence

import pytest
from pydantic import ValidationError

from app.agents import FinanceAgent, FinanceAgentValidationFailure
from app.artifacts import FileArtifactStore
from app.clients.gateway import GatewayCompletion
from app.domain import (
    FinanceAgentRequest,
    FinanceAgentResultV1,
    FinanceTaskContext,
)
from app.services import FinanceAgentExecutionError, FinanceAgentService
from app.services.orchestration import OrchestrationService
from app.state import FileStateRepository


VALID_FINANCE_RESULT: dict[str, Any] = {
    "schema_version": "1.0",
    "revenue_assumptions": [{
        "stream": "Subscriptions",
        "pricing_model": "Per workspace",
        "currency": "USD",
        "unit_price": 99,
        "monthly_units": 100,
        "monthly_growth_rate": 0.1,
        "rationale": "Founder-led sales baseline",
    }],
    "cost_structure": [{
        "name": "Inference",
        "category": "variable",
        "amount": 2000,
        "currency": "USD",
        "period": "month",
        "rationale": "Usage-linked model cost",
    }],
    "unit_economics": {
        "currency": "USD",
        "average_revenue_per_unit": 99,
        "variable_cost_per_unit": 20,
        "contribution_margin_per_unit": 79,
        "contribution_margin_ratio": 0.7979,
        "customer_acquisition_cost": 300,
        "lifetime_value": 1188,
        "ltv_cac_ratio": 3.96,
        "payback_months": 3.8,
    },
    "budget_scenarios": [
        {
            "name": "downside",
            "monthly_revenue": 4950,
            "monthly_cost": 6500,
            "runway_months": 8,
            "break_even_month": 12,
            "assumptions": ["50 paid workspaces"],
        },
        {
            "name": "base",
            "monthly_revenue": 9900,
            "monthly_cost": 7000,
            "runway_months": 12,
            "break_even_month": 6,
            "assumptions": ["100 paid workspaces"],
        },
        {
            "name": "upside",
            "monthly_revenue": 19800,
            "monthly_cost": 9000,
            "runway_months": 18,
            "break_even_month": 3,
            "assumptions": ["200 paid workspaces"],
        },
    ],
    "financial_risks": [{
        "risk": "Inference costs exceed plan",
        "probability": "medium",
        "impact": "high",
        "amount_at_risk": 12000,
        "currency": "USD",
        "mitigation": "Enforce per-workspace usage limits",
    }],
    "decision_thresholds": [{
        "metric": "LTV/CAC",
        "proceed_if": "At least 3.0",
        "pause_if": "Between 2.0 and 3.0",
        "stop_if": "Below 2.0",
        "measurement_period": "Trailing 90 days",
    }],
}


def _context(run_id: str, task_id: str) -> FinanceTaskContext:
    return FinanceTaskContext(
        run_id=run_id,
        task_id=task_id,
        objective="Decide whether to launch",
        task_title="Finance analysis",
        task_description="Model the MVP economics",
        required_deliverable="Finance brief",
        constraints=["No financial transactions"],
    )


class FakeGateway:
    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.calls = 0
        self.requests: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs) -> GatewayCompletion:
        self.calls += 1
        self.requests.append(kwargs)
        return GatewayCompletion(
            content=self.responses.pop(0),
            requested_model=kwargs["model"],
            selected_provider="qwen",
            selected_model=kwargs["model"],
            routing_reason="bounded finance route",
            request_id=f"finance-{self.calls}",
        )


@pytest.mark.asyncio
async def test_finance_agent_accepts_all_required_sections():
    gateway = FakeGateway([json.dumps(VALID_FINANCE_RESULT)])
    request = FinanceAgentRequest(
        context=_context(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
    )
    result, completion = await FinanceAgent(gateway).execute(request)
    assert isinstance(result, FinanceAgentResultV1)
    assert result.revenue_assumptions[0].stream == "Subscriptions"
    assert {scenario.name for scenario in result.budget_scenarios} == {
        "downside",
        "base",
        "upside",
    }
    assert completion.selected_provider == "qwen"
    assert gateway.calls == 1
    assert gateway.requests[0]["max_tokens"] == 8192


@pytest.mark.asyncio
async def test_finance_agent_repairs_once_and_stops():
    gateway = FakeGateway(["{}", "{}"])
    request = FinanceAgentRequest(
        context=_context(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        )
    )
    with pytest.raises(FinanceAgentValidationFailure):
        await FinanceAgent(gateway).execute(request)
    assert gateway.calls == 2


@pytest.mark.asyncio
async def test_finance_agent_can_disable_repair():
    gateway = FakeGateway(["{}"])
    request = FinanceAgentRequest(
        context=_context(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
        ),
        max_repair_attempts=0,
    )
    with pytest.raises(FinanceAgentValidationFailure):
        await FinanceAgent(gateway).execute(request)
    assert gateway.calls == 1


def test_finance_contract_rejects_unknown_or_non_finite_values():
    unknown = deepcopy(VALID_FINANCE_RESULT)
    unknown["unexpected"] = True
    with pytest.raises(ValidationError):
        FinanceAgentResultV1.model_validate(unknown)

    non_finite = deepcopy(VALID_FINANCE_RESULT)
    non_finite["unit_economics"]["lifetime_value"] = float("inf")
    with pytest.raises(ValidationError):
        FinanceAgentResultV1.model_validate(non_finite)


def test_finance_contract_rejects_duplicate_scenarios():
    duplicate = deepcopy(VALID_FINANCE_RESULT)
    duplicate["budget_scenarios"][2]["name"] = "downside"
    with pytest.raises(ValidationError, match="exactly"):
        FinanceAgentResultV1.model_validate(duplicate)


def test_finance_contract_requires_all_three_budget_scenarios():
    incomplete = deepcopy(VALID_FINANCE_RESULT)
    incomplete["budget_scenarios"] = [
        deepcopy(incomplete["budget_scenarios"][1])
    ]
    with pytest.raises(ValidationError):
        FinanceAgentResultV1.model_validate(incomplete)


@pytest.mark.asyncio
async def test_finance_service_records_route_and_idempotent_artifacts(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    orchestration = OrchestrationService(repository)
    run, _ = orchestration.create_run(
        objective="Decide whether to launch",
        actor="founder",
    )
    task, _ = orchestration.create_task(
        run.id,
        title="Finance analysis",
        description="Model the MVP economics",
        assigned_agent="finance-agent",
        actor="executive-orchestrator",
    )
    gateway = FakeGateway([
        json.dumps(VALID_FINANCE_RESULT),
        json.dumps(VALID_FINANCE_RESULT),
    ])
    service = FinanceAgentService(
        gateway,
        FileArtifactStore(tmp_path / "artifacts"),
        orchestration,
    )
    request = FinanceAgentRequest(context=_context(str(run.id), str(task.id)))

    first = await service.execute(request)
    second = await service.execute(request)

    assert first[2].checksum_sha256 == second[2].checksum_sha256
    snapshot = orchestration.get_snapshot(run.id)
    assert len(snapshot.route_decisions) == 2
    assert len(snapshot.artifacts) == 2
    assert {artifact.name for artifact in snapshot.artifacts} == {
        "finance-brief",
        "finance-brief-md",
    }


@pytest.mark.asyncio
async def test_finance_service_requires_real_route_evidence(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    orchestration = OrchestrationService(repository)
    run, _ = orchestration.create_run(objective="Launch", actor="founder")
    task, _ = orchestration.create_task(
        run.id,
        title="Finance",
        description="Model finances",
        assigned_agent="finance-agent",
        actor="executive-orchestrator",
    )

    class MissingEvidenceGateway(FakeGateway):
        async def complete(self, messages, **kwargs) -> GatewayCompletion:
            return GatewayCompletion(
                content=json.dumps(VALID_FINANCE_RESULT),
                requested_model=kwargs["model"],
            )

    service = FinanceAgentService(
        MissingEvidenceGateway(["unused"]),
        FileArtifactStore(tmp_path / "artifacts"),
        orchestration,
    )
    request = FinanceAgentRequest(context=_context(str(run.id), str(task.id)))
    with pytest.raises(FinanceAgentExecutionError, match="routing evidence"):
        await service.execute(request)
    assert orchestration.get_snapshot(run.id).artifacts == []
