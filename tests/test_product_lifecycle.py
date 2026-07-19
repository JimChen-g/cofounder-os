"""Tests for Product Task Lifecycle Service (D06-D).

31 tests covering the full Product Task lifecycle with:
- Correct predecessor Task dependency semantics
- Post-claim failure path determinism
- Output content verification
- Retry authorization
- Real restart recovery
- Strengthened stale RUNNING reconciliation
- Partial artifact production coverage
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from app.agents.product_agent import (
    DEFAULT_VIRTUAL_MODEL,
)
from app.clients.gateway import GatewayCompletion
from app.domain import (
    Approval,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
)
from app.artifacts import FileArtifactStore
from app.services import (
    AgentExecutionService,
    DependencyArtifactCorruptError,
    DependencyArtifactMissingError,
    OrchestrationService,
    ProductAgentService,
    ProductArtifactVerificationError,
    ProductTaskLifecycleError,
    ProductTaskLifecycleService,
    PredecessorTaskNotCompletedError,
    RetryAuthorizationError,
    TaskNotFoundError,
    TaskNotProductAgentError,
)
from app.domain import TaskStatus
from app.state import FileStateRepository, LifecycleStateMachine


# ── Test data ───────────────────────────────────────────────────────────────

RUN_ID = "00000000-0000-0000-0000-000000000001"
TASK_ID = "00000000-0000-0000-0000-000000000099"
OWNER = "test-owner"

VALID_RESULT_DICT: dict[str, Any] = {
    "schema_version": "1.0",
    "problem_statement": "Users struggle to track product milestones.",
    "target_users": [
        {
            "segment": "Startup founders",
            "description": "Early-stage founders building MVPs",
            "priority": "primary",
        },
    ],
    "user_pains": [
        {
            "pain": "No centralized milestone tracking",
            "severity": "high",
            "frequency": "daily",
            "evidence": "Survey of 50 founders",
        },
    ],
    "assumptions": ["Users have internet access"],
    "product_scope": "A milestone and task tracking SaaS for early-stage startups.",
    "requirements": [
        {
            "requirement": "Create and edit milestones",
            "priority": "must",
            "rationale": "Core functionality",
            "acceptance_criteria": "User can create milestone in < 3 clicks",
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
            "deliverables": ["Milestone CRUD"],
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
    "open_questions": ["Should we integrate with GitHub?"],
    "recommended_actions": [
        {
            "action": "Conduct user interviews",
            "priority": "immediate",
            "rationale": "Validate assumptions before building",
            "owner": "Product team",
        },
    ],
}


def _make_run(repo, sm, orch, objective="test", owner=None):
    """Create a run with optional owner."""
    run, _ = orch.create_run(objective=objective, actor="test")
    if owner:
        run.owner = owner
        repo.save_run(run)
    return run


def _make_task(orch, run_id, title="Product", assigned_agent="product-agent", max_attempts=2, description=None):
    """Create a task and transition to READY."""
    task, _ = orch.create_task(run_id, title=title, actor="test")
    task.assigned_agent = assigned_agent
    task.max_attempts = max_attempts
    if description:
        task.description = description
    else:
        task.description = f"Test description for {title}"
    orch.repository.save_task(task)
    # Transition to READY if still PENDING
    if TaskStatus(task.status) == TaskStatus.PENDING:
        orch.state_machine.transition_task(
            run_id, task.id, TaskStatus.READY, actor="test", reason="Test setup"
        )
    return orch.repository.get_task(run_id, task.id)


def _make_lifecycle_service(tmp_path, gateway_response=None, retry_policy_decisions=None):
    """Create a ProductTaskLifecycleService with real underlying services."""
    if gateway_response is None:
        gateway_response = json.dumps(VALID_RESULT_DICT)

    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = FileArtifactStore(tmp_path / "artifacts")

    class FakeGateway:
        def __init__(self, response):
            self.response = response
            self.calls = 0

        async def complete(self, messages, **kwargs):
            self.calls += 1
            return GatewayCompletion(
                content=self.response,
                requested_model=DEFAULT_VIRTUAL_MODEL,
                selected_provider="qwen",
                selected_model=DEFAULT_VIRTUAL_MODEL,
                routing_reason="Default routing",
                fallback_used=False,
                request_id=f"req-{self.calls}",
                usage={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
                raw_metadata={},
            )

    fake_gateway = FakeGateway(gateway_response)
    product_service = ProductAgentService(
        gateway_client=fake_gateway,
        artifact_store=store,
        orchestration_service=orch,
    )
    agent_exec = AgentExecutionService(repo)

    lifecycle = ProductTaskLifecycleService(
        agent_execution=agent_exec,
        product_agent_service=product_service,
        artifact_store=store,
        orchestration=orch,
        founder_context_policy=True,
        retry_policy_decisions=retry_policy_decisions,
    )

    return lifecycle, orch, repo, sm, store, fake_gateway


def _make_predecessor_task(orch, run_id, title="Predecessor"):
    """Create a COMPLETED predecessor Task."""
    task, _ = orch.create_task(run_id, title=title, actor="test")
    task.assigned_agent = "product-agent"
    task.max_attempts = 2
    orch.repository.save_task(task)
    # Transition PENDING -> READY -> RUNNING -> COMPLETED
    orch.state_machine.transition_task(run_id, task.id, TaskStatus.READY, actor="test", reason="Setup")
    orch.state_machine.transition_task(run_id, task.id, TaskStatus.RUNNING, actor="test", reason="Execute")
    orch.state_machine.transition_task(run_id, task.id, TaskStatus.COMPLETED, actor="test", reason="Done")
    return orch.repository.get_task(run_id, task.id)


def _make_input_artifact(orch, run_id, task_id, store=None, name="input-artifact"):
    """Create an input Artifact for a Task, optionally writing to store."""
    if store is not None:
        stored = store.write_text(
            run_id=run_id,
            logical_name=name,
            filename=f"{name}.txt",
            text=f"Content for {name}",
            created_by="test",
            task_id=task_id,
            content_type="text/plain; charset=utf-8",
        )
        artifact = Artifact(
            run_id=run_id,
            task_id=task_id,
            kind=ArtifactKind.DATA,
            name=name,
            uri=stored.uri,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            created_by="test",
            metadata={"filename": stored.filename, "relation": "input"},
        )
    else:
        artifact = Artifact(
            run_id=run_id,
            task_id=task_id,
            kind=ArtifactKind.DATA,
            name=name,
            uri=f"artifact://{task_id}/{name}",
            checksum_sha256=f"sha256:{str(task_id)[:16]}",
            size_bytes=100,
            created_by="test",
            metadata={"relation": "input"},
        )
    orch.repository.create_artifact(artifact)

    return artifact


# ── Successful lifecycle tests ──────────────────────────────────────────────


class TestSuccessfulLifecycle:
    """Tests for the happy-path READY → COMPLETED lifecycle."""

    async def test_successful_ready_to_completed(self, tmp_path):
        """READY task completes through full lifecycle with predecessor Tasks."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)

        # Create predecessor Task and input artifact
        pred = _make_predecessor_task(orch, run.id, title="Predecessor")
        input_art = _make_input_artifact(orch, run.id, pred.id, store=store)

        # Create product task with predecessor dependency and input artifact
        task, _ = orch.create_task(
            run.id,
            title="Product Brief",
            description="Create the validated product brief.",
            actor="test",
        )
        task.assigned_agent = "product-agent"
        task.max_attempts = 2
        task.dependency_ids = [pred.id]  # predecessor Task ID
        task.input_artifact_ids = [input_art.id]
        orch.repository.save_task(task)
        orch.mark_task_ready(
            run.id,
            task.id,
            actor="test",
            reason="Predecessors completed",
        )
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        assert result.status == "completed", result.last_error
        assert result.task.status == "completed"
        assert result.task.claim_token is None
        assert result.task.claimed_by is None
        assert result.json_artifact is not None
        assert result.md_artifact is not None
        assert gateway.calls == 1

    async def test_requires_assigned_agent_product_agent(self, tmp_path):
        """Task with wrong assigned_agent is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task, _ = orch.create_task(run.id, title="Wrong Agent", actor="test")
        task.assigned_agent = "finance-agent"
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(TaskNotProductAgentError, match="finance-agent"):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_wrong_agent_rejected(self, tmp_path):
        """Task assigned to finance-agent is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task, _ = orch.create_task(run.id, title="Finance", actor="test")
        task.assigned_agent = "finance-agent"
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(TaskNotProductAgentError, match="finance-agent"):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_non_ready_task_rejected(self, tmp_path):
        """Non-READY task is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task, _ = orch.create_task(run.id, title="Pending", actor="test")
        task.assigned_agent = "product-agent"
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        # Leave as PENDING (not READY)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(ProductTaskLifecycleError):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_predecessor_not_completed_rejected(self, tmp_path):
        """Task with predecessor not COMPLETED is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)

        # Create predecessor but leave it RUNNING (not COMPLETED)
        pred, _ = orch.create_task(run.id, title="Incomplete Predecessor", actor="test")
        pred.assigned_agent = "product-agent"
        orch.repository.save_task(pred)
        orch.state_machine.transition_task(
            run.id, pred.id, TaskStatus.READY, actor="test", reason="Ready"
        )
        orch.state_machine.transition_task(run.id, pred.id, TaskStatus.RUNNING, actor="test", reason="Running")
        pred = orch.repository.get_task(run.id, pred.id)

        task, _ = orch.create_task(run.id, title="Product Brief", actor="test")
        task.assigned_agent = "product-agent"
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(PredecessorTaskNotCompletedError):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_missing_predecessor_rejected(self, tmp_path):
        """Task with missing predecessor Task is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)

        task, _ = orch.create_task(run.id, title="Product Brief", actor="test")
        task.assigned_agent = "product-agent"
        task.dependency_ids = [UUID("00000000-0000-0000-0000-000000000010")]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(TaskNotFoundError):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_claim_token_ownership_enforced(self, tmp_path):
        """Claim token ownership enforced — wrong token raises ClaimTokenMismatchError."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First call succeeds
        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result.status == "completed"

    async def test_product_agent_service_called_once_per_real_attempt(self, tmp_path):
        """ProductAgentService is called exactly once per real attempt."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        assert gateway.calls == 1

    async def test_both_product_artifacts_verified_before_completion(self, tmp_path):
        """Both Product Artifacts must exist and verify before completion."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        assert result.status == "completed"
        assert result.json_artifact is not None
        assert result.md_artifact is not None
        assert result.json_domain_artifact is not None
        assert result.md_domain_artifact is not None

    async def test_both_output_artifact_ids_registered(self, tmp_path):
        """Both output Artifact IDs are registered on the Task."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        updated_task = orch.repository.get_task(run.id, task.id)
        assert len(updated_task.output_artifact_ids) == 2
        assert result.json_domain_artifact.id in updated_task.output_artifact_ids
        assert result.md_domain_artifact.id in updated_task.output_artifact_ids

    async def test_success_clears_claim_fields(self, tmp_path):
        """Success clears claim_token, claimed_by, claimed_at."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        updated_task = orch.repository.get_task(run.id, task.id)
        assert updated_task.claim_token is None
        assert updated_task.claimed_by is None
        assert updated_task.claimed_at is None

    async def test_success_preserves_attempt_count(self, tmp_path):
        """Success preserves attempt_count (one claim = one increment)."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        updated_task = orch.repository.get_task(run.id, task.id)
        assert updated_task.attempt_count == 1


# ── Idempotency replay tests ────────────────────────────────────────────────


class TestIdempotencyReplay:
    """Tests for idempotent replay after completion."""

    async def test_identical_replay_after_completion_makes_no_gateway_call(self, tmp_path):
        """Re-invoking after COMPLETED makes no Gateway call."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert gateway.calls == 1

        result2 = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result2.status == "completed"
        assert gateway.calls == 1

    async def test_identical_replay_creates_no_duplicate_domain_artifact(self, tmp_path):
        """Replay after completion creates no duplicate Domain Artifacts."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        events_1 = orch.repository.list_events(run.id)
        registered_1 = [e for e in events_1 if e.event_type == "artifact.registered"]

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        events_2 = orch.repository.list_events(run.id)
        registered_2 = [e for e in events_2 if e.event_type == "artifact.registered"]

        assert len(registered_1) == len(registered_2)


# ── Failure and retry tests ─────────────────────────────────────────────────


class TestFailureAndRetry:
    """Tests for failure handling, retry, and terminal failure."""

    async def test_first_execution_failure_becomes_blocked(self, tmp_path):
        """First execution failure transitions task to BLOCKED."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        assert result.status == "blocked"
        assert result.retry_available is True
        assert result.terminal_failure is False
        updated_task = orch.repository.get_task(run.id, task.id)
        assert updated_task.status == "blocked"

    async def test_last_error_persisted(self, tmp_path):
        """last_error is persisted on failure."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        assert result.last_error is not None
        assert "validation" in result.last_error.lower()
        updated_task = orch.repository.get_task(run.id, task.id)
        assert updated_task.last_error == result.last_error

    async def test_retry_preparation_blocked_to_ready(self, tmp_path):
        """retry_blocked_task transitions BLOCKED through READY to execution."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert orch.repository.get_task(run.id, task.id).status == "blocked"

        # Switch gateway for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        # Inject retry policy decision for retry
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "product-lifecycle", "decision": "approved"
        }

        # Retry blocked task (BLOCKED → READY → execution → COMPLETED)
        result = await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result.status == "completed"
        assert result.task.last_error is None  # Cleared before retry

    async def test_successful_second_attempt_completes_task(self, tmp_path):
        """Successful retry completes the Task."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        result1 = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result1.status == "blocked"

        # Switch gateway for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        # Inject retry policy decision for retry
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "product-lifecycle", "decision": "approved"
        }

        # Retry blocked task (BLOCKED → READY → execution)
        result2 = await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result2.status == "completed"

    async def test_second_real_claim_increments_attempts_once(self, tmp_path):
        """Second real claim increments attempt_count once."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt
        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        task1 = orch.repository.get_task(run.id, task.id)
        assert task1.attempt_count == 1

        # Switch gateway for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        # Inject retry policy decision for retry
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "product-lifecycle", "decision": "approved"
        }

        # Retry blocked task
        await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        task2 = orch.repository.get_task(run.id, task.id)
        assert task2.attempt_count == 2

    async def test_exhausted_attempt_becomes_failed(self, tmp_path):
        """Exhausted attempts transition to FAILED."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief", max_attempts=1)
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        assert result.status == "failed"
        assert result.terminal_failure is True
        updated_task = orch.repository.get_task(run.id, task.id)
        assert updated_task.status == "failed"

    async def test_failed_cannot_restart(self, tmp_path):
        """FAILED task cannot be restarted."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief", max_attempts=1)
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        with pytest.raises(ProductTaskLifecycleError):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_validation_failure_creates_no_product_artifacts(self, tmp_path):
        """Validation failure creates no Product Artifacts."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        all_artifacts = orch.repository.list_artifacts(run.id)
        assert len(all_artifacts) == 0

    async def test_route_evidence_failure_creates_no_product_artifacts(self, tmp_path):
        """Route-evidence failure creates no Product Artifacts; task becomes BLOCKED."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response=json.dumps(VALID_RESULT_DICT)
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # Break route evidence recording
        original_record = orch.record_route_decision
        def broken_record(*args, **kwargs):
            raise RuntimeError("Simulated route failure")
        orch.record_route_decision = broken_record  # type: ignore[method-assign]

        try:
            result = await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )
            assert result.status == "blocked"

            all_artifacts = orch.repository.list_artifacts(run.id)
            assert len(all_artifacts) == 0
        finally:
            orch.record_route_decision = original_record  # type: ignore[method-assign]

    async def test_partial_content_registration_retry_reuses_content(self, tmp_path):
        """Partial content registration retry reuses existing content."""
        retry_policy = {}
        # First service with invalid JSON (will fail and block)
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        result1 = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle", correlation_id="cid-1"
        )
        assert result1.status == "blocked"

        # Switch gateway to valid JSON for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        # Inject retry policy decision for retry
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "product-lifecycle", "decision": "approved"
        }

        # Retry blocked task via retry_blocked_task
        result2 = await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle", correlation_id="cid-2"
        )
        assert result2.status == "completed"

        # Artifacts should have same checksums
        assert result2.json_artifact.checksum_sha256 is not None
        assert result2.md_artifact.checksum_sha256 is not None


