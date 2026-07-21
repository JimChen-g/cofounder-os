from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.artifacts import FileArtifactStore
from app.clients import GatewayClient
from app.insurance_poc import GoldenWorkflowRequest, InsurancePOCEvidenceService
from app.insurance_poc.live_agents import (
    EngineeringPlanningAgent,
    LiveAgentValidationFailure,
)
from app.insurance_poc.runtime import InsurancePOCTaskRuntime
from app.insurance_poc.workflow import InsurancePOCGoldenWorkflow
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


def _gateway_response(content: dict[str, object], request_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"X-Request-ID": request_id},
        json={
            "id": request_id,
            "choices": [
                {
                    "message": {"role": "assistant", "content": json.dumps(content)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
            "cofounder_os": {
                "selected_provider": "qwen",
                "selected_upstream_model": "qwen-live-test",
                "routing_reason": "adaptive_constraint_score",
                "latency_ms": 42.5,
            },
        },
    )


def _gateway_text_response(content: str, request_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"X-Request-ID": request_id},
        json={
            "id": request_id,
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "total_tokens": 200,
            },
            "cofounder_os": {
                "selected_provider": "qwen",
                "selected_upstream_model": "qwen-live-test",
                "routing_reason": "adaptive_constraint_score",
                "latency_ms": 42.5,
            },
        },
    )


def _engineering_result(evidence_id: str) -> dict[str, object]:
    return {
        "schema_version": "insurance-engineering-llm-1.0",
        "plan_summary": "Integrate the bounded evidence and claims-review workflow.",
        "workstreams": [
            {
                "name": "Evidence integration",
                "deliverable": "Traceable evidence adapter",
                "days": [1, 2, 3],
                "dependencies": [],
                "acceptance_check": "Every conclusion cites an Evidence ID.",
            },
            {
                "name": "Governed workbench",
                "deliverable": "Human-review claims workbench",
                "days": [4, 5, 6, 7],
                "dependencies": ["Evidence integration"],
                "acceptance_check": "No autonomous claim decision is exposed.",
            },
        ],
        "two_week_sequence": [
            "Days 1-3: evidence integration",
            "Days 4-7: governed workbench",
            "Days 8-10: evaluation and rehearsal",
        ],
        "reused_capabilities": ["Workflow Controller", "Artifact Store"],
        "limitations": ["Planning only; no repository execution"],
        "validation_checks": ["Evidence citations", "Policy Gate path"],
        "source_evidence": [evidence_id],
        "execution_status": "plan_only",
        "code_diff": None,
        "test_result": None,
    }


def _risk_result(evidence_id: str) -> dict[str, object]:
    return {
        "schema_version": "insurance-risk-llm-1.0",
        "review_summary": "Liability output must remain advisory and private evidence local.",
        "findings": [
            {
                "risk_id": "R-AUTHORITY-001",
                "category": "authority",
                "severity": "critical",
                "finding": "Autonomous liability wording exceeds model authority.",
                "control": "Require model recommendation plus human review.",
                "source_evidence": [evidence_id],
            },
            {
                "risk_id": "R-PRIVACY-001",
                "category": "privacy",
                "severity": "high",
                "finding": "Raw accident evidence cannot be externally uploaded.",
                "control": "Keep raw evidence local and release sanitized artifacts only.",
                "source_evidence": [evidence_id],
            },
        ],
        "recommended_decision_mode": "model_recommendation_plus_human_review",
        "private_upload_allowed": False,
        "required_human_approval": True,
        "required_controls": ["Policy Gate", "Founder approval"],
        "source_evidence": [evidence_id],
    }


@pytest.mark.asyncio
async def test_engineering_agent_repairs_once_and_records_live_evidence() -> None:
    evidence_service = InsurancePOCEvidenceService(FIXTURE_DIR)
    fixture = evidence_service.fixture()
    package = evidence_service.extract(
        GoldenWorkflowRequest(mission=fixture.mission, attachments=fixture.attachments)
    )
    evidence_id = package.evidence[0].evidence_id
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _gateway_response({"invalid": True}, "req-invalid")
        return _gateway_response(_engineering_result(evidence_id), "req-repaired")

    gateway = GatewayClient(
        "http://gateway.test",
        transport=httpx.MockTransport(handler),
    )
    result, call = await EngineeringPlanningAgent(gateway).execute(
        virtual_model="cofounder-qwen",
        evidence=package,
        product={"scope_items": []},
        finance={"accepted_scope": []},
        project_status={"available": ["runtime"], "missing_or_limited": []},
    )

    assert result.execution_status == "plan_only"
    assert result.code_diff is None
    assert calls == 2
    assert call.call_count == 2
    assert call.repair_performed is True
    assert call.request_id == "req-repaired"
    assert call.selected_upstream_model == "qwen-live-test"


