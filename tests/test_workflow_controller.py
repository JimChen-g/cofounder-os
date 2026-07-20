"""D10 task loop, retry, recovery, approval, and replay tests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Sequence
from uuid import uuid4

import pytest

from app.artifacts import FileArtifactStore
from app.clients.gateway import GatewayCompletion
from app.domain import ApprovalStatus, RunStatus, TaskStatus
from app.policy import DeterministicPolicyGate
from app.services import (
    AgentExecutionService,
    FinanceAgentService,
    OrchestrationService,
    ProductAgentService,
    SYNTHESIS_TASK_TYPE,
    WorkflowController,
    WorkflowControllerError,
)
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer
from tests.test_finance_agent import VALID_FINANCE_RESULT
from tests.test_product_agent import VALID_RESULT_DICT


class SequenceGateway:
    def __init__(self, responses: Sequence[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, **kwargs) -> GatewayCompletion:
        self.calls += 1
        if not self.responses:
            raise AssertionError("Unexpected Gateway call")
        return GatewayCompletion(
            content=self.responses.pop(0),
            requested_model=kwargs["model"],
            selected_provider="qwen",
            selected_model=kwargs["model"],
            routing_reason="workflow-test",
            request_id=f"workflow-{self.calls}",
        )


def _controller(tmp_path, product_responses, finance_responses):
    repository = FileStateRepository(tmp_path / "runs")
    orchestration = OrchestrationService(repository)
    artifact_store = FileArtifactStore(tmp_path / "artifacts")
    product_gateway = SequenceGateway(product_responses)
    finance_gateway = SequenceGateway(finance_responses)
    product_service = ProductAgentService(
        product_gateway,
        artifact_store,
        orchestration,
    )
    finance_service = FinanceAgentService(
        finance_gateway,
        artifact_store,
        orchestration,
    )
    controller = WorkflowController(
        orchestration=orchestration,
        agent_execution=AgentExecutionService(repository),
        artifact_store=artifact_store,
        product_agent_service=product_service,
        finance_agent_service=finance_service,
        artifact_synthesizer=ArtifactSynthesizer(
            artifact_store,
            orchestration,
        ),
        policy_gate=DeterministicPolicyGate(),
    )
    return (
        controller,
        orchestration,
        repository,
        artifact_store,
        product_gateway,
        finance_gateway,
        product_service,
        finance_service,
    )


def _task(
    orchestration,
    run_id,
    *,
    title,
    assigned_agent,
    dependencies=None,
    metadata=None,
    max_attempts=2,
):
    task, _ = orchestration.create_task(
        run_id,
        title=title,
        description=f"Execute {title}",
        assigned_agent=assigned_agent,
        dependency_ids=dependencies,
        metadata=metadata,
        actor="executive-orchestrator",
    )
    task.max_attempts = max_attempts
    orchestration.repository.save_task(task)
    return task


@pytest.mark.asyncio
async def test_complete_workflow_and_end_to_end_replay(tmp_path):
    (
        controller,
        orchestration,
        _,
        _,
        product_gateway,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [json.dumps(VALID_RESULT_DICT)],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(
        objective="Decide whether to launch",
        actor="founder",
    )
    product = _task(
        orchestration,
        run.id,
        title="Product analysis",
        assigned_agent="product-agent",
    )
    finance = _task(
        orchestration,
        run.id,
        title="Finance analysis",
        assigned_agent="finance-agent",
    )
    synthesis = _task(
        orchestration,
        run.id,
        title="Decision artifacts",
        assigned_agent="executive-orchestrator",
        dependencies=[product.id, finance.id],
        metadata={"task_type": SYNTHESIS_TASK_TYPE},
    )

    result = await controller.run_until_terminal(run.id)
    assert result.status == RunStatus.COMPLETED
    assert {task.id for task in result.snapshot.tasks} == {
        product.id,
        finance.id,
        synthesis.id,
    }
    assert all(
        TaskStatus(task.status) == TaskStatus.COMPLETED
        for task in result.snapshot.tasks
    )
    assert len(result.snapshot.artifacts) == 9
    assert product_gateway.calls == 1
    assert finance_gateway.calls == 1

    replay = await controller.run_until_terminal(run.id)
    assert replay.status == RunStatus.COMPLETED
    assert replay.replayed is True
    assert replay.cycles == 0
    assert len(replay.snapshot.artifacts) == 9
    assert product_gateway.calls == 1
    assert finance_gateway.calls == 1


@pytest.mark.asyncio
async def test_completed_replay_rejects_corrupt_artifact(tmp_path):
    (
        controller,
        orchestration,
        _,
        artifact_store,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
    )

    completed = await controller.run_until_terminal(run.id)
    assert completed.status == RunStatus.COMPLETED
    artifact_path = (
        artifact_store.root
        / "runs"
        / str(run.id)
        / "artifacts"
        / "tasks"
        / str(finance.id)
        / "finance-brief"
        / "finance-brief.json"
    )
    artifact_path.write_text("{}", encoding="utf-8")

    with pytest.raises(WorkflowControllerError, match="integrity"):
        await controller.run_until_terminal(run.id)
    assert finance_gateway.calls == 1


@pytest.mark.asyncio
async def test_retry_is_bounded_then_succeeds(tmp_path):
    invalid = "{}"
    (
        controller,
        orchestration,
        _,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [invalid, invalid, json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
    )

    result = await controller.run_until_terminal(run.id)
    current = orchestration.repository.get_task(run.id, finance.id)
    assert result.status == RunStatus.COMPLETED
    assert TaskStatus(current.status) == TaskStatus.COMPLETED
    assert current.attempt_count == 2
    assert finance.id in result.retried_task_ids
    assert finance_gateway.calls == 3


@pytest.mark.asyncio
async def test_retry_exhaustion_is_terminal_for_task_and_run(tmp_path):
    (
        controller,
        orchestration,
        _,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(tmp_path, [], ["{}", "{}", "{}", "{}"])
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
        max_attempts=2,
    )

    result = await controller.run_until_terminal(run.id)
    current = orchestration.repository.get_task(run.id, finance.id)
    assert result.status == RunStatus.FAILED
    assert result.terminal_failure is True
    assert TaskStatus(current.status) == TaskStatus.FAILED
    assert current.attempt_count == 2
    assert finance_gateway.calls == 4


@pytest.mark.asyncio
async def test_reconciliation_completes_persisted_outputs_without_reexecution(
    tmp_path,
):
    (
        controller,
        orchestration,
        repository,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
    )
    orchestration.start_run(
        run.id,
        actor="workflow-controller",
        reason="setup",
    )
    orchestration.mark_task_ready(
        run.id,
        finance.id,
        actor="workflow-controller",
        reason="setup",
    )
    claim = controller.agent_execution.claim_task(
        run.id,
        finance.id,
        agent_id="finance-agent",
    )
    request = controller._policy_action(finance)
    assert request.operation == "execute"
    await controller._dispatch(
        repository.get_task(run.id, finance.id),
        correlation_id="reconcile-test",
    )
    assert claim.claim_token
    assert finance_gateway.calls == 1

    result = await controller.run_until_terminal(run.id)
    assert result.status == RunStatus.COMPLETED
    assert finance.id in result.reconciled_task_ids
    assert finance_gateway.calls == 1


@pytest.mark.asyncio
async def test_policy_approval_pauses_then_resumes_claimed_task(tmp_path):
    (
        controller,
        orchestration,
        repository,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
        metadata={
            "policy_action": {
                "actor": "finance-agent",
                "operation": "write",
                "tool_name": "external-budget-system",
                "external_write": True,
            }
        },
    )

    waiting = await controller.run_until_terminal(run.id)
    assert waiting.stalled is True
    assert finance_gateway.calls == 0
    assert len(waiting.approval_ids) == 1
    approval_id = waiting.approval_ids[0]
    approval = orchestration.repository.get_approval(run.id, approval_id)
    assert ApprovalStatus(approval.status) == ApprovalStatus.PENDING
    assert approval.expires_at is not None
    expires_at = approval.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    assert expires_at > datetime.now(timezone.utc)
    assert len(approval.metadata["policy_action_sha256"]) == 64
    assert approval.metadata["policy_rule_ids"] == [
        "approval.external_write"
    ]
    assert approval.metadata["policy_correlation_id"]

    orchestration.resolve_approval(
        run.id,
        approval_id,
        decision="approved",
        decided_by="founder",
        reason="Approved bounded write",
        actor="founder",
    )
    completed = await controller.run_until_terminal(run.id)
    assert completed.status == RunStatus.COMPLETED
    assert finance_gateway.calls == 1
    persisted = repository.get_task(run.id, completed.snapshot.tasks[0].id)
    assert TaskStatus(persisted.status) == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_approval_cannot_authorize_changed_policy_action(tmp_path):
    (
        controller,
        orchestration,
        repository,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
        metadata={
            "policy_action": {
                "actor": "finance-agent",
                "operation": "write",
                "tool_name": "external-budget-system",
                "external_write": True,
            }
        },
    )
    waiting = await controller.run_until_terminal(run.id)
    orchestration.resolve_approval(
        run.id,
        waiting.approval_ids[0],
        decision="approved",
        decided_by="founder",
        reason="Approved original bounded write",
        actor="founder",
    )
    changed = repository.get_task(run.id, finance.id)
    changed.metadata["policy_action"] = {
        "actor": "finance-agent",
        "operation": "execute",
        "tool_name": "shell",
        "command": "rm -rf ./data",
    }
    repository.save_task(changed)

    result = await controller.run_until_terminal(run.id)
    current = repository.get_task(run.id, finance.id)
    assert result.status == RunStatus.FAILED
    assert TaskStatus(current.status) == TaskStatus.FAILED
    assert finance_gateway.calls == 0


@pytest.mark.asyncio
async def test_expired_approval_cannot_resume_execution(tmp_path):
    (
        controller,
        orchestration,
        repository,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
        metadata={
            "policy_action": {
                "actor": "finance-agent",
                "operation": "write",
                "tool_name": "external-budget-system",
                "external_write": True,
            }
        },
    )
    waiting = await controller.run_until_terminal(run.id)
    original_id = waiting.approval_ids[0]
    approval = repository.get_approval(run.id, original_id)
    approval.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    repository.save_approval(approval)
    orchestration.resolve_approval(
        run.id,
        original_id,
        decision="approved",
        decided_by="founder",
        reason="Late approval",
        actor="founder",
    )

    result = await controller.run_until_terminal(run.id)
    current = repository.get_task(run.id, finance.id)
    assert result.stalled is True
    assert TaskStatus(current.status) == TaskStatus.WAITING_APPROVAL
    assert current.approval_id != original_id
    assert finance_gateway.calls == 0


@pytest.mark.asyncio
async def test_approval_requires_the_policy_selected_reviewer(tmp_path):
    (
        controller,
        orchestration,
        repository,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
        metadata={
            "policy_action": {
                "actor": "finance-agent",
                "operation": "read",
                "tool_name": "private-data-read",
                "private_data": True,
            }
        },
    )
    waiting = await controller.run_until_terminal(run.id)
    original_id = waiting.approval_ids[0]
    original = repository.get_approval(run.id, original_id)
    assert original.metadata["reviewer_required"] == "security"
    orchestration.resolve_approval(
        run.id,
        original_id,
        decision="approved",
        decided_by="founder",
        reason="Founder is not the selected security reviewer",
        actor="founder",
    )

    result = await controller.run_until_terminal(run.id)
    current = repository.get_task(run.id, finance.id)
    replacement = repository.get_approval(run.id, current.approval_id)
    assert result.stalled is True
    assert current.approval_id != original_id
    assert replacement.metadata["reviewer_required"] == "security"
    assert finance_gateway.calls == 0


@pytest.mark.asyncio
async def test_missing_declared_input_artifact_fails_before_gateway(tmp_path):
    (
        controller,
        orchestration,
        repository,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(
        tmp_path,
        [],
        [json.dumps(VALID_FINANCE_RESULT)],
    )
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Finance",
        assigned_agent="finance-agent",
    )
    finance.input_artifact_ids = [uuid4()]
    repository.save_task(finance)

    result = await controller.run_until_terminal(run.id)
    current = repository.get_task(run.id, finance.id)
    assert result.status == RunStatus.FAILED
    assert TaskStatus(current.status) == TaskStatus.FAILED
    assert "does not exist" in (current.last_error or "")
    assert finance_gateway.calls == 0


@pytest.mark.asyncio
async def test_policy_denial_causes_terminal_failure_without_gateway_call(
    tmp_path,
):
    (
        controller,
        orchestration,
        _,
        _,
        _,
        finance_gateway,
        _,
        _,
    ) = _controller(tmp_path, [], [])
    run, _ = orchestration.create_run(objective="Finance", actor="founder")
    finance = _task(
        orchestration,
        run.id,
        title="Dangerous",
        assigned_agent="finance-agent",
        metadata={
            "policy_action": {
                "actor": "finance-agent",
                "operation": "execute",
                "tool_name": "shell",
                "command": "rm -rf ./data",
            }
        },
    )

    result = await controller.run_until_terminal(run.id)
    current = orchestration.repository.get_task(run.id, finance.id)
    assert result.status == RunStatus.FAILED
    assert TaskStatus(current.status) == TaskStatus.FAILED
    assert finance_gateway.calls == 0


@pytest.mark.asyncio
async def test_unimplemented_agent_is_enforced_as_terminal_failure(tmp_path):
    (
        controller,
        orchestration,
        _,
        _,
        product_gateway,
        finance_gateway,
        _,
        _,
    ) = _controller(tmp_path, [], [])
    run, _ = orchestration.create_run(objective="Research", actor="founder")
    research = _task(
        orchestration,
        run.id,
        title="Unsupported research",
        assigned_agent="research-agent",
    )

    result = await controller.run_until_terminal(run.id)
    current = orchestration.repository.get_task(run.id, research.id)
    assert result.status == RunStatus.FAILED
    assert TaskStatus(current.status) == TaskStatus.FAILED
    assert "not executable" in (current.last_error or "").lower() or (
        current.attempt_count == 0
    )
    assert product_gateway.calls == 0
    assert finance_gateway.calls == 0