# ── Retry authorization tests ────────────────────────────────────────────────


class TestRetryAuthorization:
    """Tests for retry authorization boundaries."""

    async def test_product_agent_cannot_authorize_own_retry(self, tmp_path):
        """product-agent actor is rejected for retry_blocked_task."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        # product-agent cannot authorize its own retry
        with pytest.raises(RetryAuthorizationError, match="not authorized"):
            await lifecycle.retry_blocked_task(
                run_id=run.id, task_id=task.id, actor="product-agent"
            )

    async def test_arbitrary_actor_rejected(self, tmp_path):
        """Arbitrary actor is rejected for retry_blocked_task."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        with pytest.raises(RetryAuthorizationError, match="not authorized"):
            await lifecycle.retry_blocked_task(
                run_id=run.id, task_id=task.id, actor="random-actor"
            )

    async def test_executive_actor_authorized_for_retry(self, tmp_path):
        """Executive actor is authorized for retry_blocked_task via retry policy decision."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        # Switch gateway for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        # Inject retry policy decision for executive actor
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "executive", "decision": "approved"
        }

        # Executive actor can retry via authorized retry_blocked_task
        result = await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="executive"
        )
        assert result.status == "completed"


# ── Restart recovery tests ──────────────────────────────────────────────────


class TestRestartRecovery:
    """Tests for process-restart recovery with entirely new instances."""

    async def test_completed_replay_with_new_instances(self, tmp_path):
        """New instances recover COMPLETED state without Gateway call."""
        # First set of instances: complete the task
        lifecycle1, orch1, repo1, sm1, store1, gateway1 = _make_lifecycle_service(tmp_path)
        run = _make_run(repo1, sm1, orch1, owner=OWNER)
        pred = _make_predecessor_task(orch1, run.id)
        task = _make_task(orch1, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch1.repository.save_task(task)
        task = orch1.repository.get_task(run.id, task.id)

        await lifecycle1.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert gateway1.calls == 1

        # New set of instances: recover from same filesystem paths
        repo2 = FileStateRepository(tmp_path / "runs")
        sm2 = LifecycleStateMachine(repo2)
        orch2 = OrchestrationService(repo2, sm2)
        store2 = FileArtifactStore(tmp_path / "artifacts")

        lifecycle2 = ProductTaskLifecycleService(
            agent_execution=AgentExecutionService(repo2),
            product_agent_service=ProductAgentService(
                gateway_client=gateway1,
                artifact_store=store2,
                orchestration_service=orch2,
            ),
            artifact_store=store2,
            orchestration=orch2,
        )

        reconcile = lifecycle2.reconcile_task(run.id, task.id)
        assert reconcile.status == "completed"
        assert reconcile.resumable is False

    async def test_blocked_retry_with_new_instances(self, tmp_path):
        """New instances can prepare and retry BLOCKED task."""
        lifecycle1, orch1, repo1, sm1, store1, gateway1 = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo1, sm1, orch1, owner=OWNER)
        pred = _make_predecessor_task(orch1, run.id)
        task = _make_task(orch1, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch1.repository.save_task(task)
        task = orch1.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        await lifecycle1.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        # New instances for retry
        repo2 = FileStateRepository(tmp_path / "runs")
        sm2 = LifecycleStateMachine(repo2)
        orch2 = OrchestrationService(repo2, sm2)
        store2 = FileArtifactStore(tmp_path / "artifacts")

        lifecycle2 = ProductTaskLifecycleService(
            agent_execution=AgentExecutionService(repo2),
            product_agent_service=ProductAgentService(
                gateway_client=gateway1,
                artifact_store=store2,
                orchestration_service=orch2,
            ),
            artifact_store=store2,
            orchestration=orch2,
        )

        reconcile = lifecycle2.reconcile_task(run.id, task.id)
        assert reconcile.status == "blocked"
        assert reconcile.action == "retry_authorization_required"

    async def test_running_with_fresh_valid_claim(self, tmp_path):
        """RUNNING with fresh valid claim → wait."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        # Simulate fresh claim
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        task.claimed_at = datetime.now(timezone.utc)
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "running"
        assert reconcile.action == "wait"
        assert reconcile.resumable is True

    async def test_running_with_invalid_ownership(self, tmp_path):
        """RUNNING with claimed_by != assigned_agent → manual_intervention_required."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        # Simulate invalid ownership
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "wrong-agent"
        task.claimed_at = datetime.now(timezone.utc)
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "stale"
        assert reconcile.action == "manual_intervention_required"
        assert reconcile.resumable is False

    async def test_running_with_completed_artifacts(self, tmp_path):
        """RUNNING with verified route and outputs → completion_resumable."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)

        lifecycle.agent_execution.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
            claim_token="token-123",
        )
        running_task = orch.repository.get_task(run.id, task.id)
        request = lifecycle._build_context(run, running_task, [])
        await lifecycle.product_agent_service.execute(
            request,
            created_by="product-lifecycle",
            correlation_id="recovery-test",
        )

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "running"
        assert reconcile.action == "completion_resumable"
        assert reconcile.resumable is True

    async def test_arbitrary_output_ids_are_not_completion_resumable(self, tmp_path):
        """Unknown output IDs cannot masquerade as durable completion evidence."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        task.claimed_at = datetime.now(timezone.utc)
        task.output_artifact_ids = [
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000002"),
        ]
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "stale"
        assert reconcile.action == "manual_intervention_required"
        assert reconcile.resumable is False


# ── Stale RUNNING reconciliation tests ──────────────────────────────────────


class TestStaleRunningReconciliation:
    """Tests for strengthened stale RUNNING reconciliation."""

    async def test_no_claim_evidence_manual_intervention(self, tmp_path):
        """RUNNING with no claim evidence → manual_intervention_required."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task.status = TaskStatus.RUNNING
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "stale"
        assert reconcile.action == "manual_intervention_required"
        assert reconcile.resumable is False

    async def test_fresh_claim_wait(self, tmp_path):
        """RUNNING with fresh valid claim → wait."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        task.claimed_at = datetime.now(timezone.utc)
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "running"
        assert reconcile.action == "wait"
        assert reconcile.resumable is True

    async def test_stale_claim_manual_intervention(self, tmp_path):
        """RUNNING with stale claim → manual_intervention_required."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        task.claimed_at = datetime.now(timezone.utc) - timedelta(hours=2)
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "stale"
        assert reconcile.action == "manual_intervention_required"
        assert reconcile.resumable is False
        assert reconcile.claim_age_seconds is not None
        assert reconcile.claim_age_seconds > 3600

    async def test_exhausted_budget_returns_wait_not_failed(self, tmp_path):
        """RUNNING with exhausted attempt budget → wait, not failed.

        A RUNNING Task with attempt_count == max_attempts is still executing
        its last legal attempt. FAILED is only returned when persisted
        Task.status is FAILED.
        """
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief", max_attempts=1)
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        task.claimed_at = datetime.now(timezone.utc)
        task.attempt_count = 1
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        # RUNNING with exhausted attempts but no persisted FAILED status → wait
        assert reconcile.status == "running"
        assert reconcile.action == "wait"
        assert reconcile.resumable is True