@pytest.mark.asyncio
async def test_engineering_agent_preserves_call_evidence_after_failed_repair() -> None:
    evidence_service = InsurancePOCEvidenceService(FIXTURE_DIR)
    fixture = evidence_service.fixture()
    package = evidence_service.extract(
        GoldenWorkflowRequest(mission=fixture.mission, attachments=fixture.attachments)
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _gateway_text_response("{not valid JSON", f"req-invalid-{calls}")

    gateway = GatewayClient(
        "http://gateway.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(LiveAgentValidationFailure) as captured:
        await EngineeringPlanningAgent(gateway).execute(
            virtual_model="cofounder-qwen",
            evidence=package,
            product={"scope_items": []},
            finance={"accepted_scope": []},
            project_status={"available": ["runtime"], "missing_or_limited": []},
        )

    assert calls == 2
    call = captured.value.call_evidence
    assert call is not None
    assert call.call_count == 2
    assert call.repair_performed is True
    assert call.request_id == "req-invalid-2"
    assert call.selected_upstream_model == "qwen-live-test"


@pytest.mark.asyncio
async def test_golden_workflow_executes_two_live_agents_and_keeps_gate_deterministic(
    tmp_path: Path,
) -> None:
    evidence_service = InsurancePOCEvidenceService(FIXTURE_DIR)
    fixture = evidence_service.fixture()
    request = GoldenWorkflowRequest(
        mission=fixture.mission,
        attachments=fixture.attachments,
        owner="Founder",
    )
    package = evidence_service.extract(request)
    evidence_id = package.evidence[0].evidence_id

    def handler(http_request: httpx.Request) -> httpx.Response:
        payload = json.loads(http_request.content)
        system = payload["messages"][0]["content"]
        if "Engineering Planning Agent" in system:
            return _gateway_response(_engineering_result(evidence_id), "req-engineering")
        if "Risk Review Agent" in system:
            return _gateway_response(_risk_result(evidence_id), "req-risk")
        raise AssertionError(f"Unexpected live Agent prompt: {system}")

    gateway = GatewayClient(
        "http://gateway.test",
        transport=httpx.MockTransport(handler),
    )
    orchestration = OrchestrationService(FileStateRepository(tmp_path / "runs"))
    store = FileArtifactStore(tmp_path)
    controller = WorkflowController(
        orchestration=orchestration,
        agent_execution=AgentExecutionService(orchestration.repository),
        artifact_store=store,
        product_agent_service=ProductAgentService(gateway, store, orchestration),
        finance_agent_service=FinanceAgentService(gateway, store, orchestration),
        artifact_synthesizer=ArtifactSynthesizer(store, orchestration),
    )
    controller.register_task_adapter(
        InsurancePOCTaskRuntime(
            orchestration=orchestration,
            artifact_store=store,
            gateway=gateway,
        )
    )
    workflow = InsurancePOCGoldenWorkflow(
        fixture_dir=FIXTURE_DIR,
        orchestration=orchestration,
        artifacts=ArtifactRegistrationService(store, orchestration),
        workflow_controller=controller,
    )

    result = await workflow.execute(
        request,
        package,
        provider_health={"cofounder-qwen": True, "cofounder-step": False},
        provider_latency_ms={"cofounder-qwen": 42.5},
    )

    routes = {route.metadata["task_key"]: route for route in result.snapshot.route_decisions}
    for key in ("engineering-plan", "risk-review"):
        route = routes[key]
        assert route.selected_model == "cofounder-qwen"
        assert route.execution_status == "executed"
        assert route.metadata["execution_backend"] == "gateway_llm_agent"
        assert route.metadata["selected_provider"] == "qwen"
        assert route.metadata["selected_upstream_model"] == "qwen-live-test"
        assert route.metadata["call_count"] == 1
    assert result.routing_plan.live_model_calls == 2
    assert result.snapshot.run.metadata["live_model_calls"] == 2
    assert result.snapshot.run.status == "waiting_approval"
    assert any(event.event_type == "policy.denied" for event in result.snapshot.events)
    engineering = next(
        artifact
        for artifact in result.snapshot.artifacts
        if artifact.name == "engineering-delivery-plan"
    )
    engineering_value = store.read_json(
        engineering.run_id,
        str(engineering.metadata["logical_name"]),
        str(engineering.metadata["filename"]),
        engineering.task_id,
    )
    assert engineering_value["execution_backend"] == "gateway_llm_agent"
    assert engineering_value["code_diff"] is None
    assert engineering.metadata["provenance"]["live_model_calls"] == 1
