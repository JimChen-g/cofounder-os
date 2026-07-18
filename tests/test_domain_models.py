"""Tests for the canonical CoFounder OS domain model."""

import json
from datetime import timezone

import pytest
from pydantic import ValidationError

from app.domain import (
    AgentMessage,
    Approval,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    AuditEvent,
    AuditOutcome,
    MessageRole,
    RouteDecision,
    Run,
    RunStatus,
    Task,
    TaskStatus,
)


def json_payload(model):
    """Return a JSON-compatible payload on Pydantic v1 and v2."""

    return json.loads(model.json())


def test_run_defaults_are_valid_and_isolated():
    first = Run(objective="Prepare a product launch plan")
    second = Run(objective="Review launch risks")

    assert first.id != second.id
    assert first.status == RunStatus.QUEUED.value
    assert first.schema_version == "1.0"
    assert first.created_at.tzinfo is not None
    assert first.created_at.utcoffset() == timezone.utc.utcoffset(first.created_at)

    first.task_ids.append(second.id)
    first.metadata["source"] = "test"

    assert second.task_ids == []
    assert second.metadata == {}


def test_complete_domain_graph_serializes_with_stable_references():
    run = Run(
        objective="Create and approve a launch brief",
        owner="founder@example.com",
        status=RunStatus.RUNNING,
    )

    task = Task(
        run_id=run.id,
        title="Draft launch brief",
        description="Create the first decision-ready draft.",
        status=TaskStatus.RUNNING,
        assigned_agent="chief-of-staff",
    )

    message = AgentMessage(
        run_id=run.id,
        task_id=task.id,
        sender="chief-of-staff",
        recipient="research-agent",
        role=MessageRole.AGENT,
        content="Validate the market assumptions.",
        correlation_id="corr-launch-001",
    )

    route = RouteDecision(
        run_id=run.id,
        task_id=task.id,
        requested_model="cofounder-auto",
        selected_model="cofounder-qwen",
        provider="qwen",
        reason="Local reasoning is sufficient for this task.",
        candidate_models=["cofounder-qwen", "cofounder-step"],
        fallback_used=False,
        latency_ms=125.5,
    )

    approval = Approval(
        run_id=run.id,
        task_id=task.id,
        status=ApprovalStatus.PENDING,
        requested_by="chief-of-staff",
        reason="External publication requires founder approval.",
    )

    artifact = Artifact(
        run_id=run.id,
        task_id=task.id,
        kind=ArtifactKind.REPORT,
        name="launch-brief.md",
        uri="artifact://runs/launch/launch-brief.md",
        content_type="text/markdown",
        checksum_sha256="0" * 64,
        size_bytes=2048,
        created_by="chief-of-staff",
    )

    event = AuditEvent(
        run_id=run.id,
        task_id=task.id,
        event_type="route.selected",
        actor="router",
        action="select_model",
        target_type="model",
        target_id="cofounder-qwen",
        outcome=AuditOutcome.SUCCESS,
        correlation_id=message.correlation_id,
        details={"route_decision_id": str(route.id)},
    )

    run.task_ids.append(task.id)
    run.artifact_ids.append(artifact.id)
    task.approval_id = approval.id
    task.output_artifact_ids.append(artifact.id)

    payloads = [
        json_payload(run),
        json_payload(task),
        json_payload(message),
        json_payload(route),
        json_payload(approval),
        json_payload(artifact),
        json_payload(event),
    ]

    assert all(payload["schema_version"] == "1.0" for payload in payloads)
    assert payloads[0]["status"] == "running"
    assert payloads[1]["run_id"] == str(run.id)
    assert payloads[2]["role"] == "agent"
    assert payloads[3]["selected_model"] == "cofounder-qwen"
    assert payloads[4]["status"] == "pending"
    assert payloads[5]["kind"] == "report"
    assert payloads[6]["outcome"] == "success"


@pytest.mark.parametrize(
    ("factory", "kwargs"),
    [
        (Run, {"objective": ""}),
        (Task, {"run_id": "not-a-uuid", "title": "Task"}),
        (
            AgentMessage,
            {
                "run_id": "not-a-uuid",
                "sender": "agent",
                "content": "message",
            },
        ),
        (
            RouteDecision,
            {
                "run_id": "not-a-uuid",
                "selected_model": "model",
                "provider": "provider",
                "reason": "reason",
            },
        ),
    ],
)
def test_invalid_required_values_are_rejected(factory, kwargs):
    with pytest.raises(ValidationError):
        factory(**kwargs)


def test_unknown_fields_are_rejected():
    with pytest.raises(ValidationError):
        Run(
            objective="Reject undeclared state",
            undeclared_state=True,
        )


def test_non_negative_numeric_constraints():
    run = Run(objective="Validate numeric constraints")

    with pytest.raises(ValidationError):
        RouteDecision(
            run_id=run.id,
            selected_model="cofounder-qwen",
            provider="qwen",
            reason="test",
            latency_ms=-1,
        )

    with pytest.raises(ValidationError):
        Artifact(
            run_id=run.id,
            kind=ArtifactKind.DATA,
            name="dataset.json",
            uri="artifact://dataset.json",
            size_bytes=-1,
            created_by="research-agent",
        )
