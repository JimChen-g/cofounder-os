"""Lifecycle state machine for CoFounder OS runs and tasks."""

from __future__ import annotations

from typing import Dict, FrozenSet
from uuid import UUID

from app.domain import (
    AuditEvent,
    AuditOutcome,
    Run,
    RunStatus,
    Task,
    TaskStatus,
    utc_now,
)
from app.state.repository import FileStateRepository


class InvalidTransition(ValueError):
    """Raised when a requested lifecycle transition is not allowed."""


RUN_TRANSITIONS: Dict[RunStatus, FrozenSet[RunStatus]] = {
    RunStatus.QUEUED: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_APPROVAL,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {
            RunStatus.RUNNING,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

TASK_TRANSITIONS: Dict[TaskStatus, FrozenSet[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.READY: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.BLOCKED,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}

RUN_TERMINAL = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }
)

TASK_TERMINAL = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    }
)


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _validate_transition(
    current_status,
    target_status,
    transitions,
    target_type: str,
) -> None:
    allowed = transitions[current_status]
    if target_status not in allowed:
        allowed_text = ", ".join(sorted(status.value for status in allowed))
        if not allowed_text:
            allowed_text = "none"

        raise InvalidTransition(
            f"Invalid {target_type} transition "
            f"{current_status.value} -> {target_status.value}; "
            f"allowed targets: {allowed_text}"
        )


class LifecycleStateMachine:
    """Apply validated status transitions and append audit events."""

    def __init__(self, repository: FileStateRepository) -> None:
        self.repository = repository

    def transition_run(
        self,
        run_id: UUID | str,
        target_status: RunStatus | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        normalized_actor = _required_text(actor, "actor")
        normalized_reason = _required_text(reason, "reason")
        target = RunStatus(target_status)

        with self.repository.transaction(run_id) as transaction:
            current_run = transaction.get_run()
            current = RunStatus(current_run.status)

            _validate_transition(
                current,
                target,
                RUN_TRANSITIONS,
                "run",
            )

            now = utc_now()
            updated = current_run.model_copy(deep=True)
            updated.status = target.value
            updated.updated_at = now

            if target == RunStatus.RUNNING and updated.started_at is None:
                updated.started_at = now

            if target in RUN_TERMINAL:
                updated.completed_at = now

            event = AuditEvent(
                run_id=updated.id,
                event_type="run.status_changed",
                actor=normalized_actor,
                action="transition_status",
                target_type="run",
                target_id=str(updated.id),
                outcome=AuditOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "from_status": current.value,
                    "to_status": target.value,
                    "reason": normalized_reason,
                },
            )

            transaction.save_run(updated)
            transaction.append_event(event)
            return updated, event

    def transition_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        target_status: TaskStatus | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        normalized_actor = _required_text(actor, "actor")
        normalized_reason = _required_text(reason, "reason")
        target = TaskStatus(target_status)

        with self.repository.transaction(run_id) as transaction:
            current_task = transaction.get_task(task_id)
            current = TaskStatus(current_task.status)

            _validate_transition(
                current,
                target,
                TASK_TRANSITIONS,
                "task",
            )

            now = utc_now()
            updated = current_task.model_copy(deep=True)
            updated.status = target.value
            updated.updated_at = now

            if target == TaskStatus.RUNNING and updated.started_at is None:
                updated.started_at = now

            if target in TASK_TERMINAL:
                updated.completed_at = now

            event = AuditEvent(
                run_id=updated.run_id,
                task_id=updated.id,
                event_type="task.status_changed",
                actor=normalized_actor,
                action="transition_status",
                target_type="task",
                target_id=str(updated.id),
                outcome=AuditOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "from_status": current.value,
                    "to_status": target.value,
                    "reason": normalized_reason,
                },
            )

            transaction.save_task(updated)
            transaction.append_event(event)
            return updated, event