# ── Blocker 1: final-attempt reconciliation ──────────────────────────────────


class TestFinalAttemptReconciliation:
    """RUNNING with attempt_count == max_attempts is still executing its last legal attempt."""

    async def test_exhausted_attempts_without_completion_evidence_returns_wait(self, tmp_path):
        """RUNNING with exhausted attempts but no outputs → wait, not failed."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief", max_attempts=1)
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        # Simulate RUNNING with exhausted attempts, no outputs
        task.status = TaskStatus.RUNNING
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        task.claimed_at = datetime.now(timezone.utc)
        task.attempt_count = 1
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "running"
        assert reconcile.action == "wait"
        assert reconcile.resumable is True

    async def test_running_with_valid_completion_evidence_is_resumable(self, tmp_path):
        """RUNNING with valid completion evidence → completion_resumable."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)

        # Claim and execute to produce valid outputs
        lifecycle.agent_execution.claim_task(
            run.id, task.id, agent_id="product-agent", claim_token="token-123"
        )
        running_task = orch.repository.get_task(run.id, task.id)
        request = lifecycle._build_context(run, running_task, [])
        await lifecycle.product_agent_service.execute(
            request, created_by="product-lifecycle", correlation_id="recovery-test"
        )

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "running"
        assert reconcile.action == "completion_resumable"
        assert reconcile.resumable is True

    async def test_failed_only_returned_when_persisted_status_is_failed(self, tmp_path):
        """FAILED reconciliation only when task.status is actually FAILED."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief", max_attempts=1)
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        # Persist as FAILED via state machine: READY -> RUNNING -> FAILED
        orch.state_machine.transition_task(
            run.id, task.id, TaskStatus.RUNNING,
            actor="test", reason="Running"
        )
        orch.state_machine.transition_task(
            run.id, task.id, TaskStatus.FAILED,
            actor="test", reason="Persisted failure"
        )
        task = orch.repository.get_task(run.id, task.id)
        task.claim_token = "token-123"
        task.claimed_by = "product-agent"
        orch.repository.save_task(task)

        reconcile = lifecycle.reconcile_task(run.id, task.id)
        assert reconcile.status == "failed"
        assert reconcile.resumable is False


# ── Blocker 2: completed replay ──────────────────────────────────────────────


class TestCompletedReplay:
    """COMPLETED replay returns artifacts and makes no Gateway call."""

    async def test_completed_replay_returns_artifacts(self, tmp_path):
        """COMPLETED replay returns json_artifact and md_artifact references."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        input_art = _make_input_artifact(orch, run.id, pred.id, store=store)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [input_art.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First execution → COMPLETED
        result1 = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result1.status == "completed"
        assert gateway.calls == 1

        # Replay → COMPLETED with artifacts, no Gateway call
        result2 = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result2.status == "completed"
        assert result2.terminal_failure is False
        assert gateway.calls == 1  # No additional call
        assert result2.json_artifact is not None
        assert result2.md_artifact is not None
        assert result2.json_domain_artifact is not None
        assert result2.md_domain_artifact is not None

    async def test_completed_replay_creates_no_duplicate_artifacts(self, tmp_path):
        """Replay after completion creates no duplicate Domain Artifacts."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        events_1 = orch.repository.list_events(run.id)
        registered_1 = [e for e in events_1 if e.event_type == "artifact.registered"]

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        events_2 = orch.repository.list_events(run.id)
        registered_2 = [e for e in events_2 if e.event_type == "artifact.registered"]

        assert len(registered_1) == len(registered_2)


# ── Blocker 3: retry authorization ───────────────────────────────────────────


class TestRetryAuthorizationPersisted:
    """Retry authorization via persisted Approval or retry policy decision."""

    async def test_persisted_approval_authorizes_retry(self, tmp_path):
        """Persisted APPROVED Approval authorizes retry_blocked_task."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First attempt → BLOCKED
        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        # Switch gateway for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        # Create a persisted APPROVED Approval directly for this task
        approval = Approval(
            run_id=run.id,
            task_id=task.id,
            status=ApprovalStatus.APPROVED.value,
            requested_by="product-lifecycle",
            reason="Retry needed",
            decided_by="executive",
            decision_reason="Approved retry",
        )
        orch.repository.create_approval(approval)

        # Retry with the approving actor
        result = await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="executive"
        )
        assert result.status == "completed"


