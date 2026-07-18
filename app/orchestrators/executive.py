"""Bounded Executive Orchestrator for CoFounder OS."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.agents import (
    EXECUTIVE_AGENT_ID,
    AgentRegistry,
    UnknownAgentError,
)
from app.clients import GatewayCompletion
from app.domain import (
    Approval,
    RouteDecision,
    Run,
    RunStatus,
    Task,
    TaskStatus,
)
from app.models import ChatMessage
from app.services import OrchestrationService


class ExecutiveOrchestratorError(RuntimeError):
    """Base error for executive planning and materialization."""


class PlanParsingError(ExecutiveOrchestratorError):
    """Raised when model output cannot be parsed as one plan object."""


class PlanValidationError(ExecutiveOrchestratorError):
    """Raised when a parsed plan violates bounded-planning rules."""


class GatewayPlanningProtocol(Protocol):
    """Minimal Gateway boundary required by the orchestrator."""

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str = "cofounder-auto",
        temperature: float = 0.1,
        max_tokens: int = 1800,
    ) -> GatewayCompletion:
        ...


class PlannedTask(BaseModel):
    """One bounded task proposed by the Executive Orchestrator."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(
        min_length=2,
        max_length=40,
        pattern=r"^[a-z][a-z0-9-]*$",
    )
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1200)
    assigned_agent: str = Field(min_length=3, max_length=64)
    dependency_keys: list[str] = Field(default_factory=list)
    deliverable: str = Field(min_length=1, max_length=500)
    requires_approval: bool = False


class ExecutivePlan(BaseModel):
    """Strict structured output accepted from the planning model."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=2000)
    summary: str = Field(min_length=1, max_length=2000)
    tasks: list[PlannedTask] = Field(min_length=3, max_length=6)
    approval_required: bool = True

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutivePlan":
        keys = [task.key for task in self.tasks]
        if len(keys) != len(set(keys)):
            raise ValueError("Task keys must be unique")

        known = set(keys)
        graph: dict[str, tuple[str, ...]] = {}

        for task in self.tasks:
            dependencies = tuple(task.dependency_keys)
            if len(dependencies) != len(set(dependencies)):
                raise ValueError(
                    f"Task {task.key} has duplicate dependencies"
                )
            if task.key in dependencies:
                raise ValueError(
                    f"Task {task.key} cannot depend on itself"
                )

            unknown = sorted(set(dependencies) - known)
            if unknown:
                raise ValueError(
                    f"Task {task.key} has unknown dependencies: "
                    + ", ".join(unknown)
                )

            graph[task.key] = dependencies

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise ValueError("Task dependency graph contains a cycle")
            if key in visited:
                return

            visiting.add(key)
            for dependency in graph[key]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)

        for key in keys:
            visit(key)

        if not any(not task.dependency_keys for task in self.tasks):
            raise ValueError(
                "At least one task must have no dependencies"
            )

        return self

    def topological_tasks(self) -> list[PlannedTask]:
        """Return tasks with every dependency before its dependent."""

        by_key = {task.key: task for task in self.tasks}
        emitted: set[str] = set()
        ordered: list[PlannedTask] = []

        while len(ordered) < len(self.tasks):
            progress = False

            for task in self.tasks:
                if task.key in emitted:
                    continue
                if all(
                    dependency in emitted
                    for dependency in task.dependency_keys
                ):
                    ordered.append(task)
                    emitted.add(task.key)
                    progress = True

            if not progress:
                raise PlanValidationError(
                    "Task graph cannot be topologically ordered"
                )

        return ordered


class ExecutivePlanningResult(BaseModel):
    """Validated plan plus normalized Gateway routing evidence."""

    model_config = ConfigDict(extra="forbid")

    plan: ExecutivePlan
    completion: GatewayCompletion


class MaterializedExecution(BaseModel):
    """Persistent run and tasks created from one validated plan."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    tasks: list[Task]
    route_decision: RouteDecision
    approval: Approval | None = None
    plan_message_id: str
    ready_task_ids: list[str] = Field(default_factory=list)


