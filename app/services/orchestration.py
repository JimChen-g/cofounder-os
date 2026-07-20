"""Application service for composing CoFounder OS domain operations.

The service coordinates domain creation, lifecycle transitions, approvals,
routing records, messages, artifacts, and read snapshots. It does not call
providers directly and does not expose HTTP routes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, FrozenSet, Literal, Mapping, Sequence, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts import ArtifactConflictError
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
    utc_now,
)
from app.state import (
    FileStateRepository,
    InvalidTransition,
    LifecycleStateMachine,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
)
from app.state.repository import RunTransaction


class OrchestrationError(RuntimeError):
    """Base error for application-service operations."""


class DependencyNotReady(OrchestrationError):
    """Raised when a task dependency is not complete."""


class RunCompletionBlocked(OrchestrationError):
    """Raised when a run still contains incomplete or failed tasks."""


class ActiveApprovalExists(OrchestrationError):
    """Raised when a target already has an unresolved approval."""


class ApprovalResolutionError(OrchestrationError):
    """Raised when an approval cannot be resolved as requested."""


class ArtifactRelationError(OrchestrationError):
    """Raised when artifact scope and relation are inconsistent."""


class RunSnapshot(BaseModel):
    """Consistent read model collected under one run lock."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    tasks: list[Task] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    route_decisions: list[RouteDecision] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    events: list[AuditEvent] = Field(default_factory=list)


class ApprovalWorkflowResult(BaseModel):
    """Result of requesting or resolving an approval."""

    model_config = ConfigDict(extra="forbid")

    approval: Approval
    approval_event: AuditEvent
    transition_event: AuditEvent
    run: Run | None = None
    task: Task | None = None


StatusT = TypeVar("StatusT", RunStatus, TaskStatus)


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _uuid_list(values: Sequence[UUID | str] | None) -> list[UUID]:
    return [UUID(str(value)) for value in values or ()]


def _ensure_transition_allowed(
    current: StatusT,
    target: StatusT,
    transitions: Mapping[StatusT, FrozenSet[StatusT]],
    target_type: str,
) -> None:
    if target not in transitions[current]:
        allowed = ", ".join(
            sorted(status.value for status in transitions[current])
        )
        if not allowed:
            allowed = "none"
        raise InvalidTransition(
            f"Invalid {target_type} transition "
            f"{current.value} -> {target.value}; "
            f"allowed targets: {allowed}"
        )


def _event(
    *,
    run_id: UUID,
    event_type: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any],
    task_id: UUID | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(
        run_id=run_id,
        task_id=task_id,
        event_type=event_type,
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=AuditOutcome.SUCCESS,
        correlation_id=correlation_id,
        details=details,
    )


