"""D11 Product API contract and end-to-end transport tests."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.api.product import router
from app.artifacts import FileArtifactStore
from app.clients import GatewayClient, GatewayCompletion
from app.config import Settings
from app.orchestrators import ExecutiveOrchestrator
from app.policy import DeterministicPolicyGate
from app.services import (
    AgentExecutionService,
    FinanceAgentService,
    OrchestrationService,
    ProductAgentService,
    WorkflowController,
)
from app.services.product_api import ProductAPIService
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer
from tests.test_finance_agent import VALID_FINANCE_RESULT
from tests.test_product_agent import VALID_RESULT_DICT


def _plan(*, approval_required: bool) -> dict[str, object]:
    return {
        "objective": "Decide whether to launch the founder product",
        "summary": "Compare the product case and financial case, then decide.",
        "tasks": [
            {
                "key": "product-case",
                "title": "Build the product case",
                "description": "Define users, value, scope, and product risks.",
                "assigned_agent": "product-agent",
                "dependency_keys": [],
                "deliverable": "Validated product brief",
                "requires_approval": False,
            },
            {
                "key": "finance-case",
                "title": "Build the finance case",
                "description": "Model revenue, costs, economics, and thresholds.",
                "assigned_agent": "finance-agent",
                "dependency_keys": [],
                "deliverable": "Validated finance brief",
                "requires_approval": False,
            },
            {
                "key": "decision-bundle",
                "title": "Synthesize the decision bundle",
                "description": "Combine both accepted analyses into five artifacts.",
                "assigned_agent": "executive-orchestrator",
                "dependency_keys": ["product-case", "finance-case"],
                "deliverable": "Five founder decision artifacts",
                "requires_approval": False,
            },
        ],
        "approval_required": approval_required,
    }


class SequenceGateway:
    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, **kwargs) -> GatewayCompletion:
        self.calls += 1
        if not self.responses:
            raise AssertionError("Unexpected Gateway call")
        return GatewayCompletion(
            content=self.responses.pop(0),
            requested_model=kwargs["model"],
            selected_provider="qwen",
            selected_model=kwargs["model"],
            routing_reason="D11 product API test",
            request_id=f"product-api-{self.calls}",
        )


def _service(
    tmp_path: Path,
    *,
    approval_required: bool,
) -> tuple[ProductAPIService, SequenceGateway, FileArtifactStore]:
    gateway = SequenceGateway([
        json.dumps(_plan(approval_required=approval_required)),
        json.dumps(VALID_RESULT_DICT),
        json.dumps(VALID_FINANCE_RESULT),
    ])
    artifact_store = FileArtifactStore(tmp_path)
    repository = FileStateRepository(tmp_path / "runs")
    orchestration = OrchestrationService(repository)
    product_agent = ProductAgentService(
        gateway,
        artifact_store,
        orchestration,
    )
    finance_agent = FinanceAgentService(
        gateway,
        artifact_store,
        orchestration,
    )
    controller = WorkflowController(
        orchestration=orchestration,
        agent_execution=AgentExecutionService(repository),
        artifact_store=artifact_store,
        product_agent_service=product_agent,
        finance_agent_service=finance_agent,
        artifact_synthesizer=ArtifactSynthesizer(
            artifact_store,
            orchestration,
        ),
        policy_gate=DeterministicPolicyGate(),
    )
    return (
        ProductAPIService(
            executive=ExecutiveOrchestrator(gateway, orchestration),
            orchestration=orchestration,
            workflow_controller=controller,
            artifact_store=artifact_store,
        ),
        gateway,
        artifact_store,
    )


def _client(service: ProductAPIService) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = "req-d11-test"
        return await call_next(request)

    app.state.product_api_service = service
    app.include_router(router)
    return TestClient(app)


def test_create_read_artifacts_events_and_replay(tmp_path: Path) -> None:
    service, gateway, _ = _service(
        tmp_path,
        approval_required=False,
    )
    client = _client(service)
    try:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        created = client.post(
            "/api/runs",
            json={
                "objective": "Should we launch?",
                "context": "Budget is limited.",
                "owner": "founder",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        run_id = payload["run_id"]
        assert payload["status"] == "completed"
        assert payload["workflow"]["cycles"] >= 1
        assert payload["workflow"]["terminal_failure"] is False
        assert gateway.calls == 3

        snapshot = client.get(f"/api/runs/{run_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["run"]["status"] == "completed"
        assert len(snapshot.json()["tasks"]) == 3
        assert len(snapshot.json()["artifacts"]) == 9

        events = client.get(f"/api/runs/{run_id}/events?limit=5")
        assert events.status_code == 200
        assert events.json()["count"] == 5
        assert len(events.json()["events"]) == 5

        artifacts = client.get(f"/api/runs/{run_id}/artifacts")
        assert artifacts.status_code == 200
        artifact_payload = artifacts.json()
        assert artifact_payload["count"] == 9
        assert all(
            artifact["content_available"]
            for artifact in artifact_payload["artifacts"]
        )
        assert any(
            "# Executive Decision Memo" in artifact["content"]
            for artifact in artifact_payload["artifacts"]
            if artifact["content"] is not None
        )

        replay = client.post(
            f"/api/runs/{run_id}/retry",
        )
        assert replay.status_code == 200
        assert replay.json()["replayed"] is True
        assert replay.json()["cycles"] == 0
        assert gateway.calls == 3
    finally:
        client.close()


def test_plan_approval_pauses_then_resumes_to_completion(
    tmp_path: Path,
) -> None:
    service, gateway, _ = _service(
        tmp_path,
        approval_required=True,
    )
    client = _client(service)
    try:
        created = client.post(
            "/api/runs",
            json={"objective": "Should we launch?"},
        )
        assert created.status_code == 201
        payload = created.json()
        assert payload["status"] == "waiting_approval"
        assert payload["workflow"]["stalled"] is True
        assert gateway.calls == 1

        resolved = client.post(
            (
                f"/api/runs/{payload['run_id']}/approvals/"
                f"{payload['approval_id']}"
            ),
            json={
                "decision": "approved",
                "decided_by": "founder",
                "reason": "Proceed with the bounded analysis.",
            },
        )
        assert resolved.status_code == 200
        result = resolved.json()
        assert result["approval"]["status"] == "approved"
        assert result["workflow"]["status"] == "completed"
        assert len(result["workflow"]["snapshot"]["artifacts"]) == 9
        assert gateway.calls == 3
    finally:
        client.close()


def test_rejected_plan_approval_terminally_fails_without_agent_calls(
    tmp_path: Path,
) -> None:
    service, gateway, _ = _service(
        tmp_path,
        approval_required=True,
    )
    client = _client(service)
    try:
        created = client.post(
            "/api/runs",
            json={"objective": "Should we launch?"},
        ).json()
        rejected = client.post(
            (
                f"/api/runs/{created['run_id']}/approvals/"
                f"{created['approval_id']}"
            ),
            json={
                "decision": "rejected",
                "decided_by": "founder",
                "reason": "Do not spend the analysis budget.",
            },
        )
        assert rejected.status_code == 200
        assert rejected.json()["workflow"]["status"] == "failed"
        assert rejected.json()["workflow"]["terminal_failure"] is True
        assert gateway.calls == 1
    finally:
        client.close()


def test_not_found_and_validation_errors_are_bounded(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        approval_required=False,
    )
    client = _client(service)
    try:
        missing = client.get(f"/api/runs/{uuid4()}")
        assert missing.status_code == 404
        assert missing.json() == {
            "error": "not_found",
            "detail": "The requested Run, approval, or record was not found.",
            "request_id": "req-d11-test",
        }

        invalid = client.post(
            "/api/runs",
            json={
                "objective": "Valid",
                "unexpected": "forbidden",
            },
        )
        assert invalid.status_code == 422

        invalid_uuid = client.get("/api/runs/not-a-uuid")
        assert invalid_uuid.status_code == 422

        whitespace = client.post(
            "/api/runs",
            json={"objective": "   "},
        )
        assert whitespace.status_code == 422
    finally:
        client.close()


def test_artifact_metadata_can_be_read_without_content(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(
        tmp_path,
        approval_required=False,
    )
    client = _client(service)
    try:
        run_id = client.post(
            "/api/runs",
            json={"objective": "Should we launch?"},
        ).json()["run_id"]
        response = client.get(
            f"/api/runs/{run_id}/artifacts?include_content=false"
        )
        assert response.status_code == 200
        assert response.json()["count"] == 9
        assert all(
            artifact["content"] is None
            and artifact["content_omitted_reason"] == "content_not_requested"
            for artifact in response.json()["artifacts"]
        )
    finally:
        client.close()


def test_openapi_exposes_exact_d11_minimum_routes(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        approval_required=False,
    )
    client = _client(service)
    try:
        paths = set(client.get("/openapi.json").json()["paths"])
        assert paths == {
            "/api/health",
            "/api/runs",
            "/api/runs/{run_id}",
            "/api/runs/{run_id}/events",
            "/api/runs/{run_id}/artifacts",
            "/api/runs/{run_id}/approvals/{approval_id}",
            "/api/runs/{run_id}/retry",
        }
    finally:
        client.close()


def test_artifact_integrity_failure_returns_bounded_conflict(
    tmp_path: Path,
) -> None:
    service, _, artifact_store = _service(
        tmp_path,
        approval_required=False,
    )
    client = _client(service)
    try:
        run_id = client.post(
            "/api/runs",
            json={"objective": "Should we launch?"},
        ).json()["run_id"]
        artifact = service.get_run(run_id).artifacts[0]
        logical_name = str(artifact.metadata["logical_name"])
        filename = str(artifact.metadata["filename"])
        scope = (
            Path("tasks") / str(artifact.task_id)
            if artifact.task_id is not None
            else Path("run")
        )
        content_path = (
            artifact_store.root
            / "runs"
            / run_id
            / "artifacts"
            / scope
            / logical_name
            / filename
        )
        content_path.write_text("corrupt", encoding="utf-8")

        response = client.get(f"/api/runs/{run_id}/artifacts")
        assert response.status_code == 409
        assert response.json() == {
            "error": "workflow_conflict",
            "detail": (
                "The workflow cannot continue with its current persisted state."
            ),
            "request_id": "req-d11-test",
        }
        assert str(tmp_path) not in response.text
    finally:
        client.close()


def test_policy_selected_reviewer_is_enforced_before_mutation(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(
        tmp_path,
        approval_required=False,
    )
    orchestration = service.orchestration
    run, _ = orchestration.create_run(
        objective="Review an external write",
        actor="founder",
    )
    orchestration.start_run(
        run.id,
        actor="workflow-controller",
        reason="Begin review.",
    )
    task, _ = orchestration.create_task(
        run.id,
        title="External write",
        description="Write an approved external record.",
        assigned_agent="product-agent",
        actor="executive-orchestrator",
    )
    orchestration.mark_task_ready(
        run.id,
        task.id,
        actor="workflow-controller",
        reason="Dependencies complete.",
    )
    orchestration.start_task(
        run.id,
        task.id,
        actor="product-agent",
        reason="Claimed for guarded execution.",
    )
    approval = orchestration.request_approval(
        run.id,
        task_id=task.id,
        requested_by="workflow-controller",
        actor="workflow-controller",
        reason="Founder review is required.",
        metadata={"reviewer_required": "founder"},
    ).approval

    client = _client(service)
    try:
        response = client.post(
            f"/api/runs/{run.id}/approvals/{approval.id}",
            json={
                "decision": "approved",
                "decided_by": "not-the-founder",
                "reason": "Attempted approval.",
            },
        )
        assert response.status_code == 409
        persisted = orchestration.repository.get_approval(
            run.id,
            approval.id,
        )
        assert persisted.status == "pending"
    finally:
        client.close()


def test_create_response_uses_uuid_contract(tmp_path: Path) -> None:
    service, _, _ = _service(
        tmp_path,
        approval_required=False,
    )
    client = _client(service)
    try:
        payload = client.post(
            "/api/runs",
            json={"objective": "Should we launch?"},
        ).json()
        assert UUID(payload["run_id"])
    finally:
        client.close()


def test_production_composition_defaults_to_same_host_gateway(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("COFOUNDER_GATEWAY_URL", raising=False)
    settings = Settings(
        PRODUCT_DATA_DIR=str(tmp_path),
        GATEWAY_PORT=9100,
        REQUEST_TIMEOUT_SECONDS=45,
    )

    from app.services.product_api import build_product_api_service

    service = build_product_api_service(settings)
    gateway = service.executive.gateway
    assert isinstance(gateway, GatewayClient)
    assert gateway.base_url == "http://127.0.0.1:9100"
    assert gateway.timeout_seconds == 45


def test_production_composition_respects_explicit_gateway_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "COFOUNDER_GATEWAY_URL",
        "http://127.0.0.1:19000",
    )
    settings = Settings(PRODUCT_DATA_DIR=str(tmp_path))

    from app.services.product_api import build_product_api_service

    service = build_product_api_service(settings)
    gateway = service.executive.gateway
    assert isinstance(gateway, GatewayClient)
    assert gateway.base_url == "http://127.0.0.1:19000"
