"""Application service for bounded agent task execution.

This service provides the reusable execution contract for D06. It manages
task claims, attempt tracking, retry logic, and audit events. It does not
call providers directly and does not expose HTTP routes.
"""

from __future__ import annotations

import secrets
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.agents import (
    AgentRegistry,
    AgentRegistryError,
)
from app.domain import (
    AuditEvent,
    AuditOutcome,
    Task,
    TaskStatus,
    utc_now,
)
from app.state import FileStateRepository


class AgentExecutionError(RuntimeError):
    """Base error for agent execution operations."""


class TaskNotReadyError(AgentExecutionError):
    """Raised when the task is not in a claimable state."""


class AgentNotExecutableError(AgentExecutionError):
    """Raised when the agent is not registered as executable."""


class TaskAlreadyClaimedError(AgentExecutionError):
    """Raised when the task is already claimed by another agent."""


class ClaimTokenMismatchError(AgentExecutionError):
    """Raised when the claim token does not match."""


class AttemptLimitExceededError(AgentExecutionError):
    """Raised when the task has exceeded its maximum attempts."""


class TaskTerminallyFailedError(AgentExecutionError):
    """Raised when the task is in a terminal failed state."""


class TaskClaim(BaseModel):
    """Represents an atomic claim on a task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    claim_token: str
    claimed_by: str
    claimed_at: str
    attempt_number: int


class AttemptFailureResult(BaseModel):
    """Result of recording an attempt failure."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    audit_event: AuditEvent
    retry_available: bool
    terminal_failure: bool


class RetryPreparationResult(BaseModel):
    """Result of preparing a task for retry."""

    model_config = ConfigDict(extra="forbid")

    task: Task
    audit_event: AuditEvent


def _generate_claim_token() -> str:
    """Generate a cryptographically random claim token."""
    return secrets.token_urlsafe(32)


def _event(
    *,
    run_id: UUID,
    task_id: UUID,
    event_type: str,
    actor: str,
    action: str,
    details: dict[str, object],
    correlation_id: str | None = None,
) -> AuditEvent:
    """Create an audit event for execution operations."""
    return AuditEvent(
        run_id=run_id,
        task_id=task_id,
        event_type=event_type,
        actor=actor,
        action=action,
        target_type="task",
        target_id=str(task_id),
        outcome=AuditOutcome.SUCCESS,
        correlation_id=correlation_id,
        details=details,
    )


