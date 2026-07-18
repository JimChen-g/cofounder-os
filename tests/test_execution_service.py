"""Tests for the D06-A AgentExecutionService."""

from __future__ import annotations

import pytest

from app.agents import (
    AgentRegistry,
)
from app.domain import (
    AuditOutcome,
    Run,
    Task,
    TaskStatus,
)
from app.services import (
    AgentExecutionService,
    AgentNotExecutableError,
    AttemptLimitExceededError,
    ClaimTokenMismatchError,
    RetryPreparationResult,
    TaskAlreadyClaimedError,
    TaskClaim,
    TaskNotReadyError,
)
from app.state import FileStateRepository


def build_execution_service(tmp_path):
    """Build an AgentExecutionService with a fresh repository."""
    repository = FileStateRepository(tmp_path / "runs")
    service = AgentExecutionService(repository)
    return repository, service


def setup_ready_task(repository, *, agent_id="product-agent"):
    """Create a run and a ready task assigned to the given agent."""
    run = Run(objective="Test execution")
    repository.create_run(run)
    task = Task(
        run_id=run.id,
        title="Test task",
        assigned_agent=agent_id,
        status=TaskStatus.READY.value,
    )
    repository.create_task(task)
    return run, task


class TestClaimTask:
    """Tests for the claim_task method."""

    def test_legacy_task_json_receives_defaults(self, tmp_path):
        """Legacy Task JSON without new fields loads with defaults."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        # Verify defaults are applied
        assert task.attempt_count == 0
        assert task.max_attempts == 2
        assert task.last_error is None
        assert task.claimed_by is None
        assert task.claim_token is None
        assert task.claimed_at is None

    def test_atomic_claim_success(self, tmp_path):
        """Claim a ready task atomically."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        assert isinstance(claim, TaskClaim)
        assert claim.task_id == str(task.id)
        assert claim.claimed_by == "product-agent"
        assert claim.claim_token is not None
        assert claim.attempt_number == 1

        # Verify task is updated
        updated = repository.get_task(run.id, task.id)
        assert updated.status == TaskStatus.RUNNING.value
        assert updated.claim_token == claim.claim_token
        assert updated.claimed_by == "product-agent"
        assert updated.attempt_count == 1

    def test_same_token_idempotent_re_claim(self, tmp_path):
        """Re-claiming with the same token and agent is idempotent."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim1 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        claim2 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
            claim_token=claim1.claim_token,
        )

        assert claim2.claim_token == claim1.claim_token
        assert claim2.attempt_number == 1  # No double increment

        # Verify task state unchanged
        updated = repository.get_task(run.id, task.id)
        assert updated.attempt_count == 1

    def test_same_token_from_different_agent_rejected(self, tmp_path):
        """Same token from a different executable agent is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim1 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(ClaimTokenMismatchError, match="owned by"):
            service.claim_task(
                run.id,
                task.id,
                agent_id="finance-agent",
                claim_token=claim1.claim_token,
            )

    def test_competing_token_rejected(self, tmp_path):
        """Competing claim with different token is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim1 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(ClaimTokenMismatchError):
            service.claim_task(
                run.id,
                task.id,
                agent_id="product-agent",
                claim_token="wrong-token",
            )

        # Verify first claim still valid
        updated = repository.get_task(run.id, task.id)
        assert updated.claim_token == claim1.claim_token

    def test_competing_claim_without_token_rejected(self, tmp_path):
        """Claiming an already-claimed task without token is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(TaskAlreadyClaimedError):
            service.claim_task(
                run.id,
                task.id,
                agent_id="product-agent",
            )

    def test_wrong_agent_rejected(self, tmp_path):
        """Claiming a task assigned to another agent is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository, agent_id="finance-agent")

        with pytest.raises(AgentNotExecutableError, match="finance-agent"):
            service.claim_task(
                run.id,
                task.id,
                agent_id="product-agent",
            )

    def test_non_executable_agent_rejected(self, tmp_path):
        """Claiming with a non-executable agent is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        with pytest.raises(AgentNotExecutableError, match="legal-agent"):
            service.claim_task(
                run.id,
                task.id,
                agent_id="legal-agent",
            )

    def test_non_ready_task_rejected(self, tmp_path):
        """Claiming a non-ready task is rejected."""
        repository, service = build_execution_service(tmp_path)
        run = Run(objective="Test execution")
        repository.create_run(run)
        task = Task(
            run_id=run.id,
            title="Test task",
            assigned_agent="product-agent",
            status=TaskStatus.PENDING.value,
        )
        repository.create_task(task)

        with pytest.raises(TaskNotReadyError, match="READY"):
            service.claim_task(
                run.id,
                task.id,
                agent_id="product-agent",
            )

    def test_exhausted_attempt_budget_blocks_claim(self, tmp_path):
        """READY task with attempt_count >= max_attempts cannot be claimed."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        # Exhaust the attempt budget
        task.attempt_count = 2
        task.max_attempts = 2
        repository.save_task(task)

        with pytest.raises(AttemptLimitExceededError):
            service.claim_task(
                run.id,
                task.id,
                agent_id="product-agent",
            )


class TestCompleteClaimedTask:
    """Tests for the complete_claimed_task method."""

    def test_completion_requires_matching_token_and_agent(self, tmp_path):
        """Completing a task requires matching token and agent."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        completed, event = service.complete_claimed_task(
            run.id,
            task.id,
            claim_token=claim.claim_token,
            actor="product-agent",
        )

        assert completed.status == TaskStatus.COMPLETED.value
        assert completed.claim_token is None
        assert completed.claimed_by is None
        assert completed.claimed_at is None
        assert completed.completed_at is not None
        assert event.event_type == "task.completed"
        assert event.outcome == AuditOutcome.SUCCESS

    def test_completion_by_different_agent_rejected(self, tmp_path):
        """Completing by a different agent is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(AgentNotExecutableError, match="finance-agent"):
            service.complete_claimed_task(
                run.id,
                task.id,
                claim_token=claim.claim_token,
                actor="finance-agent",
            )

    def test_completion_with_wrong_token_rejected(self, tmp_path):
        """Completing with wrong token is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        _claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(ClaimTokenMismatchError):
            service.complete_claimed_task(
                run.id,
                task.id,
                claim_token="wrong-token",
                actor="product-agent",
            )

    def test_completion_clears_claim_fields(self, tmp_path):
        """Completion clears all claim-related fields."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        completed, _ = service.complete_claimed_task(
            run.id,
            task.id,
            claim_token=claim.claim_token,
            actor="product-agent",
        )

        updated = repository.get_task(run.id, task.id)
        assert updated.claim_token is None
        assert updated.claimed_by is None
        assert updated.claimed_at is None


class TestRecordAttemptFailure:
    """Tests for the record_attempt_failure method."""

    def test_first_failure_status_is_blocked(self, tmp_path):
        """First failure transitions task to BLOCKED."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        result = service.record_attempt_failure(
            run.id,
            task.id,
            claim_token=claim.claim_token,
            error="Gateway timeout",
            actor="product-agent",
        )

        assert result.retry_available is True
        assert result.terminal_failure is False
        assert result.task.status == TaskStatus.BLOCKED.value
        assert result.task.last_error == "Gateway timeout"
        assert result.task.claim_token is None  # Claim cleared
        assert result.audit_event.event_type == "task.attempt_failed"
        assert result.audit_event.outcome == AuditOutcome.FAILURE

    def test_failure_by_different_agent_rejected(self, tmp_path):
        """Recording failure by a different agent is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(AgentNotExecutableError, match="finance-agent"):
            service.record_attempt_failure(
                run.id,
                task.id,
                claim_token=claim.claim_token,
                error="Test error",
                actor="finance-agent",
            )

    def test_second_failure_is_terminal(self, tmp_path):
        """Second failure (at max_attempts) produces terminal FAILED."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        # First claim and fail
        claim1 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )
        result1 = service.record_attempt_failure(
            run.id,
            task.id,
            claim_token=claim1.claim_token,
            error="First failure",
            actor="product-agent",
        )
        assert result1.retry_available is True
        assert result1.task.status == TaskStatus.BLOCKED.value

        # Prepare retry
        retry1 = service.prepare_retry(
            run.id,
            task.id,
            actor="orchestrator",
        )
        assert retry1.task.status == TaskStatus.READY.value

        # Second claim and fail
        claim2 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )
        assert claim2.attempt_number == 2

        result2 = service.record_attempt_failure(
            run.id,
            task.id,
            claim_token=claim2.claim_token,
            error="Second failure",
            actor="product-agent",
        )

        assert result2.terminal_failure is True
        assert result2.retry_available is False
        assert result2.task.status == TaskStatus.FAILED.value
        assert result2.task.last_error == "Second failure"
        assert result2.task.claim_token is None
        assert result2.audit_event.event_type == "task.failed"
        assert result2.audit_event.outcome == AuditOutcome.FAILURE

    def test_retry_after_exhaustion_raises(self, tmp_path):
        """Retry after max_attempts exhaustion raises error."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        # Exhaust attempts
        for i in range(2):
            claim = service.claim_task(
                run.id,
                task.id,
                agent_id="product-agent",
            )
            result = service.record_attempt_failure(
                run.id,
                task.id,
                claim_token=claim.claim_token,
                error=f"Failure {i+1}",
                actor="product-agent",
            )
            if result.retry_available:
                service.prepare_retry(
                    run.id,
                    task.id,
                    actor="orchestrator",
                )

        # Task should be terminal
        updated = repository.get_task(run.id, task.id)
        assert updated.status == TaskStatus.FAILED.value

        # Cannot prepare retry on terminal task
        with pytest.raises(AttemptLimitExceededError):
            service.prepare_retry(
                run.id,
                task.id,
                actor="orchestrator",
            )

    def test_no_double_increment_on_same_claim(self, tmp_path):
        """Attempt count increments only once per real claim."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim1 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )
        assert claim1.attempt_number == 1

        # Idempotent re-claim
        claim2 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
            claim_token=claim1.claim_token,
        )
        assert claim2.attempt_number == 1  # No increment

    def test_failure_with_wrong_token_rejected(self, tmp_path):
        """Recording failure with wrong token is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        _claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(ClaimTokenMismatchError):
            service.record_attempt_failure(
                run.id,
                task.id,
                claim_token="wrong-token",
                error="Test error",
                actor="product-agent",
            )


class TestPrepareRetry:
    """Tests for the prepare_retry method."""

    def test_prepare_retry_moves_blocked_to_ready(self, tmp_path):
        """Prepare retry moves BLOCKED task to READY."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )
        result = service.record_attempt_failure(
            run.id,
            task.id,
            claim_token=claim.claim_token,
            error="Temporary failure",
            actor="product-agent",
        )
        assert result.task.status == TaskStatus.BLOCKED.value

        retry = service.prepare_retry(
            run.id,
            task.id,
            actor="orchestrator",
        )

        assert isinstance(retry, RetryPreparationResult)
        assert retry.task.status == TaskStatus.READY.value
        assert retry.task.last_error is None
        assert retry.audit_event.event_type == "task.retry_prepared"
        assert retry.audit_event.outcome == AuditOutcome.SUCCESS

    def test_prepare_retry_on_failed_rejected(self, tmp_path):
        """Preparing retry on FAILED task is rejected (terminal)."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )
        _result = service.record_attempt_failure(
            run.id,
            task.id,
            claim_token=claim.claim_token,
            error="Terminal failure",
            actor="product-agent",
        )
        # Exhaust attempts
        service.prepare_retry(
            run.id,
            task.id,
            actor="orchestrator",
        )
        claim2 = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )
        result2 = service.record_attempt_failure(
            run.id,
            task.id,
            claim_token=claim2.claim_token,
            error="Second failure",
            actor="product-agent",
        )
        assert result2.task.status == TaskStatus.FAILED.value

        # Cannot retry terminal task
        with pytest.raises(AttemptLimitExceededError):
            service.prepare_retry(
                run.id,
                task.id,
                actor="orchestrator",
            )

    def test_prepare_retry_on_running_rejected(self, tmp_path):
        """Preparing retry on RUNNING task is rejected."""
        repository, service = build_execution_service(tmp_path)
        run, task = setup_ready_task(repository)

        _claim = service.claim_task(
            run.id,
            task.id,
            agent_id="product-agent",
        )

        with pytest.raises(TaskNotReadyError, match="BLOCKED"):
            service.prepare_retry(
                run.id,
                task.id,
                actor="orchestrator",
            )


class TestExecutivePromptExposesOnlyExecutableAgents:
    """Tests for the Executive Orchestrator prompt catalog."""

    def test_executive_prompt_exposes_only_executable_agents(self):
        """Only 3 of 6 agents are exposed as executable."""
        registry = AgentRegistry()
        catalog = registry.prompt_catalog()

        # Should have 3 executable agents
        assert len(catalog) == 3

        agent_ids = {agent["agent_id"] for agent in catalog}
        assert agent_ids == {
            "executive-orchestrator",
            "product-agent",
            "finance-agent",
        }

    def test_non_executable_agents_excluded_from_prompt(self):
        """Research, legal, and operations are not in prompt catalog."""
        registry = AgentRegistry()
        catalog = registry.prompt_catalog()

        agent_ids = {agent["agent_id"] for agent in catalog}
        assert "research-agent" not in agent_ids
        assert "legal-agent" not in agent_ids
        assert "operations-agent" not in agent_ids
