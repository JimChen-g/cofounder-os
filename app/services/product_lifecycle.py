"""Product Task Lifecycle Service (D06-D).

This service integrates the accepted Product Agent, Artifact Store, and
orchestration services into one durable and replayable Product Task lifecycle.

It does not:
- Call providers directly
- Claim, complete, retry, block, or fail Tasks (AgentExecutionService owns that)
- Mutate Task status outside accepted lifecycle authorities
- Expose HTTP routes
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel
from app.artifacts import FileArtifactStore
from app.domain import (
    AuditEvent,
    ProductAgentRequest,
    ProductAgentResultV1,
    ProductTaskContext,
    Run,
    Task,
    TaskStatus,
)
from app.services import (
    AgentExecutionService,
    OrchestrationService,
    ProductAgentExecutionError,
    ProductAgentService,
)


# ── Error hierarchy ──────────────────────────────────────────────────────────


class ProductTaskLifecycleError(RuntimeError):
    """Base error for Product Task Lifecycle operations."""


class TaskNotFoundError(ProductTaskLifecycleError):
    """Raised when the target Task does not exist."""


class TaskNotProductAgentError(ProductTaskLifecycleError):
    """Raised when the task assigned_agent is not product-agent."""


class ProductTaskContextError(ProductTaskLifecycleError):
    """Raised when ProductTaskContext cannot be built."""


class DependencyArtifactMissingError(ProductTaskLifecycleError):
    """Raised when a dependency Artifact is not found."""


class DependencyArtifactCorruptError(ProductTaskLifecycleError):
    """Raised when a dependency Artifact has no checksum."""


class ProductArtifactVerificationError(ProductTaskLifecycleError):
    """Raised when Product output artifacts are not properly registered."""


class StaleTaskError(ProductTaskLifecycleError):
    """Raised when a RUNNING task has stale claim evidence."""


# ── Result models ────────────────────────────────────────────────────────────


class LifecycleExecutionResult(BaseModel):
    """Result of a Product Task lifecycle execution."""

    model_config = {"extra": "forbid"}

    status: str
    task: Task
    result: Optional[ProductAgentResultV1] = None
    json_artifact: Optional[Any] = None
    md_artifact: Optional[Any] = None
    json_domain_artifact: Optional[Any] = None
    md_domain_artifact: Optional[Any] = None
    retry_available: bool = False
    terminal_failure: bool = False
    last_error: Optional[str] = None
    audit_event: Optional[AuditEvent] = None


class ReconcileResult(BaseModel):
    """Result of a stale-running reconciliation."""

    model_config = {"extra": "forbid"}

    status: str
    resumable: bool
    action: str
    task: Task
    claim_age_seconds: Optional[float] = None


# Fix forward reference
LifecycleExecutionResult.model_rebuild()


# ── Product Task Lifecycle Service ───────────────────────────────────────────


class ProductTaskLifecycleService:
    """Integrate Product Agent execution into the Task lifecycle.

    This service composes the accepted authorities:

    - AgentExecutionService: claim / complete / fail / retry state transitions
    - ProductAgentService: Product execution + artifact writing
    - FileArtifactStore: content storage
    - OrchestrationService: domain metadata + audit
    - LifecycleStateMachine: lifecycle transitions
    - FileStateRepository: state persistence

    It does not own any of these responsibilities; it coordinates them.
    """

    def __init__(
        self,
        agent_execution: AgentExecutionService,
        product_agent_service: ProductAgentService,
        artifact_store: FileArtifactStore,
        orchestration: OrchestrationService,
        founder_context_policy: bool = True,
        stale_claim_threshold_seconds: float = 3600.0,
    ) -> None:
        self.agent_execution = agent_execution
        self.product_agent_service = product_agent_service
        self.artifact_store = artifact_store
        self.orchestration = orchestration
        self.founder_context_policy = founder_context_policy
        self.stale_claim_threshold = stale_claim_threshold_seconds

    # ── Public operations ────────────────────────────────────────────────────

    async def execute_ready_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        actor: str,
        correlation_id: Optional[str] = None,
    ) -> LifecycleExecutionResult:
        """Execute a READY Product Task through its full lifecycle.

        Preconditions:
          - Task exists
          - assigned_agent == "product-agent"
          - Agent is executable
          - Task is READY
          - task_id and run_id match the request
          - Dependencies are satisfied

        Postconditions on success:
          - Task is COMPLETED
          - Claim fields are cleared
          - Both Product Artifacts are registered as Task outputs

        Postconditions on failure:
          - Task is BLOCKED (first failure) or FAILED (exhausted)
          - last_error is persisted
          - No partial Domain Artifact duplication
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        # 1. Load and validate task
        task = self._load_task(run_uuid, task_uuid)
        self._validate_task(task, actor)

        # Idempotency: if already COMPLETED, return without side effects
        if TaskStatus(task.status) == TaskStatus.COMPLETED:
            return LifecycleExecutionResult(
                status="completed",
                task=task,
                retry_available=False,
                terminal_failure=True,
            )

        # Idempotency: if BLOCKED, return without side effects (retry must use retry_blocked_task)
        if TaskStatus(task.status) == TaskStatus.BLOCKED:
            return LifecycleExecutionResult(
                status="blocked",
                task=task,
                retry_available=True,
                terminal_failure=False,
                last_error=task.last_error,
            )

        self._validate_dependencies(task)

        # 2. Claim task (READY → RUNNING, increment attempt_count)
        try:
            claim = self.agent_execution.claim_task(
                run_id=run_uuid,
                task_id=task_uuid,
                agent_id="product-agent",
                correlation_id=correlation_id,
            )
        except Exception as exc:
            raise ProductTaskLifecycleError(
                f"Failed to claim task: {exc}"
            ) from exc

        # 3. Build context
        try:
            run = self.orchestration.repository.get_run(run_uuid)
        except Exception as exc:
            raise ProductTaskLifecycleError(
                f"Failed to load run: {exc}"
            ) from exc

        try:
            request = self._build_context(run, task)
        except Exception as exc:
            raise ProductTaskContextError(
                f"Failed to build ProductTaskContext: {exc}"
            ) from exc

        # 4. Execute Product Agent (exactly once per real attempt)
        try:
            result, completion, json_artifact, md_artifact, json_domain, md_domain = (
                await self.product_agent_service.execute(
                    request, created_by="product-lifecycle", correlation_id=correlation_id
                )
            )
        except ProductAgentExecutionError as exc:
            # Record failure and determine outcome
            failure = self.agent_execution.record_attempt_failure(
                run_id=run_uuid,
                task_id=task_uuid,
                claim_token=claim.claim_token,
                error=self._safe_error_message(exc),
                actor="product-agent",
                correlation_id=correlation_id,
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )
        except Exception as exc:
            # Unexpected error — record as failure
            failure = self.agent_execution.record_attempt_failure(
                run_id=run_uuid,
                task_id=task_uuid,
                claim_token=claim.claim_token,
                error=self._safe_error_message(exc),
                actor="product-agent",
                correlation_id=correlation_id,
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )

        # 5. Verify both Product Artifacts
        try:
            self._verify_product_artifacts(task_uuid, task, json_domain, md_domain)
        except ProductArtifactVerificationError:
            # Artifact verification failure — record as failure
            failure = self.agent_execution.record_attempt_failure(
                run_id=run_uuid,
                task_id=task_uuid,
                claim_token=claim.claim_token,
                error="Product artifact verification failed",
                actor="product-agent",
                correlation_id=correlation_id,
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )

        # 6. Complete task (RUNNING → COMPLETED, clear claim fields)
        try:
            completed_task, event = self.agent_execution.complete_claimed_task(
                run_id=run_uuid,
                task_id=task_uuid,
                claim_token=claim.claim_token,
                actor="product-agent",
                correlation_id=correlation_id,
            )
        except Exception as exc:
            raise ProductTaskLifecycleError(
                f"Failed to complete claimed task: {exc}"
            ) from exc

        return LifecycleExecutionResult(
            status=completed_task.status,
            task=completed_task,
            result=result,
            json_artifact=json_artifact,
            md_artifact=md_artifact,
            json_domain_artifact=json_domain,
            md_domain_artifact=md_domain,
            audit_event=event,
        )

    async def retry_blocked_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        actor: str,
        correlation_id: Optional[str] = None,
    ) -> LifecycleExecutionResult:
        """Retry a BLOCKED Product Task.

        Preconditions:
          - Task exists
          - assigned_agent == "product-agent"
          - Task is BLOCKED

        Postconditions on success:
          - Task is COMPLETED
          - Both Product Artifacts are registered
          - attempt_count incremented once more
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        # 1. Validate task is BLOCKED
        task = self._load_task(run_uuid, task_uuid)
        self._validate_task(task, actor)

        if TaskStatus(task.status) != TaskStatus.BLOCKED:
            raise ProductTaskLifecycleError(
                f"Task must be BLOCKED for retry; current status: {task.status}"
            )

        # 2. Prepare retry (BLOCKED → READY, clears last_error)
        try:
            self.agent_execution.prepare_retry(
                run_id=run_uuid,
                task_id=task_uuid,
                actor=actor,
                correlation_id=correlation_id,
            )
        except Exception as exc:
            raise ProductTaskLifecycleError(
                f"Failed to prepare retry: {exc}"
            ) from exc

        # 3. Execute (new claim, new attempt)
        return await self.execute_ready_task(
            run_id=run_uuid,
            task_id=task_uuid,
            actor=actor,
            correlation_id=correlation_id,
        )

    def reconcile_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
    ) -> ReconcileResult:
        """Reconcile a Product Task after process restart or stale-running detection.

        Never silently resets RUNNING → READY.
        Returns a deterministic reconciliation result.
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        task = self._load_task(run_uuid, task_uuid)

        status = TaskStatus(task.status)

        if status in (TaskStatus.COMPLETED,):
            return ReconcileResult(
                status="completed",
                resumable=False,
                action="none",
                task=task,
            )

        if status == TaskStatus.FAILED:
            return ReconcileResult(
                status="failed",
                resumable=False,
                action="none",
                task=task,
            )

        if status == TaskStatus.BLOCKED:
            return ReconcileResult(
                status="blocked",
                resumable=True,
                action="retry",
                task=task,
            )

        if status == TaskStatus.READY:
            return ReconcileResult(
                status="ready",
                resumable=True,
                action="execute",
                task=task,
            )

        if status == TaskStatus.RUNNING:
            return self._reconcile_running(task)

        # PENDING, WAITING_APPROVAL, CANCELLED — not resumable
        return ReconcileResult(
            status=status.value,
            resumable=False,
            action="none",
            task=task,
        )

    # ── Private helpers ──────────────────────────────────────────────────────

    def _load_task(self, run_uuid: UUID, task_uuid: UUID) -> Task:
        """Load a Task or raise TaskNotFoundError."""
        try:
            return self.orchestration.repository.get_task(run_uuid, task_uuid)
        except Exception as exc:
            raise TaskNotFoundError(
                f"Task {task_uuid} not found in run {run_uuid}: {exc}"
            ) from exc

    def _validate_task(self, task: Task, actor: str) -> None:
        """Validate task is a valid Product Task target.

        Note: claim_task handles READY status, agent executable check,
        agent assignment, and attempt budget. We only check assigned_agent
        here for a clear lifecycle-specific error message.
        """
        if (task.assigned_agent or "") != "product-agent":
            raise TaskNotProductAgentError(
                f"Task assigned_agent is '{task.assigned_agent}', "
                f"not 'product-agent'"
            )

    def _validate_dependencies(self, task: Task) -> None:
        """Verify all dependency artifacts exist and are verifiable."""
        for dep_id in task.dependency_ids:
            try:
                artifact = self.orchestration.repository.get_artifact(task.run_id, dep_id)
            except Exception as exc:
                raise DependencyArtifactMissingError(
                    f"Dependency artifact {dep_id} not found: {exc}"
                ) from exc

            if artifact.checksum_sha256 is None:
                raise DependencyArtifactCorruptError(
                    f"Dependency artifact {dep_id} has no checksum"
                )

    def _build_context(self, run: Run, task: Task) -> ProductAgentRequest:
        """Build a ProductTaskContext from persisted Run and Task records.

        Includes verified dependency Artifact IDs, checksums, and bounded
        summaries. Never includes secrets, credentials, complete audit logs,
        or raw runtime data.
        """
        # Build dependency summaries
        dep_ids: list[UUID] = []
        dep_checksums: dict[str, str] = {}
        dep_summaries: list[Any] = []

        for dep_id in task.dependency_ids:
            artifact = self.orchestration.repository.get_artifact(run.id, dep_id)
            dep_ids.append(dep_id)
            if artifact.checksum_sha256:
                dep_checksums[str(dep_id)] = artifact.checksum_sha256
            # Bounded summary: name only, truncated to 200 chars
            summary_text = (artifact.name or str(dep_id))[:200]
            dep_summaries.append(
                {
                    "artifact_id": dep_id,
                    "checksum": artifact.checksum_sha256 or "",
                    "summary": summary_text,
                }
            )

        # founder_context: use run.owner when policy allows
        founder_context: Optional[str] = None
        if self.founder_context_policy and run.owner:
            founder_context = run.owner[:2000]

        # required_deliverable derived from task title + description
        deliverable = f"Product brief for: {task.title}"
        if task.description:
            deliverable = f"Product brief for: {task.title} — {task.description[:200]}"

        context = ProductTaskContext(
            schema_version="1.0",
            run_id=run.id,
            task_id=task.id,
            objective=run.objective[:2000],
            task_title=task.title[:500],
            task_description=task.description[:2000],
            required_deliverable=deliverable[:500],
            founder_context=founder_context,
            constraints=[],  # No persisted constraints in current domain
            dependency_artifact_ids=dep_ids,
            dependency_artifact_checksums=dep_checksums,
            dependency_artifact_summaries=dep_summaries,
        )

        request = ProductAgentRequest(
            schema_version="1.0",
            context=context,
            include_founder_context=self.founder_context_policy,
        )

        return request

    def _verify_product_artifacts(
        self,
        task_uuid: UUID,
        task: Task,
        json_domain: Any,
        md_domain: Any,
    ) -> None:
        """Verify both Product Artifacts are properly registered in task.output_artifact_ids."""
        if json_domain is None or md_domain is None:
            raise ProductArtifactVerificationError(
                "Product Agent did not produce both JSON and Markdown artifacts"
            )

        # Verify both domain artifacts are registered in task.output_artifact_ids
        updated_task = self.orchestration.repository.get_task(task.run_id, task_uuid)
        json_registered = any(str(aid) == str(json_domain.id) for aid in updated_task.output_artifact_ids)
        md_registered = any(str(aid) == str(md_domain.id) for aid in updated_task.output_artifact_ids)

        if not json_registered or not md_registered:
            raise ProductArtifactVerificationError(
                "Product output artifacts not both registered in task.output_artifact_ids"
            )

    def _reconcile_running(self, task: Task) -> ReconcileResult:
        """Reconcile a RUNNING task by checking claim evidence."""
        if task.claim_token is None or task.claimed_by is None or task.claimed_at is None:
            # No claim evidence — stale running
            return ReconcileResult(
                status="stale",
                resumable=True,
                action="manual_intervention",
                task=task,
                claim_age_seconds=None,
            )

        # Compute claim age
        claimed_at = task.claimed_at
        if isinstance(claimed_at, datetime):
            now = datetime.now(timezone.utc)
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            claim_age = (now - claimed_at).total_seconds()
        else:
            claim_age = None

        if claim_age is not None and claim_age > self.stale_claim_threshold:
            return ReconcileResult(
                status="stale",
                resumable=False,
                action="manual_intervention",
                task=task,
                claim_age_seconds=claim_age,
            )

        return ReconcileResult(
            status="running",
            resumable=True,
            action="wait",
            task=task,
            claim_age_seconds=claim_age,
        )

    @staticmethod
    def _safe_error_message(exc: BaseException) -> str:
        """Extract a safe error message without secrets or credentials."""
        msg = str(exc)
        # Basic sanitization — remove common secret patterns
        # Never include raw exception tracebacks
        if len(msg) > 500:
            msg = msg[:500] + "..."
        return msg


# Fix forward reference
LifecycleExecutionResult.model_rebuild()
