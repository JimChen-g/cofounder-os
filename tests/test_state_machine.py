"""Tests for run and task lifecycle transitions."""

import pytest

from app.domain import Run, RunStatus, Task, TaskStatus
from app.state import (
    FileStateRepository,
    InvalidTransition,
    LifecycleStateMachine,
)


def build_machine(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    machine = LifecycleStateMachine(repository)
    return repository, machine


def test_run_lifecycle_and_audit_events(tmp_path):
    repository, machine = build_machine(tmp_path)
    run = Run(objective="Complete a controlled run")
    repository.create_run(run)

    running, first_event = machine.transition_run(
        run.id,
        RunStatus.RUNNING,
        actor="orchestrator",
        reason="Execution started.",
        correlation_id="corr-run-1",
    )

    assert running.status == "running"
    assert running.started_at is not None
    assert running.completed_at is None
    assert first_event.details["from_status"] == "queued"
    assert first_event.details["to_status"] == "running"

    waiting, _ = machine.transition_run(
        run.id,
        RunStatus.WAITING_APPROVAL,
        actor="orchestrator",
        reason="External action requires approval.",
    )
    assert waiting.status == "waiting_approval"

    resumed, _ = machine.transition_run(
        run.id,
        RunStatus.RUNNING,
        actor="founder",
        reason="Approval granted.",
    )
    assert resumed.status == "running"
    assert resumed.started_at == running.started_at

    completed, final_event = machine.transition_run(
        run.id,
        RunStatus.COMPLETED,
        actor="orchestrator",
        reason="All tasks completed.",
    )

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert final_event.event_type == "run.status_changed"
    assert final_event.target_id == str(run.id)

    events = repository.list_events(run.id)
    assert [event.details["to_status"] for event in events] == [
        "running",
        "waiting_approval",
        "running",
        "completed",
    ]


def test_invalid_run_transition_is_side_effect_free(tmp_path):
    repository, machine = build_machine(tmp_path)
    run = Run(objective="Reject invalid completion")
    repository.create_run(run)

    with pytest.raises(InvalidTransition):
        machine.transition_run(
            run.id,
            RunStatus.COMPLETED,
            actor="orchestrator",
            reason="Cannot complete before running.",
        )

    persisted = repository.get_run(run.id)
    assert persisted.status == "queued"
    assert persisted.started_at is None
    assert persisted.completed_at is None
    assert repository.list_events(run.id) == []


def test_task_lifecycle_and_audit_events(tmp_path):
    repository, machine = build_machine(tmp_path)
    run = Run(objective="Complete a controlled task")
    task = Task(
        run_id=run.id,
        title="Prepare a decision memo",
    )

    repository.create_run(run)
    repository.create_task(task)

    ready, _ = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.READY,
        actor="orchestrator",
        reason="Dependencies are satisfied.",
    )
    assert ready.status == "ready"

    running, _ = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.RUNNING,
        actor="chief-of-staff",
        reason="Agent accepted the task.",
    )
    assert running.status == "running"
    assert running.started_at is not None

    waiting, _ = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.WAITING_APPROVAL,
        actor="chief-of-staff",
        reason="Founder decision is required.",
    )
    assert waiting.status == "waiting_approval"

    resumed, _ = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.RUNNING,
        actor="founder",
        reason="Founder approved the output.",
    )
    assert resumed.started_at == running.started_at

    completed, event = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.COMPLETED,
        actor="chief-of-staff",
        reason="Approved output was delivered.",
    )

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert event.event_type == "task.status_changed"
    assert event.task_id == task.id

    events = repository.list_events(run.id)
    assert [item.details["to_status"] for item in events] == [
        "ready",
        "running",
        "waiting_approval",
        "running",
        "completed",
    ]


def test_blocked_task_can_return_to_ready(tmp_path):
    repository, machine = build_machine(tmp_path)
    run = Run(objective="Recover a blocked task")
    task = Task(
        run_id=run.id,
        title="Wait for an input",
    )

    repository.create_run(run)
    repository.create_task(task)

    blocked, _ = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.BLOCKED,
        actor="orchestrator",
        reason="Required input is unavailable.",
    )
    assert blocked.status == "blocked"

    ready, _ = machine.transition_task(
        run.id,
        task.id,
        TaskStatus.READY,
        actor="orchestrator",
        reason="Required input is now available.",
    )
    assert ready.status == "ready"


def test_terminal_task_rejects_further_transitions(tmp_path):
    repository, machine = build_machine(tmp_path)
    run = Run(objective="Protect terminal task state")
    task = Task(
        run_id=run.id,
        title="Terminal task",
    )

    repository.create_run(run)
    repository.create_task(task)

    machine.transition_task(
        run.id,
        task.id,
        TaskStatus.READY,
        actor="orchestrator",
        reason="Task is ready.",
    )
    machine.transition_task(
        run.id,
        task.id,
        TaskStatus.RUNNING,
        actor="agent",
        reason="Task execution started.",
    )
    machine.transition_task(
        run.id,
        task.id,
        TaskStatus.COMPLETED,
        actor="agent",
        reason="Task execution completed.",
    )

    with pytest.raises(InvalidTransition):
        machine.transition_task(
            run.id,
            task.id,
            TaskStatus.RUNNING,
            actor="agent",
            reason="A completed task cannot restart.",
        )

    persisted = repository.get_task(run.id, task.id)
    assert persisted.status == "completed"
    assert len(repository.list_events(run.id)) == 3


@pytest.mark.parametrize(
    ("actor", "reason", "expected_field"),
    [
        ("", "Valid reason", "actor"),
        ("orchestrator", "", "reason"),
    ],
)
def test_actor_and_reason_are_required(
    tmp_path,
    actor,
    reason,
    expected_field,
):
    repository, machine = build_machine(tmp_path)
    run = Run(objective="Validate transition attribution")
    repository.create_run(run)

    with pytest.raises(ValueError, match=expected_field):
        machine.transition_run(
            run.id,
            RunStatus.RUNNING,
            actor=actor,
            reason=reason,
        )

    assert repository.get_run(run.id).status == "queued"
    assert repository.list_events(run.id) == []