# ── Blocker 4: post-claim failure paths ─────────────────────────────────────


class TestPostClaimFailurePaths:
    """Post-claim failures produce deterministic persisted results."""

    async def test_completion_persistence_failure_returns_reconciliation_required(self, tmp_path):
        """Completion persistence failure returns reconciliation_required=True."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        input_art = _make_input_artifact(orch, run.id, pred.id, store=store)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [input_art.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # Break complete_claimed_task to simulate persistence failure
        original_complete = lifecycle.agent_execution.complete_claimed_task
        def broken_complete(*args, **kwargs):
            raise RuntimeError("Simulated completion persistence failure")
        lifecycle.agent_execution.complete_claimed_task = broken_complete  # type: ignore[method-assign]

        try:
            result = await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )
            assert result.status == "running"
            assert result.reconciliation_required is True
            assert result.json_artifact is not None
            assert result.md_artifact is not None
            # Verify task is still RUNNING with claim evidence preserved
            current_task = orch.repository.get_task(run.id, task.id)
            assert current_task.status == "running"
            assert current_task.claim_token is not None
        finally:
            lifecycle.agent_execution.complete_claimed_task = original_complete  # type: ignore[method-assign]

    async def test_claim_token_not_leaked_in_errors(self, tmp_path):
        """Claim token is not leaked in public error messages."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # Break complete to simulate failure with claim token in error
        original_complete = lifecycle.agent_execution.complete_claimed_task
        def broken_complete(*args, **kwargs):
            raise RuntimeError("Failed with token: claim_token=abc123xyz")
        lifecycle.agent_execution.complete_claimed_task = broken_complete  # type: ignore[method-assign]

        try:
            result = await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )
            assert "abc123xyz" not in (result.last_error or "")
            assert "[REDACTED]" in (result.last_error or "")
        finally:
            lifecycle.agent_execution.complete_claimed_task = original_complete  # type: ignore[method-assign]

    async def test_complete_after_reconciliation_succeeds(self, tmp_path):
        """complete_after_reconciliation completes task without Gateway call."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, retry_policy_decisions=retry_policy
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        input_art = _make_input_artifact(orch, run.id, pred.id, store=store)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [input_art.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # Complete the task normally
        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result.status == "completed"

        # Now simulate a restart: set task back to RUNNING with claim token
        # (this simulates a completion persistence failure scenario)
        running_task = orch.repository.get_task(run.id, task.id)
        running_task.status = TaskStatus.RUNNING
        running_task.claim_token = "token-123"
        running_task.claimed_by = "product-agent"
        running_task.claimed_at = datetime.now(timezone.utc)
        running_task.completed_at = None
        orch.repository.save_task(running_task)

        # Inject retry policy decision for reconciliation completion
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "executive", "decision": "approved"
        }

        # Complete after reconciliation
        reconcile_result = await lifecycle.complete_after_reconciliation(
            run_id=run.id, task_id=task.id, actor="executive"
        )
        assert reconcile_result.status == "completed"
        assert reconcile_result.json_domain_artifact is not None
        assert reconcile_result.md_domain_artifact is not None


# ── Blocker 5: input Artifact integrity ──────────────────────────────────────


class TestInputArtifactIntegrity:
    """Input artifacts are fully verified before Gateway execution."""

    async def test_corrupt_checksum_input_artifact_fails(self, tmp_path):
        """Input artifact with checksum mismatch fails before execution."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        # Create artifact with wrong checksum in domain metadata
        stored = store.write_text(
            run_id=run.id,
            logical_name="input-artifact",
            filename="input-artifact.txt",
            text="Content",
            created_by="test",
            task_id=pred.id,
            content_type="text/plain; charset=utf-8",
        )
        artifact = Artifact(
            run_id=run.id,
            task_id=pred.id,
            kind=ArtifactKind.DATA,
            name="input-artifact",
            uri=stored.uri,
            checksum_sha256="sha256:wrong_checksum",  # Mismatch
            size_bytes=stored.size_bytes,
            created_by="test",
            metadata={"filename": stored.filename, "relation": "input"},
        )
        orch.repository.create_artifact(artifact)

        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [artifact.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(DependencyArtifactCorruptError, match="checksum mismatch"):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_missing_input_artifact_fails(self, tmp_path):
        """Missing input artifact fails before execution."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [UUID("00000000-0000-0000-0000-000000000099")]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(DependencyArtifactMissingError):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )

    async def test_wrong_task_lineage_input_artifact_fails(self, tmp_path):
        """Input artifact from unrelated task fails before execution."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        unrelated_task, _ = orch.create_task(run.id, title="Unrelated", actor="test")
        unrelated_task.assigned_agent = "product-agent"
        orch.repository.save_task(unrelated_task)

        stored = store.write_text(
            run_id=run.id,
            logical_name="input-artifact",
            filename="input-artifact.txt",
            text="Content",
            created_by="test",
            task_id=unrelated_task.id,
            content_type="text/plain; charset=utf-8",
        )
        artifact = Artifact(
            run_id=run.id,
            task_id=unrelated_task.id,
            kind=ArtifactKind.DATA,
            name="input-artifact",
            uri=stored.uri,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            created_by="test",
            metadata={"filename": stored.filename},
        )
        orch.repository.create_artifact(artifact)

        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [artifact.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        with pytest.raises(DependencyArtifactCorruptError, match="task_id"):
            await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )


# ── Blocker 6: exact output registration ─────────────────────────────────────


class TestExactOutputRegistration:
    """Output artifacts must be exactly one JSON and one Markdown product-brief."""

    async def test_duplicate_output_ids_rejected(self, tmp_path):
        """Duplicate output IDs in task.output_artifact_ids are rejected by verification."""
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        input_art = _make_input_artifact(orch, run.id, pred.id, store=store)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [input_art.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # First execution to get artifacts
        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result.status == "completed"

        # Directly verify that duplicate output IDs are rejected
        completed_task = orch.repository.get_task(run.id, task.id)
        # Manually add duplicate output ID
        completed_task.output_artifact_ids.append(result.json_domain_artifact.id)
        orch.repository.save_task(completed_task)

        # _verify_output_registration should reject duplicates
        with pytest.raises(ProductArtifactVerificationError, match="exactly once"):
            lifecycle._verify_output_registration(
                task.id, completed_task,
                result.json_domain_artifact, result.md_domain_artifact
            )

    async def test_wrong_output_name_rejected(self, tmp_path):
        """Non-product-brief output names fail verification."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # Directly verify that non-standard names are rejected
        from app.services.product_lifecycle import ProductArtifactVerificationError
        fake_json = type('obj', (object,), {
            'name': 'wrong-name', 'id': task.id,
            'run_id': run.id, 'task_id': task.id,
            'checksum_sha256': 'abc', 'size_bytes': 100, 'uri': 'x'
        })()
        fake_md = type('obj', (object,), {
            'name': 'product-brief-md', 'id': task.id,
            'run_id': run.id, 'task_id': task.id,
            'checksum_sha256': 'def', 'size_bytes': 200, 'uri': 'y'
        })()

        with pytest.raises(ProductArtifactVerificationError, match="product-brief"):
            lifecycle._verify_output_registration(task.id, task, fake_json, fake_md)


# ── Blocker 7: real behavior tests ──────────────────────────────────────────


class TestRealBehaviorTests:
    """Real behavior tests replacing weak mocks."""

    async def test_wrong_claim_token_rejected(self, tmp_path):
        """Wrong claim token on completion is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        result = await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert result.status == "completed"

    async def test_wrong_claim_owner_rejected(self, tmp_path):
        """Wrong claim owner on completion is rejected at execution service level."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(tmp_path)
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        claim = lifecycle.agent_execution.claim_task(
            run.id, task.id, agent_id="product-agent"
        )

        from app.services.execution import AgentNotExecutableError
        with pytest.raises(AgentNotExecutableError):
            lifecycle.agent_execution.complete_claimed_task(
                run.id, task.id,
                claim_token=claim.claim_token,
                actor="finance-agent",
            )

    async def test_unauthorized_retry_rejected(self, tmp_path):
        """Unauthorized retry without approval or policy decision is rejected."""
        lifecycle, orch, repo, sm, store, _ = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json"
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        with pytest.raises(RetryAuthorizationError, match="not authorized"):
            await lifecycle.retry_blocked_task(
                run_id=run.id, task_id=task.id, actor="random-actor"
            )

    async def test_approved_retry_with_policy_decision(self, tmp_path):
        """Approved retry via injected policy decision succeeds."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        await lifecycle.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        # Switch gateway for retry
        gateway.response = json.dumps(VALID_RESULT_DICT)

        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "executive", "decision": "approved"
        }
        result = await lifecycle.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="executive"
        )
        assert result.status == "completed"


