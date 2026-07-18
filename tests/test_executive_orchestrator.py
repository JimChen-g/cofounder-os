"""Tests for the bounded D05 Executive Orchestrator."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from app.agents import AgentRegistry
from app.clients import GatewayCompletion
from app.domain import ApprovalStatus, RunStatus
from app.orchestrators import (
    ExecutiveOrchestrator,
    ExecutivePlan,
    ExecutivePlanningResult,
    PlanValidationError,
)
from app.services import OrchestrationService
from app.state import FileStateRepository


def valid_plan_payload(approval_required=True):
    return {
        "objective": "Evaluate a new product launch",
        "summary": (
            "Assess market evidence, unit economics, and synthesize a "
            "decision."
        ),
        "tasks": [
            {
                "key": "market-research",
                "title": "Analyze market demand",
                "description": (
                    "Evaluate target users, competitors, and demand signals."
                ),
                "assigned_agent": "product-agent",
                "dependency_keys": [],
                "deliverable": "Market evidence brief",
                "requires_approval": False,
            },
            {
                "key": "financial-case",
                "title": "Build financial case",
                "description": (
                    "Model revenue, cost, and downside scenarios."
                ),
                "assigned_agent": "finance-agent",
                "dependency_keys": [],
                "deliverable": "Financial scenario model",
                "requires_approval": False,
            },
            {
                "key": "executive-decision",
                "title": "Synthesize launch recommendation",
                "description": (
                    "Combine evidence into a decision-ready recommendation."
                ),
                "assigned_agent": "executive-orchestrator",
                "dependency_keys": [
                    "market-research",
                    "financial-case",
                ],
                "deliverable": "Executive launch recommendation",
                "requires_approval": True,
            },
        ],
        "approval_required": approval_required,
    }


class FakeGateway:
    def __init__(self, content):
        self.content = content
        self.messages = None
        self.model = None

    async def complete(
        self,
        messages,
        *,
        model="cofounder-auto",
        temperature=0.1,
        max_tokens=1800,
    ):
        self.messages = messages
        self.model = model
        return GatewayCompletion(
            content=self.content,
            requested_model=model,
            selected_provider="qwen",
            selected_model="Qwen3",
            routing_reason="Local planning is sufficient.",
            request_id="request-plan-1",
            usage={"total_tokens": 100},
        )


def build_orchestrator(tmp_path, payload):
    repository = FileStateRepository(tmp_path / "runs")
    service = OrchestrationService(repository)
    gateway = FakeGateway(json.dumps(payload))
    orchestrator = ExecutiveOrchestrator(
        gateway,
        service,
        registry=AgentRegistry(),
    )
    return repository, service, gateway, orchestrator


def test_plan_uses_registered_agents_and_strict_prompt(tmp_path):
    _, _, gateway, orchestrator = build_orchestrator(
        tmp_path,
        valid_plan_payload(),
    )

    result = asyncio.run(
        orchestrator.plan(
            "Evaluate a new product launch",
            context="The company has a limited launch budget.",
        )
    )

    assert len(result.plan.tasks) == 3
    assert result.plan.topological_tasks()[-1].key == (
        "executive-decision"
    )
    assert gateway.model == "cofounder-auto"

    system_prompt = gateway.messages[0].content
    user_payload = json.loads(gateway.messages[1].content)

    assert "Do not invent or dynamically create agents" in system_prompt
    assert "Do not create nested plans" in system_prompt
    assert len(user_payload["registered_agents"]) == 3
    assert user_payload["output_schema"]["type"] == "object"


def test_unknown_agent_plan_is_rejected(tmp_path):
    payload = valid_plan_payload()
    payload["tasks"][0]["assigned_agent"] = "invented-agent"

    _, _, _, orchestrator = build_orchestrator(
        tmp_path,
        payload,
    )

    with pytest.raises(PlanValidationError):
        asyncio.run(
            orchestrator.plan("Evaluate a product launch")
        )


def test_plan_schema_rejects_cycles_and_wrong_task_count():
    cycle = valid_plan_payload()
    cycle["tasks"][0]["dependency_keys"] = ["executive-decision"]

    with pytest.raises(ValidationError):
        ExecutivePlan.model_validate(cycle)

    too_small = valid_plan_payload()
    too_small["tasks"] = too_small["tasks"][:2]

    with pytest.raises(ValidationError):
        ExecutivePlan.model_validate(too_small)


def test_materialize_approval_plan_and_activate_after_approval(
    tmp_path,
):
    repository, service, _, orchestrator = build_orchestrator(
        tmp_path,
        valid_plan_payload(approval_required=True),
    )

    execution = asyncio.run(
        orchestrator.plan_and_materialize(
            "Evaluate a new product launch",
            owner="founder@example.com",
            correlation_id="corr-plan-1",
        )
    )

    assert execution.run.status == RunStatus.WAITING_APPROVAL.value
    assert execution.approval is not None
    assert execution.approval.status == ApprovalStatus.PENDING.value
    assert execution.ready_task_ids == []
    assert len(execution.tasks) == 3

    snapshot = service.get_snapshot(execution.run.id)
    assert len(snapshot.route_decisions) == 1
    assert len(snapshot.messages) == 1
    assert len(snapshot.tasks) == 3
    assert all(task.status == "pending" for task in snapshot.tasks)

    service.resolve_approval(
        execution.run.id,
        execution.approval.id,
        decision="approved",
        decided_by="founder",
        reason="Proceed with execution.",
        actor="executive-orchestrator",
    )

    activated = orchestrator.activate_ready_tasks(
        execution.run.id,
        reason="Plan approval was granted.",
    )

    assert len(activated) == 2
    assert all(task.status == "ready" for task in activated)

    updated = repository.get_run(execution.run.id)
    assert updated.status == "running"


def test_materialize_no_approval_activates_root_tasks(tmp_path):
    _, service, _, orchestrator = build_orchestrator(
        tmp_path,
        valid_plan_payload(approval_required=False),
    )

    execution = asyncio.run(
        orchestrator.plan_and_materialize(
            "Evaluate a new product launch"
        )
    )

    assert execution.run.status == "running"
    assert execution.approval is None
    assert len(execution.ready_task_ids) == 2

    snapshot = service.get_snapshot(execution.run.id)
    by_key = {
        task.metadata["plan_key"]: task
        for task in snapshot.tasks
    }

    assert by_key["market-research"].status == "ready"
    assert by_key["financial-case"].status == "ready"
    assert by_key["executive-decision"].status == "pending"
    assert len(snapshot.events) >= 9


def test_code_fenced_json_is_parsed_but_not_required(tmp_path):
    payload = valid_plan_payload()
    gateway_content = (
        "```json\n"
        + json.dumps(payload)
        + "\n```"
    )

    repository = FileStateRepository(tmp_path / "runs")
    service = OrchestrationService(repository)
    gateway = FakeGateway(gateway_content)
    orchestrator = ExecutiveOrchestrator(gateway, service)

    result = asyncio.run(
        orchestrator.plan("Evaluate a new product launch")
    )

    assert isinstance(result, ExecutivePlanningResult)
    assert result.plan.objective == payload["objective"]
