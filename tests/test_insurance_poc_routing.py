from __future__ import annotations

from pathlib import Path

from app.insurance_poc import (
    EvidencePreviewRequest,
    ExplainableInsuranceRouter,
    InsurancePOCEvidenceService,
    RoutingPreviewRequest,
)
from app.services import OrchestrationService
from app.state import FileStateRepository


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"


def _package():
    evidence = InsurancePOCEvidenceService(FIXTURE_DIR)
    fixture = evidence.fixture()
    return evidence.extract(
        EvidencePreviewRequest(
            mission=fixture.mission,
            attachments=fixture.attachments,
        )
    )


def test_router_explains_distinct_private_planning_and_tool_routes() -> None:
    package = _package()
    plan = ExplainableInsuranceRouter().route(RoutingPreviewRequest(evidence_package=package))

    assert plan.package_id == package.package_id
    assert plan.live_model_calls == 0
    assert len(plan.decisions) == 10
    by_task = {decision.task_key: decision for decision in plan.decisions}
    assert by_task["evidence-extraction"].selected_model == ("insurance-evidence-adapter-chain")
    assert by_task["product-analysis"].selected_model == "product-agent-local"
    assert by_task["finance-analysis"].selected_model == "finance-agent-local"
    assert by_task["engineering-plan"].selected_model == "engineering-agent-local"
    assert by_task["verification"].selected_model == "verifier-local"
    assert by_task["finance-analysis"].estimated_cost_usd == 0
    assert by_task["product-analysis"].estimated_cost_usd == 0
    assert "cofounder-step" in by_task["finance-analysis"].excluded_models
    assert all(decision.validation_required for decision in plan.decisions)
    assert all(decision.execution_status == "decision_only" for decision in plan.decisions)


def test_router_records_simulated_fallback_without_claiming_model_execution() -> None:
    plan = ExplainableInsuranceRouter().route(
        RoutingPreviewRequest(
            evidence_package=_package(),
            unavailable_models=["product-agent-local"],
        )
    )

    affected = [
        decision for decision in plan.decisions if decision.task_key == "product-analysis"
    ]
    assert len(affected) == 1
    assert all(
        decision.selected_model == "generic-deterministic-agent-local"
        for decision in affected
    )
    assert all(decision.fallback_used for decision in affected)
    assert all(
        "declared unavailable" in decision.excluded_models["product-agent-local"]
        for decision in affected
    )
    assert plan.live_model_calls == 0
    assert "not model-call claims" in plan.simulation_disclosure


def test_router_adaptively_selects_healthy_qwen_for_semantic_specialists() -> None:
    plan = ExplainableInsuranceRouter().route(
        RoutingPreviewRequest(
            evidence_package=_package(),
            provider_health={"cofounder-qwen": True, "cofounder-step": False},
            provider_latency_ms={"cofounder-qwen": 125.0},
        )
    )

    by_task = {decision.task_key: decision for decision in plan.decisions}
    for key in ("engineering-plan", "risk-review"):
        decision = by_task[key]
        assert decision.selected_model == "cofounder-qwen"
        assert decision.provider == "qwen-local-dgx"
        assert decision.fallback_used is False
        assert decision.candidate_scores["cofounder-qwen"] > (
            decision.candidate_scores[f"{decision.task_key.split('-')[0]}-agent-local"]
        )
        assert decision.estimated_latency_ms == 125.0
        assert decision.selection_strategy == "adaptive_constraint_score"
    assert by_task["product-analysis"].selected_model == "product-agent-local"
    assert by_task["finance-analysis"].selected_model == "finance-agent-local"
    assert plan.measured_provider_health == {
        "cofounder-qwen": "healthy",
        "cofounder-step": "unavailable",
    }


def test_router_exposes_human_route_as_decision_only_when_local_routes_are_unavailable() -> None:
    plan = ExplainableInsuranceRouter().route(
        RoutingPreviewRequest(
            evidence_package=_package(),
            unavailable_models=[
                "product-agent-local",
                "generic-deterministic-agent-local",
            ],
        )
    )

    product = next(
        decision for decision in plan.decisions if decision.task_key == "product-analysis"
    )
    assert product.selected_model == "human-review"
    assert product.provider == "human"
    assert product.execution_status == "decision_only"


def test_explainable_route_fields_persist_in_authoritative_run_state(
    tmp_path: Path,
) -> None:
    repository = FileStateRepository(tmp_path / "runs")
    orchestration = OrchestrationService(repository)
    run, _ = orchestration.create_run(
        objective="Insurance POC",
        actor="founder",
    )
    decision = (
        ExplainableInsuranceRouter()
        .route(RoutingPreviewRequest(evidence_package=_package()))
        .decisions[2]
    )

    persisted, event = orchestration.record_route_decision(
        run.id,
        requested_model=decision.requested_model,
        selected_model=decision.selected_model,
        provider=decision.provider,
        reason=decision.reason,
        candidate_models=decision.candidate_models,
        excluded_models=decision.excluded_models,
        required_capabilities=decision.required_capabilities,
        input_modalities=[value.value for value in decision.input_modalities],
        privacy_level=decision.privacy_level,
        complexity=decision.complexity,
        context_length=decision.context_length,
        tool_requirement=decision.tool_requirement,
        latency_budget_ms=decision.latency_budget_ms,
        cost_budget_usd=decision.cost_budget_usd,
        estimated_latency_ms=decision.estimated_latency_ms,
        estimated_cost_usd=decision.estimated_cost_usd,
        privacy_decision=decision.privacy_decision,
        fallback_model=decision.fallback_model,
        validation_required=decision.validation_required,
        validation_requirement=decision.validation_requirement,
        execution_status=decision.execution_status,
        fallback_used=decision.fallback_used,
    )

    restored = repository.get_route_decision(run.id, persisted.id)
    assert restored.required_capabilities == decision.required_capabilities
    assert restored.excluded_models == decision.excluded_models
    assert restored.privacy_decision == decision.privacy_decision
    assert restored.fallback_model == decision.fallback_model
    assert restored.validation_required is True
    assert restored.execution_status == "decision_only"
    assert event.details["estimated_cost_usd"] == decision.estimated_cost_usd
    assert event.details["validation_requirement"] == decision.validation_requirement
