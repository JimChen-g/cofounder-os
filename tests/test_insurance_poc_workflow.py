from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.insurance_poc import router
from app.artifacts import FileArtifactStore
from app.evaluation import EvaluationService
from app.insurance_poc import (
    GoldenWorkflowRequest,
    InsurancePOCEvidenceService,
    InsurancePOCGoldenWorkflow,
)
from app.services.artifact_write import ArtifactRegistrationService
from app.services.execution import AgentExecutionService
from app.services.finance_agent import FinanceAgentService
from app.services.orchestration import OrchestrationService
from app.services.product_agent import ProductAgentService
from app.services.workflow_controller import WorkflowController
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"


def _services(
    tmp_path: Path,
) -> tuple[
    InsurancePOCEvidenceService,
    InsurancePOCGoldenWorkflow,
    OrchestrationService,
    FileArtifactStore,
]:
    evidence = InsurancePOCEvidenceService(FIXTURE_DIR)
    orchestration = OrchestrationService(FileStateRepository(tmp_path / "runs"))
    store = FileArtifactStore(tmp_path)
    workflow = InsurancePOCGoldenWorkflow(
        fixture_dir=FIXTURE_DIR,
        orchestration=orchestration,
        artifacts=ArtifactRegistrationService(store, orchestration),
    )
    return evidence, workflow, orchestration, store


def _request(evidence: InsurancePOCEvidenceService) -> GoldenWorkflowRequest:
    fixture = evidence.fixture()
    return GoldenWorkflowRequest(
        mission=fixture.mission,
        attachments=fixture.attachments,
        owner="Founder",
    )


def _read_json(store: FileArtifactStore, artifact) -> dict[str, object]:
    return store.read_json(
        artifact.run_id,
        str(artifact.metadata["logical_name"]),
        str(artifact.metadata["filename"]),
        artifact.task_id,
    )


