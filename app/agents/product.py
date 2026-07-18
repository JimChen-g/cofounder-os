"""Product Agent — the first bounded specialist execution agent.

The Product Agent analyzes users, product requirements, positioning, and
delivery trade-offs. It consumes a ``running`` task, calls the Gateway for a
completion, records routing evidence and a message, and transitions the task
to ``completed``.

This module introduces no database, queue, background process, model, port,
or infrastructure dependency.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from app.agents.registry import (
    AgentRegistry,
    UnknownAgentError,
)
from app.clients import GatewayCompletion
from app.domain import (
    AgentMessage,
    AuditEvent,
    AuditOutcome,
    MessageRole,
    RouteDecision,
    RunStatus,
    Task,
    TaskStatus,
    utc_now,
)
from app.models import ChatMessage
from app.services import OrchestrationService
from app.state import FileStateRepository, RecordNotFound


class ProductAgentError(RuntimeError):
    """Base error for Product Agent execution."""


class TaskNotReadyError(ProductAgentError):
    """Raised when the target task is not in running status."""


class AgentMismatchError(ProductAgentError):
    """Raised when the task is not assigned to this agent."""


class GatewayExecutionProtocol(Protocol):
    """Minimal Gateway boundary required by execution agents."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str = "cofounder-auto",
        temperature: float = 0.1,
        max_tokens: int = 1800,
    ) -> GatewayCompletion:
        ...


class ProductAgentExecutionResult(BaseModel):
    """Outcome of one bounded Product Agent task execution."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_id: str
    completion: GatewayCompletion
    route_decision: RouteDecision
    message: AgentMessage
    transition_event: AuditEvent
    final_task: Task


PRODUCT_AGENT_ID = "product-agent"

PRODUCT_AGENT_SYSTEM_PROMPT = """You are the Product Agent for CoFounder OS.

You analyze users, product requirements, market positioning, and delivery
trade-offs to produce evidence that supports a founder decision.

You receive one bounded task with a title, description, and optional context.
Return a focused analysis that addresses the task deliverable.

