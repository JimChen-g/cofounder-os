from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.insurance_poc import router as insurance_router
from app.api.product import router as product_router
from app.artifacts import FileArtifactStore
from app.clients import GatewayClient
from app.domain import Artifact
from app.insurance_poc import (
    GoldenWorkflowRequest,
    InsurancePOCEvidenceService,
    InsurancePOCGoldenWorkflow,
)
from app.insurance_poc.runtime import InsurancePOCTaskRuntime
from app.orchestrators import ExecutiveOrchestrator
from app.services.artifact_write import ArtifactRegistrationService
from app.services.execution import AgentExecutionService
from app.services.finance_agent import FinanceAgentService
from app.services.orchestration import OrchestrationService
from app.services.product_agent import ProductAgentService
from app.services.product_api import ProductAPIService
from app.services.workflow_controller import WorkflowController
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"


def _controller(
    orchestration: OrchestrationService,
    store: FileArtifactStore,
) -> WorkflowController:
    gateway = GatewayClient("http://127.0.0.1:1", timeout_seconds=0.1)
    return WorkflowController(
        orchestration=orchestration,
        agent_execution=AgentExecutionService(orchestration.repository),
        artifact_store=store,
        product_agent_service=ProductAgentService(gateway, store, orchestration),
        finance_agent_service=FinanceAgentService(gateway, store, orchestration),
        artifact_synthesizer=ArtifactSynthesizer(store, orchestration),
    )


def _services(
    tmp_path: Path,
) -> tuple[
    InsurancePOCEvidenceService,
    InsurancePOCGoldenWorkflow,
    ProductAPIService,
    FileArtifactStore,
]:
    evidence = InsurancePOCEvidenceService(FIXTURE_DIR)
    orchestration = OrchestrationService(FileStateRepository(tmp_path / "runs"))
    store = FileArtifactStore(tmp_path)
    controller = _controller(orchestration, store)
    workflow = InsurancePOCGoldenWorkflow(
        fixture_dir=FIXTURE_DIR,
        orchestration=orchestration,
        artifacts=ArtifactRegistrationService(store, orchestration),
        workflow_controller=controller,
    )
    gateway = GatewayClient("http://127.0.0.1:1", timeout_seconds=0.1)
    product = ProductAPIService(
        executive=ExecutiveOrchestrator(gateway, orchestration),
        orchestration=orchestration,
        workflow_controller=controller,
        artifact_store=store,
    )
    return evidence, workflow, product, store


def _request(evidence: InsurancePOCEvidenceService) -> GoldenWorkflowRequest:
    fixture = evidence.fixture()
    return GoldenWorkflowRequest(
        mission=fixture.mission,
        attachments=fixture.attachments,
        owner="Founder",
    )


def _read_json(store: FileArtifactStore, artifact: Artifact) -> dict[str, object]:
    value = store.read_json(
        artifact.run_id,
        str(artifact.metadata["logical_name"]),
        str(artifact.metadata["filename"]),
        artifact.task_id,
    )
    if not isinstance(value, dict):
        raise TypeError("Expected a JSON object")
    return value