class AgentExecutionService:
    """Manage bounded task execution with claims, attempts, and retries.

    This service enforces:
    - Only executable agents can claim tasks
    - Atomic claim with idempotent same-token re-claim
    - Competing claim rejection
    - One increment per real attempt
    - Bounded retry (max_attempts)
    - Persisted last_error
    - Terminal failure after exhaustion
    - Claim cleanup on completion
    - Append-only audit events
    """

    def __init__(
        self,
        repository: FileStateRepository,
        registry: AgentRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry or AgentRegistry()

    def claim_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        agent_id: str,
        claim_token: str | None = None,
        correlation_id: str | None = None,
    ) -> TaskClaim:
        """Atomically claim a ready task for execution.

        Args:
            run_id: The run ID.
            task_id: The task ID to claim.
            agent_id: The agent claiming the task.
            claim_token: Optional existing token for idempotent re-claim.
            correlation_id: Optional correlation ID for audit.

        Returns:
            TaskClaim with the claim details.

        Raises:
            TaskNotReadyError: If the task is not in READY status.
            AgentNotExecutableError: If the agent is not executable.
            TaskAlreadyClaimedError: If the task is already claimed.
            ClaimTokenMismatchError: If the token doesn't match.
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        # Validate agent is executable
        try:
            self.registry.require_executable(agent_id)
        except AgentRegistryError as exc:
            raise AgentNotExecutableError(
                f"Agent is not executable: {agent_id}"
            ) from exc

        with self.repository.transaction(run_uuid) as transaction:
            task = transaction.get_task(task_uuid)

            # Handle existing claim first (before status check)
            if task.claim_token is not None:
                if claim_token is None:
                    raise TaskAlreadyClaimedError(
                        f"Task already claimed by {task.claimed_by}"
                    )
                if task.claim_token != claim_token:
                    raise ClaimTokenMismatchError(
                        "Claim token does not match existing claim"
                    )
                # Idempotent re-claim with same token
                claimed_at_value = task.claimed_at
                if claimed_at_value is not None and not isinstance(claimed_at_value, str):
                    claimed_at_value = claimed_at_value.isoformat()

                return TaskClaim(
                    task_id=str(task_uuid),
                    claim_token=task.claim_token,
                    claimed_by=task.claimed_by or agent_id,
                    claimed_at=claimed_at_value or utc_now().isoformat(),
                    attempt_number=task.attempt_count,
                )

            # Check task is ready (no existing claim)
            if TaskStatus(task.status) != TaskStatus.READY:
                raise TaskNotReadyError(
                    f"Task must be READY; current status: {task.status}"
                )

            # Check assigned agent matches
            if (task.assigned_agent or "") != agent_id:
                raise AgentNotExecutableError(
                    f"Task assigned to '{task.assigned_agent}', "
                    f"not '{agent_id}'"
                )

            # New claim
            new_token = claim_token or _generate_claim_token()
            now = utc_now()

            updated_task = task.model_copy(deep=True)
            updated_task.claim_token = new_token
            updated_task.claimed_by = agent_id
            updated_task.claimed_at = now
            updated_task.status = TaskStatus.RUNNING.value
            updated_task.started_at = now
            updated_task.updated_at = now
            updated_task.attempt_count = task.attempt_count + 1

            transaction.save_task(updated_task)

            event = _event(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="task.claimed",
                actor=agent_id,
                action="claim",
                details={
                    "claim_token": new_token,
                    "attempt_number": updated_task.attempt_count,
                },
                correlation_id=correlation_id,
            )
            transaction.append_event(event)

            return TaskClaim(
                task_id=str(task_uuid),
                claim_token=new_token,
                claimed_by=agent_id,
                claimed_at=now.isoformat(),
                attempt_number=updated_task.attempt_count,
            )

    def complete_claimed_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        claim_token: str,
        actor: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        """Complete a claimed task and clean up claim fields.

        Args:
            run_id: The run ID.
            task_id: The task ID.
            claim_token: The claim token from the original claim.
            actor: The actor completing the task.
            correlation_id: Optional correlation ID.

        Returns:
            Tuple of (completed task, audit event).

        Raises:
            TaskNotReadyError: If the task is not in running status.
            ClaimTokenMismatchError: If the token doesn't match.
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        with self.repository.transaction(run_uuid) as transaction:
            task = transaction.get_task(task_uuid)

            if TaskStatus(task.status) != TaskStatus.RUNNING:
                raise TaskNotReadyError(
                    f"Task must be RUNNING; current status: {task.status}"
                )

            if task.claim_token != claim_token:
                raise ClaimTokenMismatchError(
                    "Claim token does not match"
                )

            now = utc_now()
            updated_task = task.model_copy(deep=True)
            updated_task.status = TaskStatus.COMPLETED.value
            updated_task.completed_at = now
            updated_task.updated_at = now
            # Clean up claim fields
            updated_task.claim_token = None
            updated_task.claimed_by = None
            updated_task.claimed_at = None

            transaction.save_task(updated_task)

            event = _event(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="task.completed",
                actor=actor,
                action="complete",
                details={
                    "attempt_number": task.attempt_count,
                },
                correlation_id=correlation_id,
            )
            transaction.append_event(event)

            return updated_task, event

    def record_attempt_failure(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        claim_token: str,
        error: str,
        actor: str,
        correlation_id: str | None = None,
    ) -> AttemptFailureResult:
        """Record a failed attempt and determine if retry is available.

        Args:
            run_id: The run ID.
            task_id: The task ID.
            claim_token: The claim token from the original claim.
            error: The error message to persist.
            actor: The actor recording the failure.
            correlation_id: Optional correlation ID.

        Returns:
            AttemptFailureResult with the updated task and audit event.

        Raises:
            TaskNotReadyError: If the task is not in running status.
            ClaimTokenMismatchError: If the token doesn't match.
            AttemptLimitExceededError: If max_attempts is reached.
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        with self.repository.transaction(run_uuid) as transaction:
            task = transaction.get_task(task_uuid)

            if TaskStatus(task.status) != TaskStatus.RUNNING:
                raise TaskNotReadyError(
                    f"Task must be RUNNING; current status: {task.status}"
                )

            if task.claim_token != claim_token:
                raise ClaimTokenMismatchError(
                    "Claim token does not match"
                )

            # Check if we've exceeded max attempts
            if task.attempt_count >= task.max_attempts:
                # Terminal failure
                now = utc_now()
                updated_task = task.model_copy(deep=True)
                updated_task.status = TaskStatus.FAILED.value
                updated_task.completed_at = now
                updated_task.updated_at = now
                updated_task.last_error = error
                # Clean up claim fields
                updated_task.claim_token = None
                updated_task.claimed_by = None
                updated_task.claimed_at = None

                transaction.save_task(updated_task)

                event = _event(
                    run_id=run_uuid,
                    task_id=task_uuid,
                    event_type="task.failed",
                    actor=actor,
                    action="fail",
                    details={
                        "error": error,
                        "attempt_number": task.attempt_count,
                        "terminal": True,
                    },
                    correlation_id=correlation_id,
                )
                transaction.append_event(event)

                return AttemptFailureResult(
                    task=updated_task,
                    audit_event=event,
                    retry_available=False,
                    terminal_failure=True,
                )

            # Record failure, transition to FAILED (retryable)
            now = utc_now()
            updated_task = task.model_copy(deep=True)
            updated_task.status = TaskStatus.FAILED.value
            updated_task.last_error = error
            updated_task.updated_at = now
            # Clear claim to allow re-claim
            updated_task.claim_token = None
            updated_task.claimed_by = None
            updated_task.claimed_at = None

            transaction.save_task(updated_task)

            event = _event(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="task.attempt_failed",
                actor=actor,
                action="fail_attempt",
                details={
                    "error": error,
                    "attempt_number": task.attempt_count,
                    "terminal": False,
                },
                correlation_id=correlation_id,
            )
            transaction.append_event(event)

            return AttemptFailureResult(
                task=updated_task,
                audit_event=event,
                retry_available=True,
                terminal_failure=False,
            )

    def prepare_retry(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        correlation_id: str | None = None,
    ) -> RetryPreparationResult:
        """Prepare a failed task for retry by moving it back to READY.

        Args:
            run_id: The run ID.
            task_id: The task ID.
            actor: The actor preparing the retry.
            correlation_id: Optional correlation ID.

        Returns:
            RetryPreparationResult with the updated task and audit event.

        Raises:
            TaskNotReadyError: If the task is not in FAILED status.
            AttemptLimitExceededError: If the task has exhausted retries.
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        with self.repository.transaction(run_uuid) as transaction:
            task = transaction.get_task(task_uuid)

            if TaskStatus(task.status) != TaskStatus.FAILED:
                raise TaskNotReadyError(
                    f"Task must be FAILED; current status: {task.status}"
                )

            # Check if retry is allowed (not terminal)
            if task.attempt_count >= task.max_attempts:
                raise AttemptLimitExceededError(
                    f"Task has exhausted {task.max_attempts} attempts"
                )

            now = utc_now()
            updated_task = task.model_copy(deep=True)
            updated_task.status = TaskStatus.READY.value
            updated_task.updated_at = now
            # Clear last_error for clean retry
            updated_task.last_error = None

            transaction.save_task(updated_task)

            event = _event(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="task.retry_prepared",
                actor=actor,
                action="prepare_retry",
                details={
                    "attempt_number": task.attempt_count,
                },
                correlation_id=correlation_id,
            )
            transaction.append_event(event)

            return RetryPreparationResult(
                task=updated_task,
                audit_event=event,
            )
