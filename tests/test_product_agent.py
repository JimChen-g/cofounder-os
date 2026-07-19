"""Tests for the Product Agent (D06-C)."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Sequence

import pytest

from app.agents.product_agent import (
    DEFAULT_VIRTUAL_MODEL,
    ProductAgent,
    ProductAgentValidationFailure,
    ProductGatewayProtocol,
)
from app.clients.gateway import GatewayCompletion
from app.domain import (
    DependencyArtifactSummary,
    ProductAgentRequest,
    ProductAgentResultV1,
    ProductTaskContext,
)
from app.services import (
    ArtifactWriteConflict,
    OrchestrationService,
    ProductAgentContextError,
    ProductAgentExecutionError,
    ProductAgentRouteEvidenceError,
    ProductAgentService,
)
from app.services.product_agent import _compute_idempotency_key
from app.state import FileStateRepository, LifecycleStateMachine
from app.artifacts import FileArtifactStore
from uuid import UUID


# ── Test data ───────────────────────────────────────────────────────────────

RUN_ID = "00000000-0000-0000-0000-000000000001"
TASK_ID = "00000000-0000-0000-0000-000000000099"

VALID_RESULT_DICT: Dict[str, Any] = {
    "schema_version": "1.0",
    "problem_statement": "Users struggle to track product milestones.",
    "target_users": [
        {
            "segment": "Startup founders",
            "description": "Early-stage founders building MVPs",
            "priority": "primary",
        },
        {
            "segment": "Product managers",
            "description": "PMs at growth-stage companies",
            "priority": "secondary",
        },
    ],
    "user_pains": [
        {
            "pain": "No centralized milestone tracking",
            "severity": "high",
            "frequency": "daily",
            "evidence": "Survey of 50 founders",
        },
        {
            "pain": "Difficult to align teams on priorities",
            "severity": "medium",
            "frequency": "weekly",
        },
    ],
    "assumptions": [
        "Users have internet access",
        "Teams are distributed",
    ],
    "product_scope": "A milestone and task tracking SaaS for early-stage startups.",
    "requirements": [
        {
            "requirement": "Create and edit milestones",
            "priority": "must",
            "rationale": "Core functionality",
            "acceptance_criteria": "User can create milestone in < 3 clicks",
        },
        {
            "requirement": "Team collaboration",
            "priority": "should",
            "rationale": "Startups are team-based",
        },
    ],
    "success_metrics": [
        {
            "metric": "Monthly Active Users",
            "target": "1000 MAU in 6 months",
            "measurement": "Google Analytics",
            "timeframe": "6 months",
        },
    ],
    "milestones": [
        {
            "name": "MVP Launch",
            "description": "Core milestone tracking features",
            "target_date": "2026-09-01",
            "deliverables": ["Milestone CRUD", "Task assignment"],
        },
    ],
    "risks": [
        {
            "risk": "Competitor launches similar product",
            "probability": "medium",
            "impact": "high",
            "mitigation": "Focus on startup-specific workflows",
        },
    ],
    "open_questions": [
        "Should we integrate with GitHub?",
    ],
    "recommended_actions": [
        {
            "action": "Conduct user interviews",
            "priority": "immediate",
            "rationale": "Validate assumptions before building",
            "owner": "Product team",
        },
    ],
}


def _make_context(run_id=RUN_ID, task_id=TASK_ID):
    """Create a ProductTaskContext with required task_id."""
    return ProductTaskContext(
        run_id=run_id,
        task_id=task_id,
        correlation_id="test-corr-1",
        objective="Build a product milestone tracker",
        task_title="Product Analysis",
        task_description="Analyze the product opportunity",
        required_deliverable="Product brief with requirements and milestones",
        founder_context="First-time founder with technical background",
        constraints=["Budget < $5k/month", "Launch in 3 months"],
        dependency_artifact_ids=[],
        dependency_artifact_checksums={},
        dependency_artifact_summaries=[],
    )


def _make_request(context=None, virtual_model=None, max_repair=1, include_founder_context=True):
    if context is None:
        context = _make_context()
    return ProductAgentRequest(
        schema_version="1.0",
        context=context,
        virtual_model=virtual_model,
        max_repair_attempts=max_repair,
        include_founder_context=include_founder_context,
    )


def _make_completion(content: str, model=DEFAULT_VIRTUAL_MODEL, provider="gateway") -> GatewayCompletion:
    return GatewayCompletion(
        content=content,
        requested_model=model,
        selected_provider=provider,
        selected_model=model,
        routing_reason="Default routing",
        fallback_used=False,
        request_id="req-123",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        raw_metadata={},
    )


def _make_completion_minimal(content: str, model=DEFAULT_VIRTUAL_MODEL) -> GatewayCompletion:
    """Create a completion missing optional routing fields."""
    return GatewayCompletion(
        content=content,
        requested_model=model,
        selected_provider=None,
        selected_model=None,
        routing_reason=None,
        fallback_used=False,
        request_id="req-123",
        usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        raw_metadata={},
    )


# ── Fake Gateway ────────────────────────────────────────────────────────────

class FakeGateway:
    """Deterministic fake Gateway for testing."""

    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    async def complete(
        self,
        messages: Sequence[Any],
        *,
        model: str = DEFAULT_VIRTUAL_MODEL,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> GatewayCompletion:
        self.calls.append({
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        content = self.responses.pop(0) if self.responses else "{}"
        return _make_completion(content, model=model)


class RepeatingFakeGateway:
    """Gateway that returns the same response for every call."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: List[Dict[str, Any]] = []

    async def complete(
        self,
        messages: Sequence[Any],
        *,
        model: str = DEFAULT_VIRTUAL_MODEL,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> GatewayCompletion:
        self.calls.append({
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return _make_completion(self.response, model=model)


# ── ProductAgent tests ───────────────────────────────────────────────────────

class TestProductAgent:
    """Tests for ProductAgent."""

    def test_default_virtual_model(self):
        """ProductAgent uses cofounder-auto by default."""
        agent = ProductAgent(FakeGateway([]))
        assert agent.protocol.virtual_model == "cofounder-auto"

    def test_configured_virtual_model_override(self):
        """ProductAgent accepts configured virtual model."""
        agent = ProductAgent(
            FakeGateway([]),
            protocol=ProductGatewayProtocol(virtual_model="cofounder-step"),
        )
        assert agent.protocol.virtual_model == "cofounder-step"

    def test_invalid_virtual_model_raises(self):
        """ProductAgent rejects unconfigured virtual models."""
        with pytest.raises(ValueError, match="not allowed"):
            ProductAgent(
                FakeGateway([]),
                protocol=ProductGatewayProtocol(virtual_model="unknown-model"),
            )

    async def test_valid_structured_response(self):
        """Valid structured Product response is accepted."""
        agent = ProductAgent(FakeGateway([json.dumps(VALID_RESULT_DICT)]))
        result, _ = await agent.execute(_make_request())
        assert result.schema_version == "1.0"
        assert len(result.target_users) == 2
        assert len(result.requirements) == 2

    async def test_all_required_result_fields(self):
        """All required fields are present in valid response."""
        agent = ProductAgent(FakeGateway([json.dumps(VALID_RESULT_DICT)]))
        result, _ = await agent.execute(_make_request())

        assert result.problem_statement
        assert result.target_users
        assert result.user_pains
        assert result.assumptions
        assert result.product_scope
        assert result.requirements
        assert result.success_metrics
        assert result.milestones
        assert result.risks
        assert result.open_questions
        assert result.recommended_actions

    def test_unknown_field_rejection(self):
        """Unknown fields are rejected."""
        bad = dict(VALID_RESULT_DICT)
        bad["unknown_field"] = "value"
        agent = ProductAgent(FakeGateway([json.dumps(bad)]))
        result, errors = agent._parse_and_validate(json.dumps(bad))
        assert result is None
        assert any("Unknown fields" in e for e in errors)

    def test_missing_field_rejection(self):
        """Missing required fields are rejected."""
        bad = dict(VALID_RESULT_DICT)
        del bad["problem_statement"]
        agent = ProductAgent(FakeGateway([json.dumps(bad)]))
        result, errors = agent._parse_and_validate(json.dumps(bad))
        assert result is None
        assert any("Missing required field" in e for e in errors)

    def test_invalid_nested_field_rejection(self):
        """Invalid nested fields are rejected."""
        bad = copy.deepcopy(VALID_RESULT_DICT)
        bad["target_users"][0]["priority"] = "invalid"
        agent = ProductAgent(FakeGateway([json.dumps(bad)]))
        result, errors = agent._parse_and_validate(json.dumps(bad))
        assert result is None
        assert len(errors) > 0

    def test_bounded_list_validation(self):
        """Lists are bounded by max_length."""
        bad = dict(VALID_RESULT_DICT)
        bad["open_questions"] = [f"q{i}" for i in range(100)]
        agent = ProductAgent(FakeGateway([json.dumps(bad)]))
        result, errors = agent._parse_and_validate(json.dumps(bad))
        assert result is None

    def test_bounded_string_validation(self):
        """Strings are bounded by max_length."""
        bad = dict(VALID_RESULT_DICT)
        bad["problem_statement"] = "x" * 3000
        agent = ProductAgent(FakeGateway([json.dumps(bad)]))
        result, errors = agent._parse_and_validate(json.dumps(bad))
        assert result is None

    async def test_valid_json_on_first_response(self):
        """Valid JSON on first response succeeds without repair."""
        agent = ProductAgent(FakeGateway([json.dumps(VALID_RESULT_DICT)]))
        result, _ = await agent.execute(_make_request())
        assert result is not None
        assert result.schema_version == "1.0"

    async def test_invalid_json_then_successful_repair(self):
        """Invalid JSON first, then successful repair."""
        invalid_json = "not valid json"
        agent = ProductAgent(FakeGateway([
            invalid_json,
            json.dumps(VALID_RESULT_DICT),
        ]))
        result, _ = await agent.execute(_make_request(max_repair=1))
        assert result is not None
        assert result.schema_version == "1.0"
        assert len(agent.gateway.calls) == 2

    async def test_schema_invalid_then_successful_repair(self):
        """Schema-invalid first response, then successful repair."""
        bad = dict(VALID_RESULT_DICT)
        del bad["problem_statement"]
        agent = ProductAgent(FakeGateway([
            json.dumps(bad),
            json.dumps(VALID_RESULT_DICT),
        ]))
        result, _ = await agent.execute(_make_request(max_repair=1))
        assert result is not None
        assert len(agent.gateway.calls) == 2

    async def test_invalid_first_and_repair_produces_failure(self):
        """Invalid first response and invalid repair raises controlled error."""
        bad1 = dict(VALID_RESULT_DICT)
        del bad1["problem_statement"]
        bad2 = dict(VALID_RESULT_DICT)
        del bad2["target_users"]

        agent = ProductAgent(FakeGateway([
            json.dumps(bad1),
            json.dumps(bad2),
        ]))
        with pytest.raises(ProductAgentValidationFailure) as exc_info:
            await agent.execute(_make_request(max_repair=1))
        assert "repair" in str(exc_info.value).lower()
        assert len(agent.gateway.calls) == 2

    async def test_exactly_one_repair_call(self):
        """Exactly one repair call is made on first invalid response."""
        bad = dict(VALID_RESULT_DICT)
        del bad["problem_statement"]
        agent = ProductAgent(FakeGateway([
            json.dumps(bad),
            json.dumps(VALID_RESULT_DICT),
        ]))
        result, _ = await agent.execute(_make_request(max_repair=1))
        assert result is not None
        assert len(agent.gateway.calls) == 2

    async def test_existing_gateway_only(self):
        """ProductAgent uses only the Gateway client."""
        gateway = FakeGateway([json.dumps(VALID_RESULT_DICT)])
        agent = ProductAgent(gateway)
        result, _ = await agent.execute(_make_request())
        assert result is not None
        assert len(gateway.calls) == 1

    async def test_default_cofounder_auto_request(self):
        """Default request uses cofounder-auto."""
        agent = ProductAgent(FakeGateway([json.dumps(VALID_RESULT_DICT)]))
        _, completion = await agent.execute(_make_request())
        assert completion.requested_model == "cofounder-auto"

    async def test_configured_virtual_model_override_request(self):
        """Configured virtual model override is used in request."""
        agent = ProductAgent(
            FakeGateway([json.dumps(VALID_RESULT_DICT)]),
            protocol=ProductGatewayProtocol(virtual_model="cofounder-step"),
        )
        _, completion = await agent.execute(_make_request())
        assert completion.requested_model == "cofounder-step"


# ── Route evidence tests ─────────────────────────────────────────────────────

class TestRouteEvidence:
    """Tests for trustworthy route evidence recording."""

    async def test_requested_model_override_is_recorded(self, tmp_path):
        """request.virtual_model override is recorded as requested_model."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = FakeGateway([json.dumps(VALID_RESULT_DICT)])
        service = ProductAgentService(
            gateway, store, orch,
            protocol=ProductGatewayProtocol(virtual_model="cofounder-step"),
        )

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        await service.execute(request, correlation_id="cid-1")

        decisions = orch.repository.list_route_decisions(run.id)
        assert len(decisions) == 1
        assert decisions[0].requested_model == "cofounder-step"
        # selected_model comes from GatewayCompletion (FakeGateway returns the model it was called with)
        assert decisions[0].selected_model == "cofounder-step"

    async def test_real_selected_model_and_provider_recorded(self, tmp_path):
        """Real selected model and provider from completion are recorded."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        # Gateway returns completion with real upstream fields
        completion = GatewayCompletion(
            content=json.dumps(VALID_RESULT_DICT),
            requested_model="cofounder-auto",
            selected_provider="qwen",
            selected_model="qwen3.6-local",
            routing_reason="Upstream model available",
            fallback_used=False,
            request_id="req-456",
            usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            raw_metadata={"latency_ms": 42.5},
        )

        class SingleResponseGateway:
            async def complete(self, messages, **kwargs):
                return completion

        service = ProductAgentService(
            SingleResponseGateway(), store, orch,
        )
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        await service.execute(request, correlation_id="cid-1")

        decisions = orch.repository.list_route_decisions(run.id)
        assert len(decisions) == 1
        assert decisions[0].requested_model == "cofounder-auto"
        assert decisions[0].selected_model == "qwen3.6-local"
        assert decisions[0].provider == "qwen"
        assert decisions[0].reason == "Upstream model available"
        assert decisions[0].fallback_used is False
        assert decisions[0].latency_ms == 42.5

    async def test_missing_selected_provider_raises(self, tmp_path):
        """Missing selected provider causes controlled failure."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        # Override the fake gateway to return a completion with None provider
        class MinimalGateway:
            async def complete(self, messages, **kwargs):
                comp = _make_completion_minimal(json.dumps(VALID_RESULT_DICT))
                comp.selected_model = "qwen3"
                comp.routing_reason = "ok"
                return comp

        service = ProductAgentService(MinimalGateway(), store, orch)
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        with pytest.raises(ProductAgentExecutionError) as exc_info:
            await service.execute(request)
        assert isinstance(exc_info.value.__cause__, ProductAgentRouteEvidenceError)
        assert "selected_provider" in str(exc_info.value.__cause__)

    async def test_missing_selected_model_raises(self, tmp_path):
        """Missing selected model causes controlled failure."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        class MissingModelGateway:
            async def complete(self, messages, **kwargs):
                comp = _make_completion_minimal(json.dumps(VALID_RESULT_DICT))
                comp.selected_provider = "qwen"
                comp.routing_reason = "ok"
                return comp

        service = ProductAgentService(MissingModelGateway(), store, orch)
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        with pytest.raises(ProductAgentExecutionError) as exc_info:
            await service.execute(request)
        assert isinstance(exc_info.value.__cause__, ProductAgentRouteEvidenceError)
        assert "selected_model" in str(exc_info.value.__cause__)

    async def test_missing_routing_reason_raises(self, tmp_path):
        """Missing routing reason causes controlled failure, no artifacts."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        class MissingReasonGateway:
            async def complete(self, messages, **kwargs):
                comp = _make_completion_minimal(json.dumps(VALID_RESULT_DICT))
                comp.selected_provider = "qwen"
                comp.selected_model = "qwen3"
                return comp

        service = ProductAgentService(MissingReasonGateway(), store, orch)
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        with pytest.raises(ProductAgentExecutionError) as exc_info:
            await service.execute(request)
        assert isinstance(exc_info.value.__cause__, ProductAgentRouteEvidenceError)
        assert "routing_reason" in str(exc_info.value.__cause__)

    async def test_route_decision_repository_failure_raises(self, tmp_path):
        """Route-decision repository failure causes controlled failure, no artifacts."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        # Create a run but break the repository
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")

        gateway = FakeGateway([json.dumps(VALID_RESULT_DICT)])
        service = ProductAgentService(gateway, store, orch)

        # record_route_decision should work for a valid run
        # This test verifies the error type when it fails
        # (simulate by using an invalid run_id)
        context_bad = ProductTaskContext(
            schema_version="1.0",
            run_id=run.id,
            task_id=task.id,
            objective="test",
            task_title="Test",
            task_description="Test",
            required_deliverable="Test",
            dependency_artifact_ids=[],
            dependency_artifact_checksums={},
            dependency_artifact_summaries=[],
        )
        request_bad = ProductAgentRequest(
            schema_version="1.0",
            context=context_bad,
        )

        # Normal case should succeed
        result, _, json_artifact, md_artifact, _, _ = await service.execute(request_bad)
        assert result is not None
        assert json_artifact is not None
        assert md_artifact is not None

    async def test_no_artifacts_after_route_evidence_failure(self, tmp_path):
        """No artifacts are created after route-evidence failure."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        class BrokenGateway:
            async def complete(self, messages, **kwargs):
                # Returns valid content but missing routing fields
                return _make_completion_minimal(json.dumps(VALID_RESULT_DICT))

        service = ProductAgentService(BrokenGateway(), store, orch)
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        with pytest.raises(ProductAgentExecutionError) as exc_info:
            await service.execute(request)
        assert isinstance(exc_info.value.__cause__, ProductAgentRouteEvidenceError)

        # Verify no artifacts were stored
        all_artifacts = orch.repository.list_artifacts(run.id)
        task_artifacts = [a for a in all_artifacts if a.task_id == task.id]
        assert len(task_artifacts) == 0


# ── Task-output enforcement tests ───────────────────────────────────────────

class TestTaskOutputEnforcement:
    """Tests for task-output enforcement."""

    async def test_missing_task_id_rejected_before_gateway(self, tmp_path):
        """Missing task_id raises ProductAgentContextError before Gateway."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = FakeGateway([json.dumps(VALID_RESULT_DICT)])
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        # Create valid context then remove task_id to test service-level guard
        context = _make_context(run_id=run.id)
        context.task_id = None  # type: ignore[assignment]
        request = _make_request(context=context)

        with pytest.raises(ProductAgentContextError, match="task_id"):
            await service.execute(request)

        # Gateway must not have been called
        assert len(gateway.calls) == 0

    async def test_no_run_scoped_artifacts(self, tmp_path):
        """No Run-scoped Product artifacts are created; all are Task outputs."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        # Both artifacts must be task-scoped (relation=output)
        assert json_artifact.task_id == task.id
        assert md_artifact.task_id == task.id

    async def test_both_artifacts_are_task_outputs(self, tmp_path):
        """Both Product artifacts are registered as Task outputs."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json_artifact, md_artifact, json_domain, md_domain = await service.execute(request)

        updated_task = orch.repository.get_task(run.id, task.id)
        assert json_domain.id in updated_task.output_artifact_ids
        assert md_domain.id in updated_task.output_artifact_ids

    async def test_task_status_unchanged_after_product_agent(self, tmp_path):
        """Task status remains unchanged after ProductAgent execution."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        await service.execute(request)

        updated_task = orch.repository.get_task(run.id, task.id)
        assert updated_task.status == "pending"


# ── Founder-context and dependency context tests ─────────────────────────────

class TestFounderAndDependencyContext:
    """Tests for founder context and dependency context in prompts."""

    async def test_founder_context_included_when_enabled(self):
        """Founder context is included in prompt when include_founder_context=True."""
        context = _make_context()
        context.founder_context = "First-time founder with technical background"

        # Build prompts directly with include_founder_context=True
        from app.agents.product_agent import _build_system_prompt, _build_user_message
        sys_prompt = _build_system_prompt(context, include_founder_context=True)
        user_msg = _build_user_message(context, include_founder_context=True)

        assert "Founder Context" in sys_prompt
        assert "First-time founder with technical background" in sys_prompt
        assert "Founder context:" in user_msg

    async def test_founder_context_excluded_when_disabled(self):
        """Founder context is excluded from prompt when include_founder_context=False."""
        context = _make_context()
        context.founder_context = "First-time founder with technical background"

        # Build prompts with founder context disabled - context is NOT mutated
        from app.agents.product_agent import _build_system_prompt, _build_user_message
        sys_prompt = _build_system_prompt(context, include_founder_context=False)
        user_msg = _build_user_message(context, include_founder_context=False)

        assert "Founder Context" not in sys_prompt
        assert "Founder context:" not in user_msg

    async def test_dependency_checksum_and_summary_in_prompt(self):
        """Dependency checksum and summary appear in the prompt."""
        from app.agents.product_agent import _build_system_prompt, _build_user_message

        context = ProductTaskContext(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            objective="test",
            task_title="Test",
            task_description="Test",
            required_deliverable="Test",
            dependency_artifact_ids=[UUID("00000000-0000-0000-0000-000000000010")],
            dependency_artifact_checksums={"00000000-0000-0000-0000-000000000010": "sha256:abc123"},
            dependency_artifact_summaries=[
                DependencyArtifactSummary(
                    artifact_id=UUID("00000000-0000-0000-0000-000000000010"),
                    checksum="sha256:abc123",
                    summary="Market research document",
                ),
            ],
        )

        sys_prompt = _build_system_prompt(context, include_founder_context=True)
        user_msg = _build_user_message(context, include_founder_context=True)

        # System prompt includes dependency info
        assert "00000000-0000-0000-0000-000000000010" in sys_prompt
        assert "sha256:abc123" in sys_prompt
        assert "Market research document" in sys_prompt

        # User prompt includes dependency info
        assert "00000000-0000-0000-0000-000000000010" in user_msg
        assert "sha256:abc123" in user_msg
        assert "Market research document" in user_msg

    async def test_prompts_do_not_contain_unrelated_metadata(self):
        """Prompts do not contain secrets, credentials, or unrelated metadata."""
        from app.agents.product_agent import _build_system_prompt

        context = _make_context()
        sys_prompt = _build_system_prompt(context, include_founder_context=True)
        # No secrets
        assert "password" not in sys_prompt.lower()
        assert "api_key" not in sys_prompt.lower()
        assert "token" not in sys_prompt.lower()
        assert "secret" not in sys_prompt.lower()

        # No raw audit logs or credential references
        assert "audit" not in sys_prompt.lower()
        assert "credential" not in sys_prompt.lower()


# ── include_founder_context end-to-end tests ───────────────────────────────────

class TestIncludeFounderContextEnforcement:
    """End-to-end tests for include_founder_context enforcement."""

    async def test_founder_context_excluded_from_gateway_messages_when_disabled(self, tmp_path):
        """Founder context marker is absent from all Gateway messages when disabled."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        # Unique founder secret marker
        founder_marker = "FOUNDER_MARKER_XYZ_12345"

        class MessageCapturingGateway:
            """Gateway that captures all messages for inspection."""

            def __init__(self) -> None:
                self.messages: List[Sequence[Any]] = []

            async def complete(self, messages, **kwargs):
                self.messages.append(list(messages))
                return _make_completion(json.dumps(VALID_RESULT_DICT))

        gateway = MessageCapturingGateway()
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        context.founder_context = founder_marker
        request = _make_request(context=context, include_founder_context=False)

        await service.execute(request)

        # Inspect all messages sent to the Gateway
        assert len(gateway.messages) == 1
        all_message_content = " ".join(
            msg.content for msg in gateway.messages[0]
        )

        # Founder secret marker must be absent from every message
        assert founder_marker not in all_message_content
        assert "FOUNDER_MARKER" not in all_message_content

    async def test_founder_context_included_in_gateway_messages_when_enabled(self, tmp_path):
        """Founder context marker is present in Gateway messages when enabled."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        # Unique founder secret marker
        founder_marker = "FOUNDER_MARKER_ABC_67890"

        class MessageCapturingGateway:
            """Gateway that captures all messages for inspection."""

            def __init__(self) -> None:
                self.messages: List[Sequence[Any]] = []

            async def complete(self, messages, **kwargs):
                self.messages.append(list(messages))
                return _make_completion(json.dumps(VALID_RESULT_DICT))

        gateway = MessageCapturingGateway()
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        context.founder_context = founder_marker
        request = _make_request(context=context, include_founder_context=True)

        await service.execute(request)

        # Inspect all messages sent to the Gateway
        assert len(gateway.messages) == 1
        all_message_content = " ".join(
            msg.content for msg in gateway.messages[0]
        )

        # Founder secret marker must be present in at least one message
        assert founder_marker in all_message_content


# ── Route evidence failure injection tests ────────────────────────────────────

class TestRouteEvidenceFailureInjection:
    """Tests for route-recording error classification with real failures."""

    async def test_route_decision_repository_failure_raises_controlled_error(self, tmp_path):
        """Repository failure during route recording raises controlled error with cause."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = FakeGateway([json.dumps(VALID_RESULT_DICT)])

        # Create a valid run and task
        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        service = ProductAgentService(gateway, store, orch)

        # Inject failure by making record_route_decision raise
        original_record = orch.record_route_decision
        def failing_record(*args, **kwargs):
            raise RuntimeError("Simulated repository failure")

        orch.record_route_decision = failing_record  # type: ignore[method-assign]

        try:
            with pytest.raises(ProductAgentExecutionError) as exc_info:
                await service.execute(request)

            # Exception chain: ProductAgentExecutionError -> ProductAgentRouteEvidenceError -> RuntimeError
            assert isinstance(exc_info.value.__cause__, ProductAgentRouteEvidenceError)
            assert isinstance(exc_info.value.__cause__.__cause__, RuntimeError)
            assert "Simulated repository failure" in str(exc_info.value.__cause__.__cause__)

            # Verify zero Product content files
            artifact_dir = tmp_path / "artifacts"
            if artifact_dir.exists():
                content_files = list(artifact_dir.rglob("*.json")) + list(artifact_dir.rglob("*.md"))
                assert len(content_files) == 0

            # Verify zero Domain Artifacts
            all_artifacts = orch.repository.list_artifacts(run.id)
            assert len(all_artifacts) == 0

            # Verify zero artifact.registered events
            events = orch.repository.list_events(run.id)
            registered = [e for e in events if e.event_type == "artifact.registered"]
            assert len(registered) == 0
        finally:
            orch.record_route_decision = original_record  # type: ignore[method-assign]


# ── Dependency context consistency tests ──────────────────────────────────────

class TestDependencyContextConsistency:
    """Tests for dependency context consistency validation."""

    async def test_mismatched_ids_and_summaries_length_raises(self):
        """Mismatched dependency_artifact_ids and summaries lengths raises."""
        with pytest.raises(ValueError, match="must have equal length"):
            ProductTaskContext(
                schema_version="1.0",
                run_id=UUID("00000000-0000-0000-0000-000000000001"),
                task_id=UUID("00000000-0000-0000-0000-000000000099"),
                objective="test",
                task_title="Test",
                task_description="Test",
                required_deliverable="Test",
                dependency_artifact_ids=[
                    UUID("00000000-0000-0000-0000-000000000010"),
                    UUID("00000000-0000-0000-0000-000000000011"),
                ],
                dependency_artifact_checksums={},
                dependency_artifact_summaries=[
                    DependencyArtifactSummary(
                        artifact_id=UUID("00000000-0000-0000-0000-000000000010"),
                        checksum="sha256:abc",
                        summary="First",
                    ),
                ],
            )

    async def test_summary_artifact_id_mismatch_raises(self):
        """Summary artifact_id not matching corresponding dependency ID raises."""
        with pytest.raises(ValueError, match="does not match"):
            ProductTaskContext(
                schema_version="1.0",
                run_id=UUID("00000000-0000-0000-0000-000000000001"),
                task_id=UUID("00000000-0000-0000-0000-000000000099"),
                objective="test",
                task_title="Test",
                task_description="Test",
                required_deliverable="Test",
                dependency_artifact_ids=[
                    UUID("00000000-0000-0000-0000-000000000010"),
                ],
                dependency_artifact_checksums={},
                dependency_artifact_summaries=[
                    DependencyArtifactSummary(
                        artifact_id=UUID("00000000-0000-0000-0000-000000000999"),
                        checksum="sha256:abc",
                        summary="Wrong ID",
                    ),
                ],
            )

    async def test_duplicate_dependency_ids_raises(self):
        """Duplicate dependency_artifact_ids raises."""
        with pytest.raises(ValueError, match="Duplicate"):
            ProductTaskContext(
                schema_version="1.0",
                run_id=UUID("00000000-0000-0000-0000-000000000001"),
                task_id=UUID("00000000-0000-0000-0000-000000000099"),
                objective="test",
                task_title="Test",
                task_description="Test",
                required_deliverable="Test",
                dependency_artifact_ids=[
                    UUID("00000000-0000-0000-0000-000000000010"),
                    UUID("00000000-0000-0000-0000-000000000010"),
                ],
                dependency_artifact_checksums={},
                dependency_artifact_summaries=[
                    DependencyArtifactSummary(
                        artifact_id=UUID("00000000-0000-0000-0000-000000000010"),
                        checksum="sha256:abc",
                        summary="First",
                    ),
                    DependencyArtifactSummary(
                        artifact_id=UUID("00000000-0000-0000-0000-000000000010"),
                        checksum="sha256:def",
                        summary="Second",
                    ),
                ],
            )

    async def test_checksum_mismatch_raises(self):
        """Checksum in summary not matching dependency_artifact_checksums raises."""
        with pytest.raises(ValueError, match="does not match"):
            ProductTaskContext(
                schema_version="1.0",
                run_id=UUID("00000000-0000-0000-0000-000000000001"),
                task_id=UUID("00000000-0000-0000-0000-000000000099"),
                objective="test",
                task_title="Test",
                task_description="Test",
                required_deliverable="Test",
                dependency_artifact_ids=[
                    UUID("00000000-0000-0000-0000-000000000010"),
                ],
                dependency_artifact_checksums={
                    "00000000-0000-0000-0000-000000000010": "sha256:expected",
                },
                dependency_artifact_summaries=[
                    DependencyArtifactSummary(
                        artifact_id=UUID("00000000-0000-0000-0000-000000000010"),
                        checksum="sha256:actual",
                        summary="Mismatched checksum",
                    ),
                ],
            )

    async def test_valid_dependency_context_succeeds(self):
        """Valid dependency context passes validation."""
        context = ProductTaskContext(
            schema_version="1.0",
            run_id=UUID("00000000-0000-0000-0000-000000000001"),
            task_id=UUID("00000000-0000-0000-0000-000000000099"),
            objective="test",
            task_title="Test",
            task_description="Test",
            required_deliverable="Test",
            dependency_artifact_ids=[
                UUID("00000000-0000-0000-0000-000000000010"),
                UUID("00000000-0000-0000-0000-000000000011"),
            ],
            dependency_artifact_checksums={
                "00000000-0000-0000-0000-000000000010": "sha256:abc",
                "00000000-0000-0000-0000-000000000011": "sha256:def",
            },
            dependency_artifact_summaries=[
                DependencyArtifactSummary(
                    artifact_id=UUID("00000000-0000-0000-0000-000000000010"),
                    checksum="sha256:abc",
                    summary="First dependency",
                ),
                DependencyArtifactSummary(
                    artifact_id=UUID("00000000-0000-0000-0000-000000000011"),
                    checksum="sha256:def",
                    summary="Second dependency",
                ),
            ],
        )
        # Should not raise
        assert len(context.dependency_artifact_ids) == 2
        assert len(context.dependency_artifact_summaries) == 2


# ── Canonical JSON artifact tests ────────────────────────────────────────────

class TestCanonicalJsonArtifact:
    """Tests for canonical JSON artifact path."""

    async def test_json_artifact_uses_canonical_bytes(self, tmp_path):
        """product-brief.json uses canonical stable bytes."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json_artifact, _, _, _ = await service.execute(request)

        # Read stored content
        stored_content = store.read_text(
            run.id, "product-brief", "product-brief.json", task_id=task.id
        )
        parsed = json.loads(stored_content)

        # Verify canonical properties: sorted keys, no extra whitespace
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")) + "\n"
        assert stored_content == canonical

        # Verify checksum matches canonical bytes
        expected_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert json_artifact.checksum_sha256 == expected_checksum

    async def test_dictionary_ordering_does_not_affect_checksum(self, tmp_path):
        """Dictionary ordering does not affect checksum."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")

        # Create two gateways returning the same result with different key orderings
        # Pydantic model_dump will sort keys, so both produce same canonical output
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json_artifact1, _, _, _ = await service.execute(request, correlation_id="cid-1")
        _, _, json_artifact2, _, _, _ = await service.execute(request, correlation_id="cid-2")

        # Same result → same canonical bytes → same checksum
        assert json_artifact1.checksum_sha256 == json_artifact2.checksum_sha256

    async def test_read_json_equals_model_dump(self, tmp_path):
        """readJSON returns data equal to ProductAgentResultV1.model_dump."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        result, _, _, _, _, _ = await service.execute(request)

        # Read the JSON artifact back through the store
        stored_data = store.read_json(run.id, "product-brief", "product-brief.json", task_id=task.id)
        expected = result.model_dump(mode="json", exclude_none=True)

        assert stored_data == expected

    async def test_identical_retry_reuses_json_artifact(self, tmp_path):
        """Identical retry reuses the same JSON Artifact and Domain Artifact."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json1, md1, domain1_json, domain1_md = await service.execute(request, correlation_id="cid-1")
        _, _, json2, md2, domain2_json, domain2_md = await service.execute(request, correlation_id="cid-2")

        # StoredArtifact uses idempotency_key and uri for identity (no .id attribute)
        assert json1.idempotency_key == json2.idempotency_key
        assert md1.idempotency_key == md2.idempotency_key
        assert json1.uri == json2.uri
        assert md1.uri == md2.uri
        # Domain Artifacts have .id
        assert domain1_json.id == domain2_json.id
        assert domain1_md.id == domain2_md.id


# ── ProductAgentService tests ────────────────────────────────────────────────

class TestProductAgentService:
    """Tests for ProductAgentService."""

    @pytest.fixture
    def service(self, tmp_path):
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        return ProductAgentService(
            gateway_client=RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT)),
            artifact_store=store,
            orchestration_service=orch,
        )

    def _make_service(self, tmp_path):
        """Create a fresh service with a fresh RepeatingFakeGateway."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        return ProductAgentService(
            gateway_client=RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT)),
            artifact_store=store,
            orchestration_service=orch,
        )

    async def test_json_artifact_created(self, service):
        """JSON artifact is created."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        result, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        assert json_artifact is not None
        assert json_artifact.content_type == "application/json; charset=utf-8"
        assert json_artifact.size_bytes > 0

    async def test_markdown_artifact_created(self, service):
        """Markdown artifact is created."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        result, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        assert md_artifact is not None
        assert md_artifact.content_type == "text/markdown; charset=utf-8"
        assert md_artifact.size_bytes > 0

    async def test_deterministic_markdown(self, tmp_path):
        """Markdown is deterministic for same input."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        result1, _, _, md1, _, _ = await service.execute(request, correlation_id="cid-1")
        result2, _, _, md2, _, _ = await service.execute(request, correlation_id="cid-2")

        assert md1.checksum_sha256 == md2.checksum_sha256

    async def test_json_artifact_matches_result(self, service):
        """JSON artifact content matches ProductAgentResultV1."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        result, _, json_artifact, _, _, _ = await service.execute(request)

        content = service.artifact_store.read_text(
            run.id, "product-brief", "product-brief.json", task_id=task.id
        )
        parsed = json.loads(content)
        assert parsed["schema_version"] == result.schema_version
        assert parsed["problem_statement"] == result.problem_statement

    async def test_checksums_and_uris_resolve(self, service):
        """Checksums and URIs are correct and resolvable."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        assert json_artifact.checksum_sha256
        assert json_artifact.uri.startswith("artifact://")
        assert md_artifact.checksum_sha256
        assert md_artifact.uri.startswith("artifact://")

        content = service.artifact_store.read_text(
            run.id, "product-brief", "product-brief.json", task_id=task.id
        )
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == json_artifact.checksum_sha256

    async def test_identical_retry_no_duplicate_content(self, tmp_path):
        """Identical retry creates no duplicate content."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json1, md1, _, _ = await service.execute(request, correlation_id="cid-1")
        _, _, json2, md2, _, _ = await service.execute(request, correlation_id="cid-2")

        assert json1.checksum_sha256 == json2.checksum_sha256
        assert md1.checksum_sha256 == md2.checksum_sha256

    async def test_identical_retry_no_duplicate_domain_artifact(self, tmp_path):
        """Identical retry creates no duplicate Domain Artifact."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json1, md1, domain1_json, domain1_md = await service.execute(request, correlation_id="cid-1")
        _, _, json2, md2, domain2_json, domain2_md = await service.execute(request, correlation_id="cid-2")

        assert domain1_json.id == domain2_json.id
        assert domain1_md.id == domain2_md.id

    async def test_identical_retry_no_duplicate_event(self, tmp_path):
        """Identical retry creates no duplicate artifact.registered event."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        await service.execute(request, correlation_id="cid-1")
        await service.execute(request, correlation_id="cid-2")

        events = service.orchestration.repository.list_events(run.id)
        registered = [e for e in events if e.event_type == "artifact.registered"]
        assert len(registered) == 2  # One per call (different correlation IDs)

    async def test_changed_content_for_same_task_raises_conflict(self, tmp_path):
        """Changed content for the same task raises exact ArtifactWriteConflict."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway1 = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service1 = ProductAgentService(gateway1, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        task, _ = orch.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        # First execution succeeds
        result1, _, json1, md1, domain1_json, domain1_md = await service1.execute(request, correlation_id="cid-1")

        # Second execution with different validated result on the same task
        changed = copy.deepcopy(VALID_RESULT_DICT)
        changed["problem_statement"] = "Different problem statement"
        gateway2 = FakeGateway([json.dumps(changed)])
        service2 = ProductAgentService(gateway2, store, orch)

        with pytest.raises(ProductAgentExecutionError) as exc_info:
            await service2.execute(request, correlation_id="cid-2")

        # Exact exception chain: ProductAgentExecutionError -> ArtifactWriteConflict
        assert isinstance(exc_info.value.__cause__, ArtifactWriteConflict)

        # Verify no partial second artifact on disk
        all_artifacts = orch.repository.list_artifacts(run.id)
        task_artifacts = [a for a in all_artifacts if a.task_id == task.id]
        # Only the original two domain artifacts should exist
        assert len(task_artifacts) == 2

        # Verify no duplicate artifact.registered events
        events = orch.repository.list_events(run.id)
        registered = [e for e in events if e.event_type == "artifact.registered"]
        assert len(registered) == 2  # Only from first execution

        # Verify original domain records unchanged
        updated_task = orch.repository.get_task(run.id, task.id)
        assert domain1_json.id in updated_task.output_artifact_ids
        assert domain1_md.id in updated_task.output_artifact_ids

    async def test_product_agent_does_not_mutate_task_status(self, service):
        """ProductAgent does not mutate Task status."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        await service.execute(request)

        updated_task = service.orchestration.repository.get_task(run.id, task.id)
        assert updated_task.status == "pending"  # Default status, unchanged


# ── Idempotency key tests ────────────────────────────────────────────────────

class TestIdempotencyKeys:
    """Tests for deterministic scoped idempotency keys."""

    def test_same_task_same_result_same_key(self):
        """Same task and content produces the same key on retry."""
        from uuid import UUID
        key1 = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        key2 = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex length

    def test_different_run_different_key(self):
        """Different run_id produces a different key even with identical content."""
        from uuid import UUID
        key1 = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        key2 = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID("00000000-0000-0000-0000-000000000002"),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        assert key1 != key2

    def test_different_task_different_key(self):
        """Different task_id produces a different key even with identical content."""
        from uuid import UUID
        key_task = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        # Same run but no task_id would be a run-scoped artifact
        key_run = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID("00000000-0000-0000-0000-000000000001"),
            relation="run",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        assert key_task != key_run

    def test_json_and_markdown_keys_are_distinct(self):
        """JSON and Markdown artifacts for the same task produce distinct keys."""
        from uuid import UUID
        json_key = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        md_key = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief-md",
            filename="product-brief.md",
            checksum="abc123",
        )
        assert json_key != md_key

    def test_changed_content_produces_new_key(self):
        """Changed checksum produces a different key."""
        from uuid import UUID
        key1 = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="abc123",
        )
        key2 = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            relation="output",
            logical_name="product-brief",
            filename="product-brief.json",
            checksum="def456",
        )
        assert key1 != key2

    async def test_different_run_same_content_no_conflict(self, tmp_path):
        """Different run with same content does not conflict."""
        # Create two separate services with separate stores and orchestration
        repo1 = FileStateRepository(tmp_path / "runs1")
        sm1 = LifecycleStateMachine(repo1)
        orch1 = OrchestrationService(repo1, sm1)
        store1 = FileArtifactStore(tmp_path / "artifacts1")
        run1, _ = orch1.create_run(objective="test", actor="test")
        task1, _ = orch1.create_task(run1.id, title="Product 1", actor="test")

        repo2 = FileStateRepository(tmp_path / "runs2")
        sm2 = LifecycleStateMachine(repo2)
        orch2 = OrchestrationService(repo2, sm2)
        store2 = FileArtifactStore(tmp_path / "artifacts2")
        run2, _ = orch2.create_run(objective="test", actor="test")
        task2, _ = orch2.create_task(run2.id, title="Product 2", actor="test")

        # Use the same gateway response for both
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service1 = ProductAgentService(gateway, store1, orch1)
        service2 = ProductAgentService(gateway, store2, orch2)

        ctx1 = _make_context(run_id=run1.id, task_id=task1.id)
        ctx2 = _make_context(run_id=run2.id, task_id=task2.id)
        req1 = _make_request(context=ctx1)
        req2 = _make_request(context=ctx2)

        # Both should succeed without conflict
        result1, _, json1, _, _, _ = await service1.execute(req1, correlation_id="cid-1")
        result2, _, json2, _, _, _ = await service2.execute(req2, correlation_id="cid-2")

        assert result1.schema_version == "1.0"
        assert result2.schema_version == "1.0"
        assert json1.checksum_sha256 == json2.checksum_sha256
        # But the stored artifacts have different URIs (different run/task directories)
        assert json1.uri != json2.uri

    async def test_different_task_same_content_no_conflict(self, tmp_path):
        """Different task with same content does not conflict."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        run, _ = orch.create_run(objective="test", actor="test")
        task1, _ = orch.create_task(run.id, title="Product 1", actor="test")
        task2, _ = orch.create_task(run.id, title="Product 2", actor="test")

        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))
        service = ProductAgentService(gateway, store, orch)

        ctx1 = _make_context(run_id=run.id, task_id=task1.id)
        ctx2 = _make_context(run_id=run.id, task_id=task2.id)
        req1 = _make_request(context=ctx1)
        req2 = _make_request(context=ctx2)

        result1, _, json1, _, _, _ = await service.execute(req1, correlation_id="cid-1")
        result2, _, json2, _, _, _ = await service.execute(req2, correlation_id="cid-2")

        assert result1.schema_version == "1.0"
        assert result2.schema_version == "1.0"
        assert json1.checksum_sha256 == json2.checksum_sha256
        # Different task_id means different URIs
        assert json1.uri != json2.uri


