"""Tests for the D04 application orchestration service."""

import pytest

from app.domain import (
    ApprovalStatus,
    ArtifactKind,
    MessageRole,
    RunStatus,
    TaskStatus,
)
from app.services import (
    ApprovalResolutionError,
    ArtifactRelationError,
    DependencyNotReady,
    OrchestrationService,
    RunCompletionBlocked,
)
from app.state import FileStateRepository


def build_service(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    return repository, OrchestrationService(repository)


def test_create_and_read_complete_run_snapshot(tmp_path):
    repository, service = build_service(tmp_path)

    run, run_event = service.create_run(
        objective="Prepare a launch recommendation",
        actor="founder",
        owner="founder@example.com",
        correlation_id="corr-001",
    )
    task, task_event = service.create_task(
        run.id,
        title="Draft the recommendation",
        actor="orchestrator",
        assigned_agent="chief-of-staff",
    )

    service.start_run(
        run.id,
        actor="orchestrator",
        reason="Execution started.",
    )
    service.mark_task_ready(
        run.id,
        task.id,
        actor="orchestrator",
        reason="No dependencies are pending.",
    )
    service.start_task(
        run.id,
        task.id,
        actor="chief-of-staff",
        reason="Agent accepted the task.",
    )

    message, message_event = service.append_message(
        run.id,
        task_id=task.id,
        sender="chief-of-staff",
        recipient="research-agent",
        role=MessageRole.AGENT,
        content="Validate market assumptions.",
        actor="chief-of-staff",
        correlation_id="corr-001",
    )
    decision, route_event = service.record_route_decision(
        run.id,
        task_id=task.id,
        requested_model="cofounder-auto",
        selected_model="cofounder-qwen",
        provider="qwen",
        reason="Local reasoning is sufficient.",
        candidate_models=["cofounder-qwen", "cofounder-step"],
        latency_ms=10.5,
        correlation_id="corr-001",
    )
    artifact, artifact_event = service.register_artifact(
        run.id,
        task_id=task.id,
        kind=ArtifactKind.REPORT,
        name="recommendation.md",
        uri="artifact://recommendation.md",
        created_by="chief-of-staff",
        actor="chief-of-staff",
        relation="output",
        content_type="text/markdown",
        size_bytes=1024,
    )

    snapshot = service.get_snapshot(run.id)

    assert snapshot.run.id == run.id
    assert snapshot.run.task_ids == [task.id]
    assert snapshot.run.artifact_ids == [artifact.id]
    assert snapshot.tasks[0].output_artifact_ids == [artifact.id]
    assert snapshot.messages == [message]
    assert snapshot.route_decisions == [decision]
    assert snapshot.artifacts == [artifact]
    assert run_event.event_type == "run.created"
    assert task_event.event_type == "task.created"
    assert message_event.details["content_length"] == len(message.content)
    assert message.content not in str(message_event.details)
    assert route_event.event_type == "route.recorded"
    assert artifact_event.event_type == "artifact.registered"

    persisted_task = repository.get_task(run.id, task.id)
    assert persisted_task.status == "running"


def test_dependency_gates_ready_transition(tmp_path):
    _, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Execute dependent tasks",
        actor="founder",
    )
    first, _ = service.create_task(
        run.id,
        title="First task",
        actor="orchestrator",
    )
    second, _ = service.create_task(
        run.id,
        title="Second task",
        actor="orchestrator",
        dependency_ids=[first.id],
    )

    with pytest.raises(DependencyNotReady):
        service.mark_task_ready(
            run.id,
            second.id,
            actor="orchestrator",
            reason="Attempt before dependency completion.",
        )

    service.mark_task_ready(
        run.id,
        first.id,
        actor="orchestrator",
        reason="First task is ready.",
    )
    service.start_task(
        run.id,
        first.id,
        actor="agent",
        reason="First task started.",
    )
    service.complete_task(
        run.id,
        first.id,
        actor="agent",
        reason="First task completed.",
    )

    ready, _ = service.mark_task_ready(
        run.id,
        second.id,
        actor="orchestrator",
        reason="Dependency is complete.",
    )
    assert ready.status == "ready"


def test_task_approval_approved_resumes_execution(tmp_path):
    repository, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Approve a task output",
        actor="founder",
    )
    task, _ = service.create_task(
        run.id,
        title="Prepare external action",
        actor="orchestrator",
    )
    service.mark_task_ready(
        run.id,
        task.id,
        actor="orchestrator",
        reason="Task is ready.",
    )
    service.start_task(
        run.id,
        task.id,
        actor="agent",
        reason="Task started.",
    )

    requested = service.request_approval(
        run.id,
        task_id=task.id,
        requested_by="agent",
        reason="External action needs founder approval.",
        actor="orchestrator",
    )

    assert requested.approval.status == "pending"
    assert requested.task is not None
    assert requested.task.status == "waiting_approval"
    assert requested.approval_event.event_type == "approval.requested"

    resolved = service.resolve_approval(
        run.id,
        requested.approval.id,
        decision="approved",
        decided_by="founder",
        reason="Action is approved.",
        actor="orchestrator",
    )

    assert resolved.approval.status == ApprovalStatus.APPROVED.value
    assert resolved.task is not None
    assert resolved.task.status == TaskStatus.RUNNING.value
    assert resolved.approval_event.event_type == "approval.resolved"

    persisted = repository.get_task(run.id, task.id)
    assert persisted.approval_id == requested.approval.id
    assert persisted.status == "running"


