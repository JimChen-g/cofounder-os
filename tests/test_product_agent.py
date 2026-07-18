"""Tests for the D06-A Product Agent execution foundation."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.agents import (
    PRODUCT_AGENT_ID,
    AgentMismatchError,
    ProductAgent,
    ProductAgentError,
    ProductAgentExecutionResult,
    TaskNotReadyError,
)
from app.clients import GatewayCompletion
from app.domain import (
    MessageRole,
    RunStatus,
    TaskStatus,
)
from app.services import OrchestrationService
from app.state import FileStateRepository


class FakeGateway:
    """Fake Gateway that returns deterministic completions."""

    def __init__(
        self,
        content: str = "Product analysis complete.",
        *,
        requested_model: str = "cofounder-auto",
        selected_provider: str | None = "qwen",
        selected_model: str | None = "Qwen3",
        routing_reason: str | None = "Local reasoning is sufficient.",
        fallback_used: bool = False,
        request_id: str | None = "req-product-1",
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.requested_model = requested_model
        self.selected_provider = selected_provider
        self.selected_model = selected_model
        self.routing_reason = routing_reason
        self.fallback_used = fallback_used
        self.request_id = request_id
        self.usage = usage or {"total_tokens": 100}
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages,
        *,
        model: str = "cofounder-auto",
        temperature: float = 0.1,
        max_tokens: int = 1800,
    ) -> GatewayCompletion:
        self.calls.append({
            "messages": list(messages),
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        return GatewayCompletion(
            content=self.content,
            requested_model=self.requested_model,
            selected_provider=self.selected_provider,
            selected_model=self.selected_model,
            routing_reason=self.routing_reason,
            fallback_used=self.fallback_used,
            request_id=self.request_id,
            usage=dict(self.usage),
        )


def build_product_agent(tmp_path, **gateway_kwargs):
    """Build a ProductAgent wired to a FakeGateway and fresh repository."""
    repository = FileStateRepository(tmp_path / "runs")
    service = OrchestrationService(repository)
    gateway = FakeGateway(**gateway_kwargs)
    agent = ProductAgent(gateway, service)
    return repository, service, gateway, agent


def _setup_ready_task(service, run, task):
    """Bring a task to running status."""
    service.start_run(run.id, actor="orchestrator", reason="Run started.")
    service.mark_task_ready(
        run.id, task.id, actor="orchestrator", reason="No dependencies."
    )
    service.start_task(
        run.id, task.id, actor="orchestrator", reason="Task started."
    )


def _run_async(coro):
    """Run an async coroutine to completion in a fresh event loop."""
    import asyncio
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_execute_produces_complete_result(tmp_path):
    repository, service, gateway, agent = build_product_agent(tmp_path)

    run, _ = service.create_run(
        objective="Launch a new feature",
        actor="founder",
    )
    task, _ = service.create_task(
        run.id,
        title="Analyze user segments",
        description="Identify target user segments and value propositions.",
        actor="orchestrator",
        assigned_agent=PRODUCT_AGENT_ID,
    )
    _setup_ready_task(service, run, task)

    result = _run_async(
        agent.execute(
            run.id,
            task.id,
            context="Focus on enterprise users.",
            actor="product-agent",
            correlation_id="corr-prod-1",
        )
    )

    # Result model fields
    assert isinstance(result, ProductAgentExecutionResult)
    assert result.run_id == str(run.id)
    assert result.task_id == str(task.id)
    assert result.completion.content == "Product analysis complete."
    assert result.message.sender == PRODUCT_AGENT_ID
    assert result.message.role == MessageRole.AGENT.value
    assert result.transition_event.event_type == "task.status_changed"
    assert result.final_task.status == TaskStatus.COMPLETED.value
    assert result.final_task.completed_at is not None

    # Persisted state verified through repository reads
    persisted_run = repository.get_run(run.id)
    assert persisted_run.status == RunStatus.RUNNING.value  # run stays running

    persisted_task = repository.get_task(run.id, task.id)
    assert persisted_task.status == TaskStatus.COMPLETED.value

    # Events were recorded
    events = repository.list_events(run.id)
    event_types = [e.event_type for e in events]
    assert "route.recorded" in event_types
    assert "message.appended" in event_types
    assert "task.status_changed" in event_types


def test_execute_records_correct_gateway_call(tmp_path):
    _, _, gateway, agent = build_product_agent(tmp_path)

    run, _ = agent.service.create_run(
        objective="Evaluate pricing strategy",
        actor="founder",
    )
    task, _ = agent.service.create_task(
        run.id,
        title="Evaluate pricing tiers",
        description="Compare freemium vs. paid tiers.",
        actor="orchestrator",
        assigned_agent=PRODUCT_AGENT_ID,
    )
    _setup_ready_task(agent.service, run, task)

    _run_async(
        agent.execute(
            run.id,
            task.id,
            context="Consider SMB market.",
        )
    )

    assert len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["model"] == "cofounder-auto"
    assert len(call["messages"]) == 2
    assert call["messages"][0].role == "system"
    assert "Product Agent" in call["messages"][0].content
    assert "Evaluate pricing tiers" in call["messages"][1].content
    assert "Consider SMB market." in call["messages"][1].content


def test_task_not_running_raises_task_not_ready(tmp_path):
    _, _, _, agent = build_product_agent(tmp_path)

    run, _ = agent.service.create_run(
        objective="Reject pending task",
        actor="founder",
    )
    task, _ = agent.service.create_task(
        run.id,
        title="Still pending",
        actor="orchestrator",
        assigned_agent=PRODUCT_AGENT_ID,
    )

    with pytest.raises(TaskNotReadyError, match="running"):
        _run_async(agent.execute(run.id, task.id))


def test_completed_task_raises_task_not_ready(tmp_path):
    _, service, _, agent = build_product_agent(tmp_path)

    run, _ = service.create_run(actor="founder", objective="Complete task")
    task, _ = service.create_task(
        run.id,
        title="Already done",
        actor="orchestrator",
        assigned_agent=PRODUCT_AGENT_ID,
    )
    _setup_ready_task(service, run, task)
    service.complete_task(
        run.id, task.id, actor="orchestrator", reason="Done."
    )

    with pytest.raises(TaskNotReadyError, match="completed"):
        _run_async(agent.execute(run.id, task.id))


def test_wrong_agent_assignment_raises_mismatch(tmp_path):
    _, _, _, agent = build_product_agent(tmp_path)

    run, _ = agent.service.create_run(
        objective="Reject wrong agent",
        actor="founder",
    )
    task, _ = agent.service.create_task(
        run.id,
        title="Not for Product Agent",
        actor="orchestrator",
        assigned_agent="finance-agent",
    )
    _setup_ready_task(agent.service, run, task)

    with pytest.raises(AgentMismatchError, match="finance-agent"):
        _run_async(agent.execute(run.id, task.id))


def test_empty_gateway_content_raises_product_agent_error(tmp_path):
    _, _, gateway, agent = build_product_agent(
        tmp_path, content="   \n\n   "
    )

    run, _ = agent.service.create_run(
        objective="Reject empty response",
        actor="founder",
    )
    task, _ = agent.service.create_task(
        run.id,
        title="Expect content",
        actor="orchestrator",
        assigned_agent=PRODUCT_AGENT_ID,
    )
    _setup_ready_task(agent.service, run, task)

    with pytest.raises(ProductAgentError, match="empty content"):
        _run_async(agent.execute(run.id, task.id))


def test_non_running_run_raises_task_not_ready(tmp_path):
    _, _, _, agent = build_product_agent(tmp_path)

    run, _ = agent.service.create_run(
        objective="Run still queued",
        actor="founder",
    )
    task, _ = agent.service.create_task(
        run.id,
        title="Cannot execute yet",
        actor="orchestrator",
        assigned_agent=PRODUCT_AGENT_ID,
    )

    with pytest.raises(TaskNotReadyError, match="queued"):
        _run_async(agent.execute(run.id, task.id))


def test_run_not_found_raises_product_agent_error(tmp_path):
    _, _, _, agent = build_product_agent(tmp_path)

    import uuid
    missing_run = uuid.uuid4()
    missing_task = uuid.uuid4()

    with pytest.raises(ProductAgentError):
        _run_async(agent.execute(missing_run, missing_task))


def test_execution_result_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ProductAgentExecutionResult(
            run_id="run-1",
            task_id="task-1",
            completion=GatewayCompletion(
                content="result",
                requested_model="cofounder-auto",
            ),
            route_decision=__import__(
                "app.domain", fromlist=["RouteDecision"]
            ).RouteDecision(
                run_id=__import__("uuid").UUID(int=0),
                selected_model="model",
                provider="p",
                reason="r",
            ),
            message=__import__(
                "app.domain", fromlist=["AgentMessage"]
            ).AgentMessage(
                run_id=__import__("uuid").UUID(int=0),
                sender="a",
                content="m",
            ),
            transition_event=__import__(
                "app.domain", fromlist=["AuditEvent"]
            ).AuditEvent(
                run_id=__import__("uuid").UUID(int=0),
                actor="a",
                action="t",
                target_type="type",
                outcome=__import__(
                    "app.domain", fromlist=["AuditOutcome"]
                ).AuditOutcome.SUCCESS,
            ),
            final_task=__import__(
                "app.domain", fromlist=["Task"]
            ).Task(
                run_id=__import__("uuid").UUID(int=0),
                title="t",
            ),
            undeclared_field="rejected",
        )


def test_product_agent_rejects_empty_execution_model():
    class FakeGateway:
        async def complete(self, messages, **kwargs):
            return GatewayCompletion(
                content="result",
                requested_model="cofounder-auto",
            )

    with pytest.raises(ValueError, match="execution_model"):
        ProductAgent(
            FakeGateway(),
            __import__(
                "app.services", fromlist=["OrchestrationService"]
            ).OrchestrationService(
                __import__(
                    "app.state", fromlist=["FileStateRepository"]
                ).FileStateRepository("/tmp/nonexistent-runs-test")
            ),
            execution_model="   ",
        )