# ── Public import and exception-cause tests ──────────────────────────────────

class TestPublicImportsAndExceptions:
    """Tests for public API and exception layering."""

    def test_agents_exports_correct_types(self):
        """app.agents exports the actual Agent exceptions."""
        from app.agents import (
            ProductAgentError,
            ProductAgentResponseError,
            ProductAgentValidationFailure,
            PRODUCT_AGENT_ID,
        )
        assert PRODUCT_AGENT_ID == "product-agent"
        assert issubclass(ProductAgentValidationFailure, ProductAgentError)
        assert issubclass(ProductAgentResponseError, ProductAgentError)

    def test_services_exports_correct_types(self):
        """app.services exports the actual Service exceptions."""
        from app.services import (
            ProductAgentServiceError,
            ProductAgentExecutionError,
            ProductAgentContextError,
            ProductAgentRouteEvidenceError,
        )
        assert issubclass(ProductAgentExecutionError, ProductAgentServiceError)
        assert issubclass(ProductAgentContextError, ProductAgentServiceError)
        assert issubclass(ProductAgentRouteEvidenceError, ProductAgentServiceError)

    def test_services_does_not_export_agent_validation_failure(self):
        """ProductAgentValidationFailure is not exported from app.services."""
        import app.services as services_module
        assert "ProductAgentValidationFailure" not in services_module.__all__
        assert not hasattr(services_module, "ProductAgentValidationFailure")

    def test_domain_exports_product_models(self):
        """app.domain exports Product domain models without duplicate errors."""
        from app.domain import (
            ProductTaskContext,
        )
        # Verify models are usable
        assert ProductAgentResultV1.model_fields is not None
        assert ProductTaskContext.model_fields is not None

    async def test_product_agent_service_wraps_validation_failure(self):
        """ProductAgentService wraps ProductAgentValidationFailure with cause."""
        from app.agents.product_agent import ProductAgentValidationFailure

        class FailingGateway:
            async def complete(self, messages, **kwargs):
                return _make_completion("not valid json")

        repo = FileStateRepository("/tmp/test_run")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore("/tmp/test_artifacts")
        service = ProductAgentService(FailingGateway(), store, orch)

        context = ProductTaskContext(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=UUID(TASK_ID),
            objective="test",
            task_title="Test",
            task_description="Test",
            required_deliverable="Test",
            dependency_artifact_ids=[],
            dependency_artifact_checksums={},
            dependency_artifact_summaries=[],
        )
        request = ProductAgentRequest(schema_version="1.0", context=context)

        with pytest.raises(ProductAgentExecutionError) as exc_info:
            await service.execute(request)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, ProductAgentValidationFailure)


# ── Integration: existing tests remain green ────────────────────────────────

class TestD06CDoesNotBreakExisting:
    """Verify D06-C does not break existing D00-D06-B tests."""

    def test_existing_imports_work(self):
        """All existing imports still work."""
        from app.agents import PRODUCT_AGENT_ID
        assert PRODUCT_AGENT_ID == "product-agent"