def test_task_approval_rejected_fails_task(tmp_path):
    _, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Reject unsafe task output",
        actor="founder",
    )
    task, _ = service.create_task(
        run.id,
        title="External publication",
        actor="orchestrator",
    )
    service.mark_task_ready(
        run.id,
        task.id,
        actor="orchestrator",
        reason="Task is ready.",
    )
    service.start_task(
        run.id,
        task.id,
        actor="agent",
        reason="Task started.",
    )
    requested = service.request_approval(
        run.id,
        task_id=task.id,
        requested_by="agent",
        reason="Publication approval is required.",
        actor="orchestrator",
    )

    resolved = service.resolve_approval(
        run.id,
        requested.approval.id,
        decision="rejected",
        decided_by="founder",
        reason="Claims require more evidence.",
        actor="orchestrator",
    )

    assert resolved.approval.status == "rejected"
    assert resolved.task is not None
    assert resolved.task.status == "failed"


def test_run_level_approval_resumes_run(tmp_path):
    _, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Approve overall execution",
        actor="founder",
    )
    service.start_run(
        run.id,
        actor="orchestrator",
        reason="Run started.",
    )

    requested = service.request_approval(
        run.id,
        requested_by="orchestrator",
        reason="Founder decision is required.",
        actor="orchestrator",
    )

    assert requested.run is not None
    assert requested.run.status == RunStatus.WAITING_APPROVAL.value

    resolved = service.resolve_approval(
        run.id,
        requested.approval.id,
        decision="approved",
        decided_by="founder",
        reason="Continue execution.",
        actor="orchestrator",
    )

    assert resolved.run is not None
    assert resolved.run.status == RunStatus.RUNNING.value


def test_resolved_approval_cannot_be_resolved_again(tmp_path):
    _, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Resolve once",
        actor="founder",
    )
    service.start_run(
        run.id,
        actor="orchestrator",
        reason="Run started.",
    )
    requested = service.request_approval(
        run.id,
        requested_by="orchestrator",
        reason="Decision required.",
        actor="orchestrator",
    )
    service.resolve_approval(
        run.id,
        requested.approval.id,
        decision="approved",
        decided_by="founder",
        reason="Approved.",
        actor="orchestrator",
    )

    with pytest.raises(ApprovalResolutionError):
        service.resolve_approval(
            run.id,
            requested.approval.id,
            decision="approved",
            decided_by="founder",
            reason="Duplicate decision.",
            actor="orchestrator",
        )


def test_run_completion_requires_all_tasks_completed_or_cancelled(tmp_path):
    _, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Complete only after tasks",
        actor="founder",
    )
    task, _ = service.create_task(
        run.id,
        title="Required task",
        actor="orchestrator",
    )
    service.start_run(
        run.id,
        actor="orchestrator",
        reason="Run started.",
    )

    with pytest.raises(RunCompletionBlocked):
        service.complete_run(
            run.id,
            actor="orchestrator",
            reason="Premature completion.",
        )

    service.mark_task_ready(
        run.id,
        task.id,
        actor="orchestrator",
        reason="Task ready.",
    )
    service.start_task(
        run.id,
        task.id,
        actor="agent",
        reason="Task started.",
    )
    service.complete_task(
        run.id,
        task.id,
        actor="agent",
        reason="Task completed.",
    )
    completed, _ = service.complete_run(
        run.id,
        actor="orchestrator",
        reason="All tasks completed.",
    )

    assert completed.status == "completed"


@pytest.mark.parametrize(
    ("relation", "task_id"),
    [
        ("run", "00000000-0000-0000-0000-000000000001"),
        ("input", None),
        ("output", None),
    ],
)
def test_invalid_artifact_relation_is_side_effect_free(
    tmp_path,
    relation,
    task_id,
):
    _, service = build_service(tmp_path)

    run, _ = service.create_run(
        objective="Validate artifact relationships",
        actor="founder",
    )

    with pytest.raises(ArtifactRelationError):
        service.register_artifact(
            run.id,
            task_id=task_id,
            kind=ArtifactKind.DATA,
            name="invalid.json",
            uri="artifact://invalid.json",
            created_by="agent",
            actor="agent",
            relation=relation,
        )

    snapshot = service.get_snapshot(run.id)
    assert snapshot.artifacts == []
    assert snapshot.run.artifact_ids == []
