"""Tests for atomic filesystem state persistence."""

import json
from datetime import datetime, timezone
from pathlib import Path

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


def test_cross_run_child_symlink_is_rejected_on_get_and_list(
    tmp_path: Path,
) -> None:
    repository = FileStateRepository(tmp_path / "runs")
    source = Run(objective="Source run")
    victim = Run(objective="Victim run")
    repository.create_run(source)
    repository.create_run(victim)
    source_task = Task(run_id=source.id, title="Source task")
    repository.create_task(source_task)

    source_path = (
        repository.root
        / str(source.id)
        / "tasks"
        / f"{source_task.id}.json"
    )
    victim_path = (
        repository.root
        / str(victim.id)
        / "tasks"
        / f"{source_task.id}.json"
    )
    victim_path.symlink_to(source_path)

    with pytest.raises(RecordScopeError, match="cannot be a symlink"):
        repository.get_task(victim.id, source_task.id)
    with pytest.raises(RecordScopeError, match="cannot be a symlink"):
        repository.list_tasks(victim.id)


def test_cross_run_child_payload_is_rejected_after_read(
    tmp_path: Path,
) -> None:
    repository = FileStateRepository(tmp_path / "runs")
    source = Run(objective="Source run")
    victim = Run(objective="Victim run")
    repository.create_run(source)
    repository.create_run(victim)
    source_task = Task(run_id=source.id, title="Source task")
    repository.create_task(source_task)

    victim_path = (
        repository.root
        / str(victim.id)
        / "tasks"
        / f"{source_task.id}.json"
    )
    victim_path.write_text(
        source_task.model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(RecordScopeError, match="does not match"):
        repository.get_task(victim.id, source_task.id)
    with pytest.raises(RecordScopeError, match="does not match"):
        repository.list_tasks(victim.id)


def test_symlinked_child_collection_is_rejected(tmp_path: Path) -> None:
    repository = FileStateRepository(tmp_path / "runs")
    source = Run(objective="Source run")
    victim = Run(objective="Victim run")
    repository.create_run(source)
    repository.create_run(victim)

    victim_tasks = repository.root / str(victim.id) / "tasks"
    victim_tasks.rmdir()
    victim_tasks.symlink_to(
        repository.root / str(source.id) / "tasks",
        target_is_directory=True,
    )

    with pytest.raises(RecordScopeError, match="cannot be a symlink"):
        repository.list_tasks(victim.id)


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


def test_list_runs_is_newest_first_and_bounded(tmp_path: Path) -> None:
    repository = FileStateRepository(tmp_path / "runs")
    older = Run(
        objective="Older",
        updated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
    )
    newer = Run(
        objective="Newer",
        updated_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    repository.create_run(older)
    repository.create_run(newer)

    assert repository.list_runs() == [newer, older]
    assert repository.list_runs(limit=1) == [newer]
    assert repository.list_runs(limit=0) == []

    with pytest.raises(ValueError, match="non-negative"):
        repository.list_runs(limit=-1)


def test_list_runs_ignores_noncanonical_and_symlinked_directories(
    tmp_path: Path,
) -> None:
    repository = FileStateRepository(tmp_path / "runs")
    run = Run(objective="Canonical")
    repository.create_run(run)

    ignored = repository.root / "not-a-run"
    ignored.mkdir()
    (ignored / "run.json").write_text(
        run.model_dump_json(),
        encoding="utf-8",
    )
    alias = repository.root / "run-alias"
    alias.symlink_to(repository.root / str(run.id), target_is_directory=True)

    assert repository.list_runs() == [run]