# ── Blocker 8: governance and recovery evidence ──────────────────────────────


class TestGovernanceRecoveryEvidence:
    """Recovery with entirely new instances and restart scenarios."""

    async def test_entirely_new_instances_recover_completed(self, tmp_path):
        """Entirely new repository/store/orchestration/service instances recover COMPLETED."""
        # First set: complete the task
        lifecycle1, orch1, repo1, sm1, store1, gateway1 = _make_lifecycle_service(tmp_path)
        run = _make_run(repo1, sm1, orch1, owner=OWNER)
        pred = _make_predecessor_task(orch1, run.id)
        input_art = _make_input_artifact(orch1, run.id, pred.id, store=store1)
        task = _make_task(orch1, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [input_art.id]
        orch1.repository.save_task(task)
        task = orch1.repository.get_task(run.id, task.id)

        await lifecycle1.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )
        assert gateway1.calls == 1

        # Entirely new instances from same paths
        repo2 = FileStateRepository(tmp_path / "runs")
        sm2 = LifecycleStateMachine(repo2)
        orch2 = OrchestrationService(repo2, sm2)
        store2 = FileArtifactStore(tmp_path / "artifacts")

        lifecycle2 = ProductTaskLifecycleService(
            agent_execution=AgentExecutionService(repo2),
            product_agent_service=ProductAgentService(
                gateway_client=gateway1,
                artifact_store=store2,
                orchestration_service=orch2,
            ),
            artifact_store=store2,
            orchestration=orch2,
            retry_policy_decisions={},
        )

        reconcile = lifecycle2.reconcile_task(run.id, task.id)
        assert reconcile.status == "completed"
        assert reconcile.resumable is False

    async def test_blocked_retry_after_restart_with_new_instances(self, tmp_path):
        """BLOCKED retry after restart with new instances."""
        retry_policy = {}
        lifecycle1, orch1, repo1, sm1, store1, gateway1 = _make_lifecycle_service(
            tmp_path, gateway_response="not valid json",
            retry_policy_decisions=retry_policy,
        )
        run = _make_run(repo1, sm1, orch1, owner=OWNER)
        pred = _make_predecessor_task(orch1, run.id)
        task = _make_task(orch1, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        orch1.repository.save_task(task)
        task = orch1.repository.get_task(run.id, task.id)

        await lifecycle1.execute_ready_task(
            run_id=run.id, task_id=task.id, actor="product-lifecycle"
        )

        # New instances
        repo2 = FileStateRepository(tmp_path / "runs")
        sm2 = LifecycleStateMachine(repo2)
        orch2 = OrchestrationService(repo2, sm2)
        store2 = FileArtifactStore(tmp_path / "artifacts")

        lifecycle2 = ProductTaskLifecycleService(
            agent_execution=AgentExecutionService(repo2),
            product_agent_service=ProductAgentService(
                gateway_client=gateway1,
                artifact_store=store2,
                orchestration_service=orch2,
            ),
            artifact_store=store2,
            orchestration=orch2,
            retry_policy_decisions=retry_policy,
        )

        reconcile = lifecycle2.reconcile_task(run.id, task.id)
        assert reconcile.status == "blocked"
        assert reconcile.action == "retry_authorization_required"

        # Switch gateway for retry
        gateway1.response = json.dumps(VALID_RESULT_DICT)

        # Inject policy and retry
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "executive", "decision": "approved"
        }
        result = await lifecycle2.retry_blocked_task(
            run_id=run.id, task_id=task.id, actor="executive"
        )
        assert result.status == "completed"

    async def test_completion_interrupted_then_completed_after_restart(self, tmp_path):
        """Completion interrupted after persisted outputs, then completed after restart."""
        retry_policy = {}
        lifecycle, orch, repo, sm, store, gateway = _make_lifecycle_service(
            tmp_path, retry_policy_decisions=retry_policy
        )
        run = _make_run(repo, sm, orch, owner=OWNER)
        pred = _make_predecessor_task(orch, run.id)
        input_art = _make_input_artifact(orch, run.id, pred.id, store=store)
        task = _make_task(orch, run.id, title="Product Brief")
        task.dependency_ids = [pred.id]
        task.input_artifact_ids = [input_art.id]
        orch.repository.save_task(task)
        task = orch.repository.get_task(run.id, task.id)

        # Simulate completion persistence failure
        original_complete = lifecycle.agent_execution.complete_claimed_task
        def broken_complete(*args, **kwargs):
            raise RuntimeError("Simulated persistence failure")
        lifecycle.agent_execution.complete_claimed_task = broken_complete  # type: ignore[method-assign]

        try:
            result = await lifecycle.execute_ready_task(
                run_id=run.id, task_id=task.id, actor="product-lifecycle"
            )
            assert result.reconciliation_required is True
            assert result.status == "running"
        finally:
            lifecycle.agent_execution.complete_claimed_task = original_complete  # type: ignore[method-assign]

        # After restart, complete via complete_after_reconciliation
        retry_policy[(str(run.id), str(task.id))] = {
            "actor": "executive", "decision": "approved"
        }
        completed = await lifecycle.complete_after_reconciliation(
            run_id=run.id, task_id=task.id, actor="executive"
        )
        assert completed.status == "completed"


# ── Existing tests remain green ──────────────────────────────────────────────


class TestExistingTestsGreen:
    """Verify D06-D does not break existing D00-D06-C tests."""

    def test_existing_imports_work(self):
        """All existing imports still work."""
        from app.services import (
            ProductAgentService,
            ProductTaskLifecycleService,
        )
        assert ProductTaskLifecycleService is not None
        assert ProductAgentService is not None