@pytest.mark.asyncio
async def test_golden_workflow_executes_agents_conflicts_and_governance(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, product, store = _services(tmp_path)
    request = _request(evidence_service)
    package = evidence_service.extract(request)
    result = await workflow.execute(request, package, correlation_id="test-golden")

    snapshot = result.snapshot
    assert snapshot.run.status == "waiting_approval"
    assert len(snapshot.tasks) == 10
    by_key = {task.metadata["task_key"]: task for task in snapshot.tasks}
    assert by_key["private-upload-policy"].status == "cancelled"
    assert by_key["release-approval"].status == "waiting_approval"
    assert all(
        task.status == "completed"
        for key, task in by_key.items()
        if key not in {"private-upload-policy", "release-approval"}
    )
    assert all(
        task.metadata["evidence_package_id"] == str(package.package_id)
        for task in snapshot.tasks
    )
    assert set(by_key["finance-analysis"].dependency_ids) == {
        by_key["executive-orchestration"].id
    }
    assert set(by_key["engineering-plan"].dependency_ids) == {
        by_key["product-analysis"].id,
        by_key["finance-analysis"].id,
    }
    assert set(by_key["risk-review"].dependency_ids) == {
        by_key["product-analysis"].id,
        by_key["finance-analysis"].id,
    }

    assert len(snapshot.route_decisions) == 10
    route_by_key = {
        route.metadata["task_key"]: route for route in snapshot.route_decisions
    }
    assert all(
        route_by_key[key].execution_status == "executed"
        for key in by_key
        if key not in {"private-upload-policy", "release-approval"}
    )
    assert all(
        route_by_key[key].metadata["execution_backend"]
        == "deterministic_local_agent"
        for key in by_key
        if key not in {"private-upload-policy", "release-approval"}
    )
    assert result.routing_plan.live_model_calls == 0
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
        and artifact.metadata["source_agents"][-1] == "verifier"
        and artifact.metadata["validation_status"] == "verified_with_revision"
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
    assert scope["proposal_after"]["deferred"][0]["optional"] is True

    verification = next(
        artifact for artifact in snapshot.artifacts if artifact.name == "verification-report"
    )
    verification_value = _read_json(store, verification)
    assert verification_value["issues_found"] == 2
    assert verification_value["status"] == "revised_and_passed"

    approval = snapshot.approvals[0]
    assert approval.status == "pending"
    assert approval.metadata["reviewer_required"] == "founder"
    assert approval.metadata["policy_action_sha256"]
    policy_events = [event.event_type for event in snapshot.events]
    assert "policy.denied" in policy_events
    assert "policy.approval_required" in policy_events
    assert policy_events.count("agent.executed") == 8
    assert product.get_run(result.run_id).run.status == "waiting_approval"


@pytest.mark.asyncio
async def test_submitted_fallback_is_bound_to_actual_local_execution(tmp_path: Path) -> None:
    evidence_service, workflow, _, _ = _services(tmp_path)
    request = _request(evidence_service)
    request.unavailable_models = ["product-agent-local"]
    result = await workflow.execute(request, evidence_service.extract(request))

    product_task = next(
        task for task in result.snapshot.tasks if task.metadata["task_key"] == "product-analysis"
    )
    route = next(
        route for route in result.snapshot.route_decisions if route.task_id == product_task.id
    )
    assert route.selected_model == "generic-deterministic-agent-local"
    assert route.fallback_used is True
    assert route.execution_status == "executed"
    assert route.metadata["execution_backend"] == "deterministic_local_agent"


def test_golden_workflow_api_issues_capability_and_rejects_missing_cookie(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, product, _ = _services(tmp_path)
    app = FastAPI()
    app.state.insurance_poc_evidence_service = evidence_service
    app.state.insurance_poc_workflow = workflow
    app.state.product_api_service = product
    app.include_router(insurance_router)
    app.include_router(product_router)

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
        assert len(payload["snapshot"]["tasks"]) == 10
        assert len(payload["snapshot"]["route_decisions"]) == 10
        cookie_name = f"cofounder_approval_{payload['run_id'].replace('-', '')}"
        assert client.cookies.get(cookie_name)
        client.cookies.clear()
        denied = client.post(
            f"/api/runs/{payload['run_id']}/approvals/{payload['approval_id']}",
            json={
                "decision": "approved",
                "decided_by": "founder",
                "reason": "Approve sanitized package.",
                "max_cycles": 100,
            },
        )
    assert denied.status_code == 409


@pytest.mark.asyncio
async def test_authenticated_founder_approval_completes_and_replay_verifies(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, product, _ = _services(tmp_path)
    request = _request(evidence_service)
    capability = "founder-test-capability"
    created = await workflow.execute(
        request,
        evidence_service.extract(request),
        approval_capability_sha256=hashlib.sha256(capability.encode()).hexdigest(),
    )

    completed = await product.resolve_approval(
        created.run_id,
        created.approval_id,
        decision="approved",
        decided_by="founder",
        reason="Release only the sanitized demo package.",
        approval_capability=capability,
        correlation_id="approval-test",
        max_cycles=100,
    )
    replay = await product.retry_run(
        created.run_id,
        correlation_id="replay-test",
        max_cycles=100,
    )

    assert completed.workflow.status == "completed"
    assert replay.status == "completed"
    assert replay.replayed is True
    release = next(
        task
        for task in replay.snapshot.tasks
        if task.metadata["task_key"] == "release-approval"
    )
    assert release.status == "completed"
    assert any(
        artifact.name == "governed-release-receipt"
        for artifact in replay.snapshot.artifacts
    )


@pytest.mark.asyncio
async def test_failure_retry_and_fresh_controller_restart_are_recoverable(
    tmp_path: Path,
) -> None:
    evidence_service, workflow, product, store = _services(tmp_path)
    request = _request(evidence_service)
    created = await workflow.execute(
        request,
        evidence_service.extract(request),
        failure_injection_task="finance-analysis",
    )
    finance = next(
        task
        for task in created.snapshot.tasks
        if task.metadata["task_key"] == "finance-analysis"
    )
    assert finance.status == "completed"
    assert finance.attempt_count == 2
    assert any(
        event.event_type == "task.attempt_failed" and event.task_id == finance.id
        for event in created.snapshot.events
    )

    fresh_controller = _controller(product.orchestration, store)
    fresh_controller.register_task_adapter(
        InsurancePOCTaskRuntime(
            orchestration=product.orchestration,
            artifact_store=store,
        )
    )
    gateway = GatewayClient("http://127.0.0.1:1", timeout_seconds=0.1)
    restarted = ProductAPIService(
        executive=ExecutiveOrchestrator(gateway, product.orchestration),
        orchestration=product.orchestration,
        workflow_controller=fresh_controller,
        artifact_store=store,
    )
    resolution = await restarted.resolve_approval(
        created.run_id,
        created.approval_id,
        decision="approved",
        decided_by="founder",
        reason="Resume after controller restart.",
        approval_capability=None,
        correlation_id="restart-test",
        max_cycles=100,
    )
    assert resolution.workflow.status == "completed"


@pytest.mark.asyncio
async def test_two_independent_runs_have_independent_records(tmp_path: Path) -> None:
    evidence_service, workflow, product, _ = _services(tmp_path)
    request = _request(evidence_service)
    first = await workflow.execute(request, evidence_service.extract(request))
    second = await workflow.execute(request, evidence_service.extract(request))
    first_completed = await product.resolve_approval(
        first.run_id,
        first.approval_id,
        decision="approved",
        decided_by="founder",
        reason="Complete independent run one.",
        approval_capability=None,
        correlation_id="independent-one",
        max_cycles=100,
    )
    second_completed = await product.resolve_approval(
        second.run_id,
        second.approval_id,
        decision="approved",
        decided_by="founder",
        reason="Complete independent run two.",
        approval_capability=None,
        correlation_id="independent-two",
        max_cycles=100,
    )

    assert first.run_id != second.run_id
    assert first_completed.workflow.status == "completed"
    assert second_completed.workflow.status == "completed"
    assert {item.id for item in first.snapshot.tasks}.isdisjoint(
        {item.id for item in second.snapshot.tasks}
    )
    assert all(item.run_id == first.run_id for item in first.snapshot.artifacts)
    assert all(item.run_id == second.run_id for item in second.snapshot.artifacts)


@pytest.mark.asyncio
async def test_founder_rejection_stops_without_release_receipt(tmp_path: Path) -> None:
    evidence_service, workflow, product, _ = _services(tmp_path)
    request = _request(evidence_service)
    created = await workflow.execute(request, evidence_service.extract(request))
    rejected = await product.resolve_approval(
        created.run_id,
        created.approval_id,
        decision="rejected",
        decided_by="founder",
        reason="Do not dispatch any insurer package.",
        approval_capability=None,
        correlation_id="reject-test",
        max_cycles=100,
    )

    assert rejected.workflow.status == "failed"
    assert rejected.workflow.snapshot.approvals[0].status == "rejected"
    assert not any(
        artifact.name == "governed-release-receipt"
        for artifact in rejected.workflow.snapshot.artifacts
    )