def test_golden_workflow_persists_shared_dag_conflicts_and_governance(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, orchestration, store = _services(tmp_path)
    request = _request(evidence_service)
    package = evidence_service.extract(request)

    result = workflow.execute(request, package, correlation_id="test-golden")

    snapshot = result.snapshot
    assert snapshot.run.status == "waiting_approval"
    assert len(snapshot.tasks) == 8
    assert all(task.status == "completed" for task in snapshot.tasks)
    assert all(
        task.metadata["evidence_package_id"] == str(package.package_id) for task in snapshot.tasks
    )
    assert all(
        task.metadata["route_execution_status"] == "decision_only" for task in snapshot.tasks
    )
    by_key = {task.metadata["task_key"]: task for task in snapshot.tasks}
    assert set(by_key["engineering-plan"].dependency_ids) == {
        by_key["product-analysis"].id,
        by_key["finance-analysis"].id,
    }
    assert set(by_key["risk-review"].dependency_ids) == {
        by_key["product-analysis"].id,
        by_key["finance-analysis"].id,
    }
    assert by_key["product-analysis"].metadata["stage"] == 3
    assert by_key["finance-analysis"].metadata["stage"] == 3
    assert by_key["engineering-plan"].metadata["stage"] == 4
    assert by_key["risk-review"].metadata["stage"] == 4
    assert len(snapshot.route_decisions) == 8
    assert all(route.execution_status == "decision_only" for route in snapshot.route_decisions)
    assert result.routing_plan.live_model_calls == 0
    assert len(result.conflicts) == 2
    assert {item.conflict_type for item in result.conflicts} == {
        "scope_budget",
        "authority_boundary",
    }

    artifact_names = {artifact.name for artifact in snapshot.artifacts}
    assert {
        "executive-decision-memo",
        "insurance-poc-product-brief",
        "technical-implementation-plan",
        "budget-summary",
        "risk-register",
        "two-week-action-plan",
        "verification-report",
        "conflict-resolution-log",
    } <= artifact_names
    final_artifacts = [
        artifact
        for artifact in snapshot.artifacts
        if artifact.name
        in {
            "executive-decision-memo",
            "insurance-poc-product-brief",
            "technical-implementation-plan",
            "budget-summary",
            "risk-register",
            "two-week-action-plan",
        }
    ]
    assert len(final_artifacts) == 6
    assert all(
        artifact.metadata["artifact_version"] == "2.0"
        and artifact.metadata["validation_status"] == "verified_with_revision"
        and artifact.metadata["source_agents"]
        and artifact.metadata["source_evidence"]
        for artifact in final_artifacts
    )

    conflicts = next(
        artifact for artifact in snapshot.artifacts if artifact.name == "conflict-resolution-log"
    )
    conflict_value = _read_json(store, conflicts)
    scope = conflict_value["conflicts"][0]
    assert scope["proposal_before"]["planned_cost_cny"] == 63_000
    assert scope["proposal_after"]["planned_cost_cny"] == 45_000
    assert scope["proposal_after"]["deferred"] == "Optional automated insurer write-back"

    verification = next(
        artifact for artifact in snapshot.artifacts if artifact.name == "verification-report"
    )
    verification_value = _read_json(store, verification)
    assert verification_value["issues_found"] == 2
    assert verification_value["status"] == "revised_and_passed"

    approval = snapshot.approvals[0]
    assert approval.status == "pending"
    decisions = approval.metadata["policy_decisions"]
    assert decisions["private_upload"]["disposition"] == "deny"
    assert decisions["sanitized_dispatch"]["disposition"] == "require_approval"
    assert approval.metadata["external_action_executed"] is False
    assert orchestration.get_snapshot(result.run_id).run.status == "waiting_approval"
    evaluation = EvaluationService(orchestration, store).evaluate_run(result.run_id)
    artifact_dimension = next(item for item in evaluation.dimensions if item.key == "artifacts")
    assert evaluation.required_artifact_count == 6
    assert artifact_dimension.score == 100


def test_golden_workflow_persists_fallback_without_claiming_execution(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, _, _ = _services(tmp_path)
    request = _request(evidence_service)
    request.unavailable_models = ["cofounder-step"]

    result = workflow.execute(request, evidence_service.extract(request))

    fallback_routes = [route for route in result.snapshot.route_decisions if route.fallback_used]
    assert len(fallback_routes) == 3
    assert all(route.selected_model == "cofounder-qwen" for route in fallback_routes)
    assert all(route.execution_status == "decision_only" for route in fallback_routes)
    assert "no live Qwen or Step call" in result.execution_disclosure


def test_golden_workflow_api_returns_persisted_snapshot(tmp_path: Path) -> None:
    evidence_service, workflow, _, _ = _services(tmp_path)
    app = FastAPI()
    app.state.insurance_poc_evidence_service = evidence_service
    app.state.insurance_poc_workflow = workflow
    app.include_router(router)

    with TestClient(app) as client:
        fixture = client.get("/api/insurance-poc/fixture").json()
        response = client.post(
            "/api/insurance-poc/runs",
            json={
                "mission": fixture["mission"],
                "attachments": fixture["attachments"],
                "owner": "Founder",
            },
        )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "waiting_approval"
    assert len(payload["snapshot"]["tasks"]) == 8
    assert len(payload["snapshot"]["route_decisions"]) == 8
    assert len(payload["conflicts"]) == 2


class _NoCallGateway:
    async def complete(self, *args, **kwargs):
        raise AssertionError("Golden workflow replay must not call a model")


def _controller(
    orchestration: OrchestrationService,
    store: FileArtifactStore,
) -> WorkflowController:
    gateway = _NoCallGateway()
    return WorkflowController(
        orchestration=orchestration,
        agent_execution=AgentExecutionService(orchestration.repository),
        artifact_store=store,
        product_agent_service=ProductAgentService(gateway, store, orchestration),
        finance_agent_service=FinanceAgentService(gateway, store, orchestration),
        artifact_synthesizer=ArtifactSynthesizer(store, orchestration),
    )


@pytest.mark.asyncio
async def test_founder_approval_completes_and_replay_verifies_integrity(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, orchestration, store = _services(tmp_path)
    request = _request(evidence_service)
    created = workflow.execute(request, evidence_service.extract(request))

    orchestration.resolve_approval(
        created.run_id,
        created.approval_id,
        decision="approved",
        decided_by="founder",
        reason="Release only the sanitized demo package.",
        actor="product-api",
    )
    controller = _controller(orchestration, store)
    completed = await controller.run_until_terminal(created.run_id)
    replay = await controller.run_until_terminal(created.run_id)

    assert completed.status == "completed"
    assert replay.status == "completed"
    assert replay.replayed is True
    assert replay.cycles == 0
    evaluation = EvaluationService(orchestration, store).evaluate_run(created.run_id)
    assert evaluation.overall_score == 100
    assert evaluation.grade == "excellent"


def test_founder_rejection_stops_without_external_action(tmp_path: Path) -> None:
    evidence_service, workflow, orchestration, _ = _services(tmp_path)
    request = _request(evidence_service)
    created = workflow.execute(request, evidence_service.extract(request))

    orchestration.resolve_approval(
        created.run_id,
        created.approval_id,
        decision="rejected",
        decided_by="founder",
        reason="Do not dispatch any insurer package.",
        actor="product-api",
    )
    snapshot = orchestration.get_snapshot(created.run_id)

    assert snapshot.run.status == "failed"
    assert snapshot.approvals[0].status == "rejected"
    assert snapshot.approvals[0].metadata["external_action_executed"] is False