SYSTEM_PROMPT = """You are the single Executive Orchestrator for CoFounder OS.

Produce exactly one JSON object matching the supplied schema.

Hard constraints:
- Create between 3 and 6 tasks.
- Use only agent_id values from registered_agents.
- Do not invent or dynamically create agents.
- Do not create nested plans or recursive planning tasks.
- Keep the dependency graph acyclic.
- Use stable lowercase task keys with hyphens.
- Include at least one root task with no dependencies.
- Assign a concrete deliverable to every task.
- Set approval_required=true when execution or conclusions need founder review.
- Output JSON only. Do not use Markdown or code fences.
"""


class ExecutiveOrchestrator:
    """Plan once, validate strictly, and persist through D04 services."""

    def __init__(
        self,
        gateway: GatewayPlanningProtocol,
        service: OrchestrationService,
        *,
        registry: AgentRegistry | None = None,
        planning_model: str = "cofounder-auto",
    ) -> None:
        if not planning_model.strip():
            raise ValueError("planning_model must not be empty")

        self.gateway = gateway
        self.service = service
        self.registry = registry or AgentRegistry()
        self.planning_model = planning_model

    async def plan(
        self,
        objective: str,
        *,
        context: str | None = None,
    ) -> ExecutivePlanningResult:
        normalized_objective = objective.strip()
        if not normalized_objective:
            raise ValueError("objective must not be empty")

        request_payload = {
            "objective": normalized_objective,
            "context": context.strip() if context else None,
            "registered_agents": self.registry.prompt_catalog(),
            "output_schema": ExecutivePlan.model_json_schema(),
        }

        messages = [
            ChatMessage(
                role="system",
                content=SYSTEM_PROMPT,
            ),
            ChatMessage(
                role="user",
                content=json.dumps(
                    request_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ),
        ]

        completion = await self.gateway.complete(
            messages,
            model=self.planning_model,
            temperature=0.1,
            max_tokens=1800,
        )

        plan = self._parse_plan(completion.content)
        self._validate_agents(plan)

        return ExecutivePlanningResult(
            plan=plan,
            completion=completion,
        )

    def materialize(
        self,
        result: ExecutivePlanningResult,
        *,
        owner: str | None = None,
        actor: str = EXECUTIVE_AGENT_ID,
        correlation_id: str | None = None,
    ) -> MaterializedExecution:
        """Persist one already validated plan without another model call."""

        self._validate_agents(result.plan)
        ordered = result.plan.topological_tasks()

        run, _ = self.service.create_run(
            objective=result.plan.objective,
            actor=actor,
            owner=owner,
            metadata={
                "executive_plan_summary": result.plan.summary,
                "executive_plan_task_count": len(result.plan.tasks),
                "executive_plan_approval_required": (
                    result.plan.approval_required
                ),
                "planning_model": result.completion.requested_model,
            },
            correlation_id=correlation_id,
        )

        plan_message, _ = self.service.append_message(
            run.id,
            sender=EXECUTIVE_AGENT_ID,
            recipient="orchestration-service",
            content=result.plan.model_dump_json(indent=2),
            actor=actor,
            correlation_id=correlation_id,
            metadata={
                "message_type": "executive_plan",
                "schema": "ExecutivePlan",
            },
        )

        tasks_by_key: dict[str, Task] = {}
        persisted_tasks: list[Task] = []

        for planned in ordered:
            dependency_ids = [
                tasks_by_key[key].id
                for key in planned.dependency_keys
            ]
            task, _ = self.service.create_task(
                run.id,
                title=planned.title,
                actor=actor,
                description=planned.description,
                assigned_agent=planned.assigned_agent,
                dependency_ids=dependency_ids,
                metadata={
                    "plan_key": planned.key,
                    "deliverable": planned.deliverable,
                    "requires_approval": planned.requires_approval,
                },
                correlation_id=correlation_id,
            )
            tasks_by_key[planned.key] = task
            persisted_tasks.append(task)

        completion = result.completion
        route_decision, _ = self.service.record_route_decision(
            run.id,
            requested_model=completion.requested_model,
            selected_model=(
                completion.selected_model
                or completion.requested_model
            ),
            provider=completion.selected_provider or "gateway",
            reason=(
                completion.routing_reason
                or "Gateway completed the Executive Orchestrator plan."
            ),
            actor="router",
            candidate_models=[completion.requested_model],
            fallback_used=completion.fallback_used,
            correlation_id=(
                correlation_id or completion.request_id
            ),
            metadata={
                "gateway_request_id": completion.request_id,
                "usage": completion.usage,
                "plan_schema": "ExecutivePlan",
            },
        )

        started_run, _ = self.service.start_run(
            run.id,
            actor=actor,
            reason="Executive plan was validated and materialized.",
            correlation_id=correlation_id,
        )

        approval = None
        ready_task_ids: list[str] = []

        if result.plan.approval_required:
            approval_result = self.service.request_approval(
                run.id,
                requested_by=EXECUTIVE_AGENT_ID,
                reason="Founder approval is required before execution.",
                actor=actor,
                correlation_id=correlation_id,
                metadata={
                    "approval_type": "executive_plan",
                    "task_count": len(persisted_tasks),
                },
            )
            approval = approval_result.approval
            final_run = approval_result.run or started_run
        else:
            ready_tasks = self.activate_ready_tasks(
                run.id,
                actor=actor,
                reason="Root tasks are ready after plan materialization.",
                correlation_id=correlation_id,
            )
            ready_task_ids = [str(task.id) for task in ready_tasks]
            final_run = self.service.get_snapshot(run.id).run

        return MaterializedExecution(
            run=final_run,
            tasks=persisted_tasks,
            route_decision=route_decision,
            approval=approval,
            plan_message_id=str(plan_message.id),
            ready_task_ids=ready_task_ids,
        )

    async def plan_and_materialize(
        self,
        objective: str,
        *,
        context: str | None = None,
        owner: str | None = None,
        actor: str = EXECUTIVE_AGENT_ID,
        correlation_id: str | None = None,
    ) -> MaterializedExecution:
        """Run one bounded planning call and persist its accepted result."""

        result = await self.plan(
            objective,
            context=context,
        )
        return self.materialize(
            result,
            owner=owner,
            actor=actor,
            correlation_id=correlation_id,
        )

    def activate_ready_tasks(
        self,
        run_id,
        *,
        actor: str = EXECUTIVE_AGENT_ID,
        reason: str = "Task dependencies are complete.",
        correlation_id: str | None = None,
    ) -> list[Task]:
        """Move eligible pending or blocked tasks to ready."""

        snapshot = self.service.get_snapshot(run_id)
        if RunStatus(snapshot.run.status) != RunStatus.RUNNING:
            return []

        tasks_by_id = {task.id: task for task in snapshot.tasks}
        activated: list[Task] = []

        for task in snapshot.tasks:
            current = TaskStatus(task.status)
            if current not in {
                TaskStatus.PENDING,
                TaskStatus.BLOCKED,
            }:
                continue

            dependencies_complete = all(
                dependency_id in tasks_by_id
                and TaskStatus(tasks_by_id[dependency_id].status)
                == TaskStatus.COMPLETED
                for dependency_id in task.dependency_ids
            )

            if not dependencies_complete:
                continue

            ready, _ = self.service.mark_task_ready(
                run_id,
                task.id,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
            )
            activated.append(ready)

        return activated

    def _validate_agents(self, plan: ExecutivePlan) -> None:
        for task in plan.tasks:
            try:
                self.registry.require_executable(
                    task.assigned_agent
                )
            except UnknownAgentError as exc:
                raise PlanValidationError(str(exc)) from exc

    @staticmethod
    def _parse_plan(content: str) -> ExecutivePlan:
        candidate = content.strip()

        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()

        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end < start:
            raise PlanParsingError(
                "Planning response contains no JSON object"
            )

        json_text = candidate[start : end + 1]

        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise PlanParsingError(
                f"Planning response is invalid JSON: {exc.msg}"
            ) from exc

        try:
            return ExecutivePlan.model_validate(payload)
        except ValidationError as exc:
            raise PlanValidationError(
                "Planning response violates ExecutivePlan: "
                + str(exc)
            ) from exc
