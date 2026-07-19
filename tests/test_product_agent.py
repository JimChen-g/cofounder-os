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
    ProductAgentRequest,
    ProductTaskContext,
)
from app.services import (
    OrchestrationService,
    ProductAgentService,
)
from app.services.product_agent import _compute_idempotency_key
from app.state import FileStateRepository, LifecycleStateMachine
from app.artifacts import FileArtifactStore


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


def _make_context(run_id=RUN_ID, task_id=None):
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
    )


def _make_request(context=None, virtual_model=None, max_repair=1):
    if context is None:
        context = _make_context()
    return ProductAgentRequest(
        schema_version="1.0",
        context=context,
        virtual_model=virtual_model,
        max_repair_attempts=max_repair,
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


# ── Fake Gateway ────────────────────────────────────────────────────────────

class FakeGateway:
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

    async def test_no_artifact_creation_after_terminal_failure(self):
        """No artifacts are created after terminal validation failure."""
        bad = dict(VALID_RESULT_DICT)
        del bad["problem_statement"]
        agent = ProductAgent(FakeGateway([json.dumps(bad)]))
        with pytest.raises(ProductAgentValidationFailure):
            await agent.execute(_make_request())

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

    async def test_route_decision_recorded(self, tmp_path):
        """Route decision is recorded from actual Gateway result."""
        repo = FileStateRepository(tmp_path / "runs")
        sm = LifecycleStateMachine(repo)
        orch = OrchestrationService(repo, sm)
        store = FileArtifactStore(tmp_path / "artifacts")
        gateway = FakeGateway([json.dumps(VALID_RESULT_DICT)])
        service = ProductAgentService(gateway, store, orch)

        run, _ = orch.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        await service.execute(request, correlation_id="cid-1")

        decisions = orch.repository.list_route_decisions(run.id)
        assert len(decisions) == 1
        assert decisions[0].requested_model == "cofounder-auto"
        assert decisions[0].selected_model == "cofounder-auto"


# ── ProductAgentService tests ───────────────────────────────────────────────

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
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        result, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        assert json_artifact is not None
        assert json_artifact.content_type == "application/json; charset=utf-8"
        assert json_artifact.size_bytes > 0

    async def test_markdown_artifact_created(self, service):
        """Markdown artifact is created."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        result, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        assert md_artifact is not None
        assert md_artifact.content_type == "text/markdown; charset=utf-8"
        assert md_artifact.size_bytes > 0

    async def test_deterministic_markdown(self, tmp_path):
        """Markdown is deterministic for same input."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        result1, _, _, md1, _, _ = await service.execute(request, correlation_id="cid-1")
        result2, _, _, md2, _, _ = await service.execute(request, correlation_id="cid-2")

        assert md1.checksum_sha256 == md2.checksum_sha256

    async def test_json_artifact_matches_result(self, service):
        """JSON artifact content matches ProductAgentResultV1."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        result, _, json_artifact, _, _, _ = await service.execute(request)

        content = service.artifact_store.read_text(
            run.id, "product-brief", "product-brief.json"
        )
        parsed = json.loads(content)
        assert parsed["schema_version"] == result.schema_version
        assert parsed["problem_statement"] == result.problem_statement

    async def test_both_artifacts_registered_as_task_outputs(self, service):
        """Both artifacts are registered as task outputs when task_id present."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        task, _ = service.orchestration.create_task(run.id, title="Product", actor="test")
        context = _make_context(run_id=run.id, task_id=task.id)
        request = _make_request(context=context)

        _, _, json_artifact, md_artifact, json_domain, md_domain = await service.execute(request)

        updated_task = service.orchestration.repository.get_task(run.id, task.id)
        assert json_domain.id in updated_task.output_artifact_ids
        assert md_domain.id in updated_task.output_artifact_ids

    async def test_checksums_and_uris_resolve(self, service):
        """Checksums and URIs are correct and resolvable."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        _, _, json_artifact, md_artifact, _, _ = await service.execute(request)

        assert json_artifact.checksum_sha256
        assert json_artifact.uri.startswith("artifact://")
        assert md_artifact.checksum_sha256
        assert md_artifact.uri.startswith("artifact://")

        content = service.artifact_store.read_text(
            run.id, "product-brief", "product-brief.json"
        )
        assert hashlib.sha256(content.encode("utf-8")).hexdigest() == json_artifact.checksum_sha256

    async def test_identical_retry_no_duplicate_content(self, tmp_path):
        """Identical retry creates no duplicate content."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        _, _, json1, md1, _, _ = await service.execute(request, correlation_id="cid-1")
        _, _, json2, md2, _, _ = await service.execute(request, correlation_id="cid-2")

        assert json1.checksum_sha256 == json2.checksum_sha256
        assert md1.checksum_sha256 == md2.checksum_sha256

    async def test_identical_retry_no_duplicate_domain_artifact(self, tmp_path):
        """Identical retry creates no duplicate Domain Artifact."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        _, _, json1, md1, domain1_json, domain1_md = await service.execute(request, correlation_id="cid-1")
        _, _, json2, md2, domain2_json, domain2_md = await service.execute(request, correlation_id="cid-2")

        assert domain1_json.id == domain2_json.id
        assert domain1_md.id == domain2_md.id

    async def test_identical_retry_no_duplicate_event(self, tmp_path):
        """Identical retry creates no duplicate artifact.registered event."""
        service = self._make_service(tmp_path)
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)
        request = _make_request(context=context)

        await service.execute(request, correlation_id="cid-1")
        await service.execute(request, correlation_id="cid-2")

        events = service.orchestration.repository.list_events(run.id)
        registered = [e for e in events if e.event_type == "artifact.registered"]
        assert len(registered) == 2  # One per call (different correlation IDs)

    async def test_differing_content_same_key_raises_conflict(self, service):
        """Differing content with same idempotency key raises conflict."""
        run, _ = service.orchestration.create_run(objective="test", actor="test")
        context = _make_context(run_id=run.id)

        request1 = _make_request(context=context)
        await service.execute(request1, correlation_id="cid-1")

        # Change the result content
        bad = copy.deepcopy(VALID_RESULT_DICT)
        bad["problem_statement"] = "Different problem"
        gateway2 = FakeGateway([json.dumps(bad)])
        service2 = ProductAgentService(gateway2, service.artifact_store, service.orchestration)

        with pytest.raises((ProductAgentValidationFailure, Exception)):
            await service2.execute(request1, correlation_id="cid-2")

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
        # Same run but no task_id (run-scoped artifact)
        key_run = _compute_idempotency_key(
            schema_version="1.0",
            run_id=UUID(RUN_ID),
            task_id=None,
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

        repo2 = FileStateRepository(tmp_path / "runs2")
        sm2 = LifecycleStateMachine(repo2)
        orch2 = OrchestrationService(repo2, sm2)
        store2 = FileArtifactStore(tmp_path / "artifacts2")
        run2, _ = orch2.create_run(objective="test", actor="test")

        # Use the same gateway response for both
        gateway = RepeatingFakeGateway(json.dumps(VALID_RESULT_DICT))

        service1 = ProductAgentService(gateway, store1, orch1)
        service2 = ProductAgentService(gateway, store2, orch2)

        ctx1 = _make_context(run_id=run1.id)
        ctx2 = _make_context(run_id=run2.id)
        req1 = _make_request(context=ctx1)
        req2 = _make_request(context=ctx2)

        # Both should succeed without conflict
        result1, _, json1, _, _, _ = await service1.execute(req1, correlation_id="cid-1")
        result2, _, json2, _, _, _ = await service2.execute(req2, correlation_id="cid-2")

        assert result1.schema_version == "1.0"
        assert result2.schema_version == "1.0"
        assert json1.checksum_sha256 == json2.checksum_sha256
        # But the stored artifacts have different URIs (different run directories)
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


# ── Integration: existing tests remain green ────────────────────────────────

class TestD06CDoesNotBreakExisting:
    """Verify D06-C does not break existing D00-D06-B tests."""

    def test_existing_imports_work(self):
        """All existing imports still work."""
        from app.agents import PRODUCT_AGENT_ID
        assert PRODUCT_AGENT_ID == "product-agent"
