"""Tests for atomic filesystem state persistence."""

import json

import pytest

from app.domain import (
    AgentMessage,
    Approval,
    Artifact,
    ArtifactKind,
    AuditEvent,
    AuditOutcome,
    RouteDecision,
    Run,
    Task,
)
from app.state import (
    FileStateRepository,
    RecordAlreadyExists,
    RecordNotFound,
    RecordScopeError,
)


def test_run_round_trip_and_atomic_update(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    run = Run(objective="Build an execution plan")

    repository.create_run(run)
    loaded = repository.get_run(run.id)

    assert loaded == run

    loaded.owner = "founder@example.com"
    repository.save_run(loaded)

    updated = repository.get_run(run.id)
    assert updated.owner == "founder@example.com"

    run_dir = tmp_path / "runs" / str(run.id)
    assert (run_dir / "run.json").is_file()
    assert list(run_dir.glob("*.tmp")) == []

    payload = json.loads(
        (run_dir / "run.json").read_text(encoding="utf-8")
    )
    assert payload["id"] == str(run.id)


def test_duplicate_and_missing_records_are_rejected(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    run = Run(objective="Reject duplicate state")

    repository.create_run(run)

    with pytest.raises(RecordAlreadyExists):
        repository.create_run(run)

    missing_run = Run(objective="Missing state")

    with pytest.raises(RecordNotFound):
        repository.get_run(missing_run.id)


def test_all_child_record_types_round_trip(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    run = Run(objective="Persist all canonical records")
    repository.create_run(run)

    task = Task(
        run_id=run.id,
        title="Create records",
    )
    message = AgentMessage(
        run_id=run.id,
        task_id=task.id,
        sender="chief-of-staff",
        content="Create the decision record.",
    )
    decision = RouteDecision(
        run_id=run.id,
        task_id=task.id,
        selected_model="cofounder-qwen",
        provider="qwen",
        reason="Local execution is sufficient.",
    )
    approval = Approval(
        run_id=run.id,
        task_id=task.id,
        requested_by="chief-of-staff",
        reason="Publication requires approval.",
    )
    artifact = Artifact(
        run_id=run.id,
        task_id=task.id,
        kind=ArtifactKind.REPORT,
        name="plan.md",
        uri="artifact://plan.md",
        created_by="chief-of-staff",
    )

    repository.create_task(task)
    repository.create_message(message)
    repository.create_route_decision(decision)
    repository.create_approval(approval)
    repository.create_artifact(artifact)

    assert repository.get_task(run.id, task.id) == task
    assert repository.get_message(run.id, message.id) == message
    assert repository.get_route_decision(run.id, decision.id) == decision
    assert repository.get_approval(run.id, approval.id) == approval
    assert repository.get_artifact(run.id, artifact.id) == artifact

    assert repository.list_tasks(run.id) == [task]
    assert repository.list_messages(run.id) == [message]
    assert repository.list_route_decisions(run.id) == [decision]
    assert repository.list_approvals(run.id) == [approval]
    assert repository.list_artifacts(run.id) == [artifact]


def test_cross_run_record_is_rejected(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    first = Run(objective="First run")
    second = Run(objective="Second run")

    repository.create_run(first)
    repository.create_run(second)

    wrong_task = Task(
        run_id=second.id,
        title="Wrong scope",
    )

    with repository.transaction(first.id) as transaction:
        with pytest.raises(RecordScopeError):
            transaction.create_task(wrong_task)


def test_append_only_event_ledger_preserves_order_and_limit(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    run = Run(objective="Record state transitions")
    repository.create_run(run)

    events = [
        AuditEvent(
            run_id=run.id,
            event_type=f"test.event.{index}",
            actor="test",
            action="append",
            target_type="run",
            target_id=str(run.id),
            outcome=AuditOutcome.SUCCESS,
            details={"index": index},
        )
        for index in range(3)
    ]

    for event in events:
        repository.append_event(event)

    assert repository.list_events(run.id) == events
    assert repository.list_events(run.id, limit=2) == events[-2:]
    assert repository.list_events(run.id, limit=0) == []

    event_file = (
        tmp_path
        / "runs"
        / str(run.id)
        / "events.jsonl"
    )
    assert len(event_file.read_text(encoding="utf-8").splitlines()) == 3


def test_child_record_requires_existing_run(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    missing = Run(objective="Not persisted")
    task = Task(
        run_id=missing.id,
        title="Cannot be persisted",
    )

    with pytest.raises(RecordNotFound):
        repository.create_task(task)