class OrchestrationService:
    """Compose repository and lifecycle operations into product workflows."""

    def __init__(
        self,
        repository: FileStateRepository,
        state_machine: LifecycleStateMachine | None = None,
    ) -> None:
        self.repository = repository
        self.state_machine = state_machine or LifecycleStateMachine(repository)

        if self.state_machine.repository is not repository:
            raise ValueError(
                "state_machine and service must use the same repository"
            )

    def create_run(
        self,
        *,
        objective: str,
        actor: str,
        owner: str | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        normalized_actor = _required_text(actor, "actor")
        run = Run(
            objective=_required_text(objective, "objective"),
            owner=owner,
            metadata=dict(metadata or {}),
        )

        with self.repository.transaction(run.id) as transaction:
            transaction.create_run(run)
            event = _event(
                run_id=run.id,
                event_type="run.created",
                actor=normalized_actor,
                action="create",
                target_type="run",
                target_id=str(run.id),
                correlation_id=correlation_id,
                details={
                    "objective_length": len(run.objective),
                    "owner": run.owner,
                },
            )
            transaction.append_event(event)

        return run, event

    def create_task(
        self,
        run_id: UUID | str,
        *,
        title: str,
        actor: str,
        description: str = "",
        assigned_agent: str | None = None,
        dependency_ids: Sequence[UUID | str] | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        normalized_actor = _required_text(actor, "actor")
        dependencies = _uuid_list(dependency_ids)

        task = Task(
            run_id=UUID(str(run_id)),
            title=_required_text(title, "title"),
            description=description,
            assigned_agent=assigned_agent,
            dependency_ids=dependencies,
            metadata=dict(metadata or {}),
        )

        with self.repository.transaction(run_id) as transaction:
            run = transaction.get_run()

            for dependency_id in dependencies:
                transaction.get_task(dependency_id)

            transaction.create_task(task)

            updated_run = run.model_copy(deep=True)
            if task.id not in updated_run.task_ids:
                updated_run.task_ids.append(task.id)
            updated_run.updated_at = utc_now()
            transaction.save_run(updated_run)

            event = _event(
                run_id=updated_run.id,
                task_id=task.id,
                event_type="task.created",
                actor=normalized_actor,
                action="create",
                target_type="task",
                target_id=str(task.id),
                correlation_id=correlation_id,
                details={
                    "title": task.title,
                    "assigned_agent": task.assigned_agent,
                    "dependency_ids": [
                        str(value) for value in task.dependency_ids
                    ],
                },
            )
            transaction.append_event(event)

        return task, event

    def start_run(
        self,
        run_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        return self.state_machine.transition_run(
            run_id,
            RunStatus.RUNNING,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def wait_run_for_approval(
        self,
        run_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        return self.state_machine.transition_run(
            run_id,
            RunStatus.WAITING_APPROVAL,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def complete_run(
        self,
        run_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        with self.repository.transaction(run_id) as transaction:
            tasks = transaction.list_tasks()
            blocking = [
                task
                for task in tasks
                if TaskStatus(task.status)
                not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
            ]

            if blocking:
                summary = ", ".join(
                    f"{task.id}:{task.status}" for task in blocking
                )
                raise RunCompletionBlocked(
                    f"Run has non-completable tasks: {summary}"
                )

            return self.state_machine.transition_run_in_transaction(
                transaction,
                RunStatus.COMPLETED,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
            )

    def fail_run(
        self,
        run_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        return self.state_machine.transition_run(
            run_id,
            RunStatus.FAILED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def cancel_run(
        self,
        run_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Run, AuditEvent]:
        return self.state_machine.transition_run(
            run_id,
            RunStatus.CANCELLED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def mark_task_ready(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        with self.repository.transaction(run_id) as transaction:
            task = transaction.get_task(task_id)
            incomplete_dependencies = []

            for dependency_id in task.dependency_ids:
                dependency = transaction.get_task(dependency_id)
                if TaskStatus(dependency.status) != TaskStatus.COMPLETED:
                    incomplete_dependencies.append(dependency)

            if incomplete_dependencies:
                summary = ", ".join(
                    f"{dependency.id}:{dependency.status}"
                    for dependency in incomplete_dependencies
                )
                raise DependencyNotReady(
                    f"Task dependencies are not complete: {summary}"
                )

            return self.state_machine.transition_task_in_transaction(
                transaction,
                task.id,
                TaskStatus.READY,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
            )

    def start_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        return self.state_machine.transition_task(
            run_id,
            task_id,
            TaskStatus.RUNNING,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def wait_task_for_approval(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        return self.state_machine.transition_task(
            run_id,
            task_id,
            TaskStatus.WAITING_APPROVAL,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def block_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        return self.state_machine.transition_task(
            run_id,
            task_id,
            TaskStatus.BLOCKED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def complete_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        return self.state_machine.transition_task(
            run_id,
            task_id,
            TaskStatus.COMPLETED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def fail_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        return self.state_machine.transition_task(
            run_id,
            task_id,
            TaskStatus.FAILED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def cancel_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        *,
        actor: str,
        reason: str,
        correlation_id: str | None = None,
    ) -> tuple[Task, AuditEvent]:
        return self.state_machine.transition_task(
            run_id,
            task_id,
            TaskStatus.CANCELLED,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )

    def append_message(
        self,
        run_id: UUID | str,
        *,
        sender: str,
        content: str,
        actor: str,
        task_id: UUID | str | None = None,
        recipient: str | None = None,
        role: MessageRole | str = MessageRole.AGENT,
        correlation_id: str | None = None,
        parent_message_id: UUID | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[AgentMessage, AuditEvent]:
        normalized_actor = _required_text(actor, "actor")
        message = AgentMessage(
            run_id=UUID(str(run_id)),
            task_id=UUID(str(task_id)) if task_id is not None else None,
            sender=_required_text(sender, "sender"),
            recipient=recipient,
            role=MessageRole(role),
            content=_required_text(content, "content"),
            correlation_id=correlation_id,
            parent_message_id=(
                UUID(str(parent_message_id))
                if parent_message_id is not None
                else None
            ),
            metadata=dict(metadata or {}),
        )

        with self.repository.transaction(run_id) as transaction:
            transaction.get_run()
            if message.task_id is not None:
                transaction.get_task(message.task_id)
            if message.parent_message_id is not None:
                transaction.get_message(message.parent_message_id)

            transaction.create_message(message)
            event = _event(
                run_id=message.run_id,
                task_id=message.task_id,
                event_type="message.appended",
                actor=normalized_actor,
                action="append",
                target_type="agent_message",
                target_id=str(message.id),
                correlation_id=correlation_id,
                details={
                    "sender": message.sender,
                    "recipient": message.recipient,
                    "role": message.role,
                    "content_length": len(message.content),
                    "parent_message_id": (
                        str(message.parent_message_id)
                        if message.parent_message_id is not None
                        else None
                    ),
                },
            )
            transaction.append_event(event)

        return message, event

    def record_route_decision(
        self,
        run_id: UUID | str,
        *,
        selected_model: str,
        provider: str,
        reason: str,
        actor: str = "router",
        task_id: UUID | str | None = None,
        requested_model: str | None = None,
        candidate_models: Sequence[str] | None = None,
        fallback_used: bool = False,
        latency_ms: float | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[RouteDecision, AuditEvent]:
        normalized_actor = _required_text(actor, "actor")
        decision = RouteDecision(
            run_id=UUID(str(run_id)),
            task_id=UUID(str(task_id)) if task_id is not None else None,
            requested_model=requested_model,
            selected_model=_required_text(
                selected_model,
                "selected_model",
            ),
            provider=_required_text(provider, "provider"),
            reason=_required_text(reason, "reason"),
            candidate_models=list(candidate_models or ()),
            fallback_used=fallback_used,
            latency_ms=latency_ms,
            metadata=dict(metadata or {}),
        )

        with self.repository.transaction(run_id) as transaction:
            transaction.get_run()
            if decision.task_id is not None:
                transaction.get_task(decision.task_id)

            transaction.create_route_decision(decision)
            event = _event(
                run_id=decision.run_id,
                task_id=decision.task_id,
                event_type="route.recorded",
                actor=normalized_actor,
                action="record",
                target_type="route_decision",
                target_id=str(decision.id),
                correlation_id=correlation_id,
                details={
                    "requested_model": decision.requested_model,
                    "selected_model": decision.selected_model,
                    "provider": decision.provider,
                    "candidate_models": decision.candidate_models,
                    "fallback_used": decision.fallback_used,
                    "latency_ms": decision.latency_ms,
                },
            )
            transaction.append_event(event)

        return decision, event

    def request_approval(
        self,
        run_id: UUID | str,
        *,
        requested_by: str,
        reason: str,
        actor: str,
        task_id: UUID | str | None = None,
        expires_at: datetime | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalWorkflowResult:
        normalized_actor = _required_text(actor, "actor")
        normalized_requested_by = _required_text(
            requested_by,
            "requested_by",
        )
        normalized_reason = _required_text(reason, "reason")

        approval = Approval(
            run_id=UUID(str(run_id)),
            task_id=UUID(str(task_id)) if task_id is not None else None,
            requested_by=normalized_requested_by,
            reason=normalized_reason,
            expires_at=expires_at,
            metadata=dict(metadata or {}),
        )

        with self.repository.transaction(run_id) as transaction:
            run = transaction.get_run()
            task = None

            if approval.task_id is not None:
                task = transaction.get_task(approval.task_id)
                task_current = TaskStatus(task.status)
                _ensure_transition_allowed(
                    task_current,
                    TaskStatus.WAITING_APPROVAL,
                    TASK_TRANSITIONS,
                    "task",
                )
                self._assert_no_active_task_approval(
                    transaction,
                    task,
                )
            else:
                run_current = RunStatus(run.status)
                _ensure_transition_allowed(
                    run_current,
                    RunStatus.WAITING_APPROVAL,
                    RUN_TRANSITIONS,
                    "run",
                )

            transaction.create_approval(approval)

            if task is not None:
                linked_task = task.model_copy(deep=True)
                linked_task.approval_id = approval.id
                linked_task.updated_at = utc_now()
                transaction.save_task(linked_task)

            approval_event = _event(
                run_id=approval.run_id,
                task_id=approval.task_id,
                event_type="approval.requested",
                actor=normalized_actor,
                action="request",
                target_type="approval",
                target_id=str(approval.id),
                correlation_id=correlation_id,
                details={
                    "requested_by": approval.requested_by,
                    "reason": approval.reason,
                    "expires_at": (
                        approval.expires_at.isoformat()
                        if approval.expires_at is not None
                        else None
                    ),
                    "scope": (
                        "task" if approval.task_id is not None else "run"
                    ),
                },
            )
            transaction.append_event(approval_event)

            if approval.task_id is not None:
                transitioned_task, transition_event = (
                    self.state_machine.transition_task_in_transaction(
                        transaction,
                        approval.task_id,
                        TaskStatus.WAITING_APPROVAL,
                        actor=normalized_actor,
                        reason=normalized_reason,
                        correlation_id=correlation_id,
                    )
                )
                transitioned_run = None
            else:
                transitioned_run, transition_event = (
                    self.state_machine.transition_run_in_transaction(
                        transaction,
                        RunStatus.WAITING_APPROVAL,
                        actor=normalized_actor,
                        reason=normalized_reason,
                        correlation_id=correlation_id,
                    )
                )
                transitioned_task = None

        return ApprovalWorkflowResult(
            approval=approval,
            approval_event=approval_event,
            transition_event=transition_event,
            run=transitioned_run,
            task=transitioned_task,
        )

    def resolve_approval(
        self,
        run_id: UUID | str,
        approval_id: UUID | str,
        *,
        decision: Literal["approved", "rejected"],
        decided_by: str,
        reason: str,
        actor: str,
        correlation_id: str | None = None,
    ) -> ApprovalWorkflowResult:
        normalized_actor = _required_text(actor, "actor")
        normalized_decided_by = _required_text(
            decided_by,
            "decided_by",
        )
        normalized_reason = _required_text(reason, "reason")
        decision_status = ApprovalStatus(decision)

        if decision_status not in {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
        }:
            raise ApprovalResolutionError(
                "Only approved or rejected decisions are supported"
            )

        with self.repository.transaction(run_id) as transaction:
            approval = transaction.get_approval(approval_id)

            if ApprovalStatus(approval.status) != ApprovalStatus.PENDING:
                raise ApprovalResolutionError(
                    f"Approval is already resolved: {approval.status}"
                )

            run = transaction.get_run()
            task = None

            if approval.task_id is not None:
                task = transaction.get_task(approval.task_id)
                target_task_status = (
                    TaskStatus.RUNNING
                    if decision_status == ApprovalStatus.APPROVED
                    else TaskStatus.FAILED
                )
                _ensure_transition_allowed(
                    TaskStatus(task.status),
                    target_task_status,
                    TASK_TRANSITIONS,
                    "task",
                )
            else:
                target_run_status = (
                    RunStatus.RUNNING
                    if decision_status == ApprovalStatus.APPROVED
                    else RunStatus.FAILED
                )
                _ensure_transition_allowed(
                    RunStatus(run.status),
                    target_run_status,
                    RUN_TRANSITIONS,
                    "run",
                )

            resolved = approval.model_copy(deep=True)
            resolved.status = decision_status.value
            resolved.decided_by = normalized_decided_by
            resolved.decision_reason = normalized_reason
            resolved.decided_at = utc_now()
            transaction.save_approval(resolved)

            approval_event = _event(
                run_id=resolved.run_id,
                task_id=resolved.task_id,
                event_type="approval.resolved",
                actor=normalized_actor,
                action="resolve",
                target_type="approval",
                target_id=str(resolved.id),
                correlation_id=correlation_id,
                details={
                    "decision": decision_status.value,
                    "decided_by": normalized_decided_by,
                    "reason": normalized_reason,
                    "scope": (
                        "task" if resolved.task_id is not None else "run"
                    ),
                },
            )
            transaction.append_event(approval_event)

            transition_reason = (
                f"Approval {decision_status.value}: {normalized_reason}"
            )

            if resolved.task_id is not None:
                transitioned_task, transition_event = (
                    self.state_machine.transition_task_in_transaction(
                        transaction,
                        resolved.task_id,
                        target_task_status,
                        actor=normalized_actor,
                        reason=transition_reason,
                        correlation_id=correlation_id,
                    )
                )
                transitioned_run = None
            else:
                transitioned_run, transition_event = (
                    self.state_machine.transition_run_in_transaction(
                        transaction,
                        target_run_status,
                        actor=normalized_actor,
                        reason=transition_reason,
                        correlation_id=correlation_id,
                    )
                )
                transitioned_task = None

        return ApprovalWorkflowResult(
            approval=resolved,
            approval_event=approval_event,
            transition_event=transition_event,
            run=transitioned_run,
            task=transitioned_task,
        )

    def _find_artifact_by_idempotency(
        self,
        transaction: RunTransaction,
        run_id: UUID | str,
        task_id: UUID | str | None,
        idempotency_key: str | None,
    ) -> Artifact | None:
        """Return an existing Artifact matching the idempotency key, or None."""
        if idempotency_key is None:
            return None

        candidates = transaction.list_artifacts()
        for candidate in candidates:
            if candidate.run_id != UUID(str(run_id)):
                continue
            if candidate.task_id != (UUID(str(task_id)) if task_id is not None else None):
                continue
            if candidate.metadata.get("idempotency_key") == idempotency_key:
                return candidate
        return None

    def register_artifact(
        self,
        run_id: UUID | str,
        *,
        kind: ArtifactKind | str,
        name: str,
        uri: str,
        created_by: str,
        actor: str,
        relation: Literal["run", "input", "output"] = "run",
        task_id: UUID | str | None = None,
        content_type: str | None = None,
        checksum_sha256: str | None = None,
        size_bytes: int | None = None,
        correlation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Artifact, AuditEvent | None]:
        normalized_actor = _required_text(actor, "actor")

        if relation == "run" and task_id is not None:
            raise ArtifactRelationError(
                "Run artifacts must not include task_id"
            )

        if relation in {"input", "output"} and task_id is None:
            raise ArtifactRelationError(
                f"{relation} artifacts require task_id"
            )

        artifact = Artifact(
            run_id=UUID(str(run_id)),
            task_id=UUID(str(task_id)) if task_id is not None else None,
            kind=ArtifactKind(kind),
            name=_required_text(name, "name"),
            uri=_required_text(uri, "uri"),
            content_type=content_type,
            checksum_sha256=checksum_sha256,
            size_bytes=size_bytes,
            created_by=_required_text(created_by, "created_by"),
            metadata=dict(metadata or {}, relation=relation),
        )

        idempotency_key = artifact.metadata.get("idempotency_key")

        with self.repository.transaction(run_id) as transaction:
            run = transaction.get_run()
            task = None

            if artifact.task_id is not None:
                task = transaction.get_task(artifact.task_id)

            # Domain-level idempotency: reuse existing artifact with matching key
            if idempotency_key is not None:
                existing = self._find_artifact_by_idempotency(
                    transaction, run_id, task_id, idempotency_key
                )
                if existing is not None:
                    if self._check_domain_idempotency_match(
                        existing, artifact
                    ):
                        # Idempotent retry: return existing artifact, no new event
                        return existing, None
                    raise ArtifactConflictError(
                        f"Artifact idempotency key {idempotency_key!r} "
                        f"conflicts with existing record {existing.id}"
                    )

            transaction.create_artifact(artifact)

            updated_run = run.model_copy(deep=True)
            if artifact.id not in updated_run.artifact_ids:
                updated_run.artifact_ids.append(artifact.id)
            updated_run.updated_at = utc_now()
            transaction.save_run(updated_run)

            if task is not None:
                updated_task = task.model_copy(deep=True)
                target_ids = (
                    updated_task.input_artifact_ids
                    if relation == "input"
                    else updated_task.output_artifact_ids
                )
                if artifact.id not in target_ids:
                    target_ids.append(artifact.id)
                updated_task.updated_at = utc_now()
                transaction.save_task(updated_task)

            event = _event(
                run_id=artifact.run_id,
                task_id=artifact.task_id,
                event_type="artifact.registered",
                actor=normalized_actor,
                action="register",
                target_type="artifact",
                target_id=str(artifact.id),
                correlation_id=correlation_id,
                details={
                    "name": artifact.name,
                    "kind": artifact.kind,
                    "uri": artifact.uri,
                    "relation": relation,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                },
            )
            transaction.append_event(event)

        return artifact, event

    @staticmethod
    def _check_domain_idempotency_match(
        existing: Artifact,
        requested: Artifact,
    ) -> bool:
        """Return True if *existing* and *requested* are fully compatible."""
        if existing.run_id != requested.run_id:
            return False
        if existing.task_id != requested.task_id:
            return False
        if existing.kind != requested.kind:
            return False
        if existing.name != requested.name:
            return False
        if existing.uri != requested.uri:
            return False
        if existing.checksum_sha256 != requested.checksum_sha256:
            return False
        if existing.size_bytes != requested.size_bytes:
            return False
        if existing.content_type != requested.content_type:
            return False
        if existing.created_by != requested.created_by:
            return False
        if existing.metadata.get("format_version") != requested.metadata.get("format_version"):
            return False
        if existing.metadata.get("provenance") != requested.metadata.get("provenance"):
            return False
        if existing.metadata.get("idempotency_key") != requested.metadata.get("idempotency_key"):
            return False
        if existing.metadata.get("relation") != requested.metadata.get("relation"):
            return False
        return True

    def get_snapshot(
        self,
        run_id: UUID | str,
        *,
        event_limit: int | None = None,
    ) -> RunSnapshot:
        with self.repository.transaction(run_id) as transaction:
            return RunSnapshot(
                run=transaction.get_run(),
                tasks=transaction.list_tasks(),
                messages=transaction.list_messages(),
                route_decisions=transaction.list_route_decisions(),
                approvals=transaction.list_approvals(),
                artifacts=transaction.list_artifacts(),
                events=transaction.list_events(limit=event_limit),
            )

    @staticmethod
    def _assert_no_active_task_approval(
        transaction: RunTransaction,
        task: Task,
    ) -> None:
        if task.approval_id is None:
            return

        existing = transaction.get_approval(task.approval_id)
        if ApprovalStatus(existing.status) == ApprovalStatus.PENDING:
            raise ActiveApprovalExists(
                f"Task already has pending approval: {existing.id}"
            )