Do not invent new tasks, dependencies, or agents. Stay within the scope of
the assigned task.
"""


class ProductAgent:
    """Execute one Product Agent task through the Gateway boundary.

    The agent is immutable after construction. It validates task assignment,
    performs one bounded Gateway call, records routing evidence and a message,
    and transitions the task to completed.
    """

    def __init__(
        self,
        gateway: GatewayExecutionProtocol,
        service: OrchestrationService,
        *,
        registry: AgentRegistry | None = None,
        execution_model: str = "cofounder-auto",
    ) -> None:
        if not execution_model.strip():
            raise ValueError("execution_model must not be empty")

        self.gateway = gateway
        self.service = service
        self.registry = registry or AgentRegistry()
        self.execution_model = execution_model

        try:
            self.registry.require_executable(PRODUCT_AGENT_ID)
        except UnknownAgentError as exc:
            raise ProductAgentError(
                f"Product Agent is not registered: {exc}"
            ) from exc

    async def execute(
        self,
        run_id: str | object,
        task_id: str | object,
        *,
        context: str | None = None,
        actor: str | None = None,
        correlation_id: str | None = None,
    ) -> ProductAgentExecutionResult:
        """Execute one task and persist all evidence atomically.

        All state mutations (route decision, message, task transition) occur
        under a single run lock held by the repository transaction.
        """

        from uuid import UUID

        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))
        execution_actor = actor or PRODUCT_AGENT_ID

        # --- Pre-flight validation (reads only, no lock held) ---
        repository: FileStateRepository = self.service.repository

        try:
            run = repository.get_run(run_uuid)
        except RecordNotFound:
            raise ProductAgentError(f"Run not found: {run_id}")

        if RunStatus(run.status) != RunStatus.RUNNING:
            raise TaskNotReadyError(
                f"Run must be running; current status: {run.status}"
            )

        try:
            task = repository.get_task(run_uuid, task_uuid)
        except RecordNotFound:
            raise ProductAgentError(
                f"Task not found in run {run_id}: {task_id}"
            )

        if (task.assigned_agent or "") != PRODUCT_AGENT_ID:
            raise AgentMismatchError(
                f"Task is assigned to '{task.assigned_agent}', "
                f"not '{PRODUCT_AGENT_ID}'"
            )

        user_content = (
            f"Task: {task.title}\n\n"
            f"Description: {task.description}"
        )
        if context and context.strip():
            user_content += f"\n\nContext: {context.strip()}"

        messages: Sequence[ChatMessage] = [
            ChatMessage(
                role="system",
                content=PRODUCT_AGENT_SYSTEM_PROMPT,
            ),
            ChatMessage(
                role="user",
                content=user_content,
            ),
        ]

        # --- Async Gateway call (no lock held) ---
        completion = await self.gateway.complete(
            messages,
            model=self.execution_model,
            temperature=0.1,
            max_tokens=1800,
        )

        if not completion.content or not completion.content.strip():
            raise ProductAgentError(
                "Gateway returned empty content for task execution"
            )

        # --- All state mutations under one lock ---
        with self.service.repository.transaction(run_uuid) as transaction:
            # Re-read task under lock to detect concurrent changes.
            current_task = transaction.get_task(task_uuid)
            if TaskStatus(current_task.status) != TaskStatus.RUNNING:
                raise TaskNotReadyError(
                    f"Task status changed during execution: "
                    f"{current_task.status}"
                )

            selected_model = (
                completion.selected_model or completion.requested_model
            )
            provider = completion.selected_provider or "gateway"

            # Build and persist the route decision.
            route_decision = RouteDecision(
                run_id=run_uuid,
                task_id=task_uuid,
                requested_model=completion.requested_model,
                selected_model=selected_model,
                provider=provider,
                reason=(
                    completion.routing_reason
                    or "Product Agent task execution."
                ),
                candidate_models=[completion.requested_model],
                fallback_used=completion.fallback_used,
            )
            transaction.create_route_decision(route_decision)

            route_event = AuditEvent(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="route.recorded",
                actor="router",
                action="record",
                target_type="route_decision",
                target_id=str(route_decision.id),
                outcome=AuditOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "requested_model": completion.requested_model,
                    "selected_model": selected_model,
                    "provider": provider,
                    "candidate_models": [completion.requested_model],
                    "fallback_used": completion.fallback_used,
                    "latency_ms": None,
                },
            )
            transaction.append_event(route_event)

            # Build and persist the agent message.
            message = AgentMessage(
                run_id=run_uuid,
                task_id=task_uuid,
                sender=PRODUCT_AGENT_ID,
                recipient="founder",
                role=MessageRole.AGENT,
                content=completion.content.strip(),
                correlation_id=correlation_id,
                metadata={
                    "message_type": "product_agent_result",
                    "execution_model": selected_model,
                    "provider": provider,
                    "usage": completion.usage,
                },
            )
            transaction.create_message(message)

            message_event = AuditEvent(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="message.appended",
                actor=execution_actor,
                action="append",
                target_type="agent_message",
                target_id=str(message.id),
                outcome=AuditOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "sender": message.sender,
                    "recipient": message.recipient,
                    "role": message.role,
                    "content_length": len(message.content),
                    "parent_message_id": None,
                },
            )
            transaction.append_event(message_event)

            # Transition the task to completed.
            now = utc_now()
            updated_task = current_task.model_copy(deep=True)
            updated_task.status = TaskStatus.COMPLETED.value
            updated_task.updated_at = now
            updated_task.completed_at = now
            transaction.save_task(updated_task)

            transition_event = AuditEvent(
                run_id=run_uuid,
                task_id=task_uuid,
                event_type="task.status_changed",
                actor=execution_actor,
                action="transition_status",
                target_type="task",
                target_id=str(task_uuid),
                outcome=AuditOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "from_status": TaskStatus.RUNNING.value,
                    "to_status": TaskStatus.COMPLETED.value,
                    "reason": "Product Agent analysis completed.",
                },
            )
            transaction.append_event(transition_event)

            final_task = updated_task

        return ProductAgentExecutionResult(
            run_id=str(run_uuid),
            task_id=str(task_uuid),
            completion=completion,
            route_decision=route_decision,
            message=message,
            transition_event=transition_event,
            final_task=final_task,
        )
