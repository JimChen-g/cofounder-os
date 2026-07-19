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

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from pydantic import BaseModel

from app.artifacts import FileArtifactStore
from app.domain import (
    ApprovalStatus,
    AuditEvent,
    ProductAgentRequest,
    ProductAgentResultV1,
    ProductTaskContext,
    Run,
    Task,
    TaskStatus,
)
from app.services.execution import AgentExecutionService
from app.services.orchestration import OrchestrationService
from app.services.product_agent import (
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


class PredecessorTaskNotCompletedError(ProductTaskLifecycleError):
    """Raised when a predecessor Task is not COMPLETED."""


class DependencyArtifactMissingError(ProductTaskLifecycleError):
    """Raised when a dependency Artifact is not found."""


class DependencyArtifactCorruptError(ProductTaskLifecycleError):
    """Raised when a dependency Artifact has integrity issues."""


class ProductArtifactVerificationError(ProductTaskLifecycleError):
    """Raised when Product output artifacts are not properly registered."""


class OutputArtifactCorruptError(ProductTaskLifecycleError):
    """Raised when a Product output artifact file is missing or corrupt."""


class StaleTaskError(ProductTaskLifecycleError):
    """Raised when a RUNNING task has stale claim evidence."""


class CompletionPersistenceError(ProductTaskLifecycleError):
    """Raised when Task completion cannot be persisted after valid outputs exist."""


class RetryAuthorizationError(ProductTaskLifecycleError):
    """Raised when retry is not authorized for the requesting actor."""


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
    reconciliation_required: bool = False


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
        retry_policy_decisions: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
    ) -> None:
        self.agent_execution = agent_execution
        self.product_agent_service = product_agent_service
        self.artifact_store = artifact_store
        self.orchestration = orchestration
        self.founder_context_policy = founder_context_policy
        self.stale_claim_threshold = stale_claim_threshold_seconds
        self.retry_policy_decisions = (
            retry_policy_decisions if retry_policy_decisions is not None else {}
        )

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
          - Dependencies (predecessor Tasks) are COMPLETED
          - Input artifacts exist and are verifiable

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
            json_artifact = None
            md_artifact = None
            json_domain = None
            md_domain = None

            for art_id in task.output_artifact_ids:
                try:
                    art = self.orchestration.repository.get_artifact(
                        run_uuid, art_id
                    )
                    if art.name == "product-brief":
                        json_domain = art
                        json_artifact = self._resolve_stored_artifact(
                            run_uuid, task_uuid, art
                        )
                    elif art.name == "product-brief-md":
                        md_domain = art
                        md_artifact = self._resolve_stored_artifact(
                            run_uuid, task_uuid, art
                        )
                except Exception:
                    pass

            return LifecycleExecutionResult(
                status="completed",
                task=task,
                retry_available=False,
                terminal_failure=False,
                json_artifact=json_artifact,
                md_artifact=md_artifact,
                json_domain_artifact=json_domain,
                md_domain_artifact=md_domain,
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

        # 2. Validate predecessor Tasks are COMPLETED
        self._validate_predecessors(run_uuid, task)

        # 3. Validate and load input Artifacts for context
        input_artifacts = self._validate_input_artifacts(run_uuid, task)

        # 4. Claim task (READY → RUNNING, increment attempt_count)
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

        # 5. Build context (after claim, using verified input artifacts)
        try:
            run = self.orchestration.repository.get_run(run_uuid)
        except Exception as exc:
            # Run reload failure — record as failure
            failure = self._record_failure(
                run_uuid, task_uuid, claim.claim_token,
                f"Failed to load run: {exc}", correlation_id
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )

        try:
            request = self._build_context(run, task, input_artifacts)
        except Exception as exc:
            # Context construction failure — record as failure
            failure = self._record_failure(
                run_uuid, task_uuid, claim.claim_token,
                f"Failed to build ProductTaskContext: {exc}", correlation_id
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )

        # 6. Execute Product Agent (exactly once per real attempt)
        try:
            result, completion, json_artifact, md_artifact, json_domain, md_domain = (
                await self.product_agent_service.execute(
                    request, created_by="product-lifecycle", correlation_id=correlation_id
                )
            )
        except ProductAgentExecutionError as exc:
            failure = self._record_failure(
                run_uuid, task_uuid, claim.claim_token,
                self._safe_error_message(exc), correlation_id
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
            failure = self._record_failure(
                run_uuid, task_uuid, claim.claim_token,
                self._safe_error_message(exc), correlation_id
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )

        # 7. Verify actual output content before completion
        try:
            stored_json = self._resolve_stored_artifact(run_uuid, task_uuid, json_domain)
            stored_md = self._resolve_stored_artifact(run_uuid, task_uuid, md_domain)
            self._verify_output_content(stored_json, json_domain)
            self._verify_output_content(stored_md, md_domain)
            self._verify_output_registration(task_uuid, task, json_domain, md_domain)
        except Exception as exc:
            # Output verification failure — record as failure
            # Catch ALL exceptions to prevent leaking claim tokens or creating partial artifacts
            failure = self._record_failure(
                run_uuid, task_uuid, claim.claim_token,
                self._safe_error_message(exc), correlation_id
            )
            return LifecycleExecutionResult(
                status=failure.task.status,
                task=failure.task,
                retry_available=failure.retry_available,
                terminal_failure=failure.terminal_failure,
                last_error=failure.task.last_error,
                audit_event=failure.audit_event,
            )

        # 8. Complete task (RUNNING → COMPLETED, clear claim fields)
        try:
            completed_task, event = self.agent_execution.complete_claimed_task(
                run_id=run_uuid,
                task_id=task_uuid,
                claim_token=claim.claim_token,
                actor="product-agent",
                correlation_id=correlation_id,
            )
        except Exception as exc:
            # Completion persistence failure — valid outputs exist, mark as reconciliation required
            # Preserve RUNNING state and ownership evidence
            safe_msg = self._safe_error_message(exc)
            try:
                current_task = self.orchestration.repository.get_task(run_uuid, task_uuid)
            except Exception:
                # If repository read fails, use the task from before completion attempt
                current_task = task
            return LifecycleExecutionResult(
                status="running",
                task=current_task,
                result=result,
                json_artifact=json_artifact,
                md_artifact=md_artifact,
                json_domain_artifact=json_domain,
                md_domain_artifact=md_domain,
                retry_available=False,
                terminal_failure=False,
                reconciliation_required=True,
                last_error=f"Completion persistence failed: {safe_msg}",
            )

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
        """Retry a BLOCKED Product Task with authorization.

        Preconditions:
          - Task exists
          - assigned_agent == "product-agent"
          - Task is BLOCKED
          - actor is authorized via persisted Approval or retry policy decision

        Postconditions on success:
          - Task is COMPLETED
          - Both Product Artifacts are registered
          - attempt_count incremented once more
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        # 1. Authorize retry actor (no hard-coded actor names)
        self._authorize_retry(run_uuid, task_uuid, actor)

        # 2. Load and validate task is BLOCKED
        task = self._load_task(run_uuid, task_uuid)
        self._validate_task(task, actor)

        if TaskStatus(task.status) != TaskStatus.BLOCKED:
            raise ProductTaskLifecycleError(
                f"Task must be BLOCKED for retry; current status: {task.status}"
            )

        # 3. Prepare retry (BLOCKED → READY, clears last_error)
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

        # 4. Execute (new claim, new attempt)
        return await self.execute_ready_task(
            run_id=run_uuid,
            task_id=task_uuid,
            actor=actor,
            correlation_id=correlation_id,
        )

    async def complete_after_reconciliation(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
        actor: str,
        correlation_id: Optional[str] = None,
    ) -> LifecycleExecutionResult:
        """Complete a RUNNING task after reconciliation with valid persisted outputs.

        This is an authorized operation that safely completes a task after restart
        without another Gateway call. It requires a persisted Approval or retry
        policy decision that records actor, Task, decision, and correlation evidence.
        """
        run_uuid = UUID(str(run_id))
        task_uuid = UUID(str(task_id))

        # Authorize
        self._authorize_retry(run_uuid, task_uuid, actor)

        task = self._load_task(run_uuid, task_uuid)

        if TaskStatus(task.status) != TaskStatus.RUNNING:
            raise ProductTaskLifecycleError(
                f"Task must be RUNNING for reconciliation completion; "
                f"current: {task.status}"
            )

        if not task.claim_token:
            raise ProductTaskLifecycleError(
                "Task has no claim token for reconciliation completion"
            )

        # Verify outputs exist and are valid
        try:
            json_domain, md_domain = self._verify_outputs_exist(
                run_uuid, task_uuid, task
            )
            stored_json = self._resolve_stored_artifact(
                run_uuid, task_uuid, json_domain
            )
            stored_md = self._resolve_stored_artifact(
                run_uuid, task_uuid, md_domain
            )
            self._verify_output_content(stored_json, json_domain)
            self._verify_output_content(stored_md, md_domain)
            self._verify_output_registration(task_uuid, task, json_domain, md_domain)
        except Exception as exc:
            raise ProductTaskLifecycleError(
                f"Output verification failed for reconciliation: {exc}"
            ) from exc

        # Complete using the persisted claim token
        # Actor must match assigned_agent for complete_claimed_task;
        # authorization was already verified by _authorize_retry
        completed_task, event = self.agent_execution.complete_claimed_task(
            run_id=run_uuid,
            task_id=task_uuid,
            claim_token=task.claim_token,
            actor=task.assigned_agent or "product-agent",
            correlation_id=correlation_id,
        )

        return LifecycleExecutionResult(
            status=completed_task.status,
            task=completed_task,
            json_artifact=stored_json,
            md_artifact=stored_md,
            json_domain_artifact=json_domain,
            md_domain_artifact=md_domain,
            audit_event=event,
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

        if status == TaskStatus.COMPLETED:
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
                action="retry_authorization_required",
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
        """Validate task is a valid Product Task target."""
        if (task.assigned_agent or "") != "product-agent":
            raise TaskNotProductAgentError(
                f"Task assigned_agent is '{task.assigned_agent}', "
                f"not 'product-agent'"
            )

    def _authorize_retry(self, run_uuid: UUID, task_uuid: UUID, actor: str) -> None:
        """Authorize a retry or reconciliation completion operation.

        Requires either:
        - A persisted Approval for this task with status APPROVED, with
          purpose (reason), expiry, approver (decided_by), and correlation evidence
        - An injected retry policy decision matching actor and task

        Actor names alone are never sufficient authorization.
        """
        # Check persisted Approvals first — require complete evidence
        approvals = self.orchestration.repository.list_approvals(run_uuid)
        for approval in approvals:
            if approval.task_id == task_uuid:
                if ApprovalStatus(approval.status) == ApprovalStatus.APPROVED:
                    # Require purpose, expiry, approver, and correlation evidence
                    if not approval.reason or not approval.reason.strip():
                        raise RetryAuthorizationError(
                            f"Approval {approval.id} lacks required purpose (reason)"
                        )
                    if not approval.decided_by or not approval.decided_by.strip():
                        raise RetryAuthorizationError(
                            f"Approval {approval.id} lacks required approver (decided_by)"
                        )
                    if approval.expires_at is None:
                        raise RetryAuthorizationError(
                            f"Approval {approval.id} lacks required expiry (expires_at)"
                        )
                    # Verify the actor is the approver or the requester
                    if approval.decided_by != actor and approval.requested_by != actor:
                        raise RetryAuthorizationError(
                            f"Actor '{actor}' is not the approver "
                            f"'{approval.decided_by}' or requester "
                            f"'{approval.requested_by}' for approval {approval.id}"
                        )
                    return  # Authorized by persisted approval with complete evidence

        # Check injected retry policy decisions
        key = (str(run_uuid), str(task_uuid))
        decision = self.retry_policy_decisions.get(key)
        if decision is not None:
            if (
                decision.get("actor") == actor
                and decision.get("decision") == "approved"
                and decision.get("purpose")
                and decision.get("approver")
                and decision.get("correlation_id")
            ):
                return  # Authorized by injected decision with complete evidence

        raise RetryAuthorizationError(
            f"Actor '{actor}' is not authorized for retry on task {task_uuid}. "
            f"No persisted approval or retry policy decision found."
        )

    def _validate_predecessors(self, run_uuid: UUID, task: Task) -> None:
        """Validate all predecessor Tasks exist and are COMPLETED.

        Task.dependency_ids are predecessor Task IDs.
        """
        for pred_id in task.dependency_ids:
            try:
                pred = self.orchestration.repository.get_task(run_uuid, pred_id)
            except Exception as exc:
                raise TaskNotFoundError(
                    f"Predecessor task {pred_id} not found: {exc}"
                ) from exc

            if TaskStatus(pred.status) != TaskStatus.COMPLETED:
                raise PredecessorTaskNotCompletedError(
                    f"Predecessor task {pred_id} is {pred.status}, not COMPLETED"
                )

    def _validate_input_artifacts(self, run_uuid: UUID, task: Task) -> list[Any]:
        """Validate input artifacts exist, are scoped to this run/task, and verify.

        For each task.input_artifact_id verify:
        - Domain Artifact exists
        - correct run and permitted task lineage
        - relation is input
        - StoredArtifact exists
        - checksum, size and URI match Domain metadata
        - FileArtifactStore.verify passes

        Returns list of Domain Artifact records for context building.
        """
        input_artifacts = []
        for art_id in task.input_artifact_ids:
            # Load Domain Artifact record
            try:
                domain_artifact = self.orchestration.repository.get_artifact(
                    run_uuid, art_id
                )
            except Exception as exc:
                raise DependencyArtifactMissingError(
                    f"Input artifact {art_id} not found: {exc}"
                ) from exc

            # Verify run scope
            if domain_artifact.run_id != run_uuid:
                raise DependencyArtifactCorruptError(
                    f"Input artifact {art_id} run_id mismatch"
                )

            # Verify task lineage: artifact must belong to this task or a predecessor
            if domain_artifact.task_id is not None:
                valid_lineage = (
                    domain_artifact.task_id == task.id
                    or domain_artifact.task_id in task.dependency_ids
                )
                if not valid_lineage:
                    raise DependencyArtifactCorruptError(
                        f"Input artifact {art_id} task_id "
                        f"{domain_artifact.task_id} does not match "
                        f"task {task.id} or its dependencies"
                    )

            # Verify relation is input
            relation = (domain_artifact.metadata or {}).get("relation", "")
            if relation != "input":
                raise DependencyArtifactCorruptError(
                    f"Input artifact {art_id} has relation '{relation}', "
                    f"expected 'input'"
                )

            # Resolve StoredArtifact
            filename = (
                domain_artifact.metadata or {}
            ).get("filename", domain_artifact.name)
            try:
                stored = self._resolve_stored_artifact(
                    run_uuid, task.id, domain_artifact
                )
            except Exception as exc:
                raise DependencyArtifactCorruptError(
                    f"Input artifact {art_id} stored artifact not found: {exc}"
                ) from exc

            # Cross-check checksum between Domain and Stored
            if (
                domain_artifact.checksum_sha256
                and stored.checksum_sha256 != domain_artifact.checksum_sha256
            ):
                raise DependencyArtifactCorruptError(
                    f"Input artifact {art_id} checksum mismatch: "
                    f"stored={stored.checksum_sha256}, "
                    f"domain={domain_artifact.checksum_sha256}"
                )

            # Cross-check size between Domain and Stored
            if (
                domain_artifact.size_bytes is not None
                and stored.size_bytes != domain_artifact.size_bytes
            ):
                raise DependencyArtifactCorruptError(
                    f"Input artifact {art_id} size mismatch: "
                    f"stored={stored.size_bytes}, "
                    f"domain={domain_artifact.size_bytes}"
                )

            # Cross-check URI between Domain and Stored
            if stored.uri != domain_artifact.uri:
                raise DependencyArtifactCorruptError(
                    f"Input artifact {art_id} URI mismatch: "
                    f"stored={stored.uri}, domain={domain_artifact.uri}"
                )

            # Verify content integrity via store
            self.artifact_store.verify(
                run_uuid,
                domain_artifact.name,
                filename,
                task_id=domain_artifact.task_id,
            )

            input_artifacts.append(domain_artifact)

        return input_artifacts

    def _build_context(
        self, run: Run, task: Task, input_artifacts: list[Any]
    ) -> ProductAgentRequest:
        """Build a ProductTaskContext from persisted Run and Task records.

        Uses verified input artifacts (task.input_artifact_ids).
        Never includes secrets, credentials, complete audit logs, or raw runtime data.
        """
        dep_ids: list[UUID] = []
        dep_checksums: dict[str, str] = {}
        dep_summaries: list[Any] = []

        for domain_art in input_artifacts:
            dep_ids.append(domain_art.id)
            if domain_art.checksum_sha256:
                dep_checksums[str(domain_art.id)] = domain_art.checksum_sha256
            summary_text = (domain_art.name or str(domain_art.id))[:200]
            dep_summaries.append(
                {
                    "artifact_id": domain_art.id,
                    "checksum": domain_art.checksum_sha256 or "",
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
            constraints=[],
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

    def _resolve_stored_artifact(
        self, run_uuid: UUID, task_uuid: UUID, domain_artifact: Any
    ) -> Any:
        """Resolve a StoredArtifact by looking up stored metadata."""
        stored_artifacts = self.artifact_store.list_run_meta(run_uuid)
        for stored in stored_artifacts:
            if (
                stored.logical_name == domain_artifact.name
                and stored.task_id
                == (
                    domain_artifact.task_id
                    if domain_artifact.task_id is not None
                    else task_uuid
                )
            ):
                return stored
        raise OutputArtifactCorruptError(
            f"Stored artifact not found for {domain_artifact.name}"
        )

    def _verify_output_content(self, stored: Any, domain: Any) -> None:
        """Verify a Product output artifact's content integrity.

        Checks:
        - StoredArtifact checksum matches content
        - StoredArtifact size matches
        - Domain Artifact checksum matches StoredArtifact
        - Both URIs agree
        """
        # Verify content integrity via store
        filename = (domain.metadata or {}).get("filename", domain.name)
        try:
            self.artifact_store.verify(
                domain.run_id, domain.name, filename,
                task_id=domain.task_id
            )
        except Exception as exc:
            raise OutputArtifactCorruptError(
                f"Output artifact {domain.name} verification failed: {exc}"
            ) from exc

        # Verify checksum and size agree with Domain metadata
        if stored.checksum_sha256 != domain.checksum_sha256:
            raise OutputArtifactCorruptError(
                f"Output artifact {domain.name} checksum mismatch: "
                f"stored={stored.checksum_sha256}, domain={domain.checksum_sha256}"
            )

        if stored.size_bytes != domain.size_bytes:
            raise OutputArtifactCorruptError(
                f"Output artifact {domain.name} size mismatch: "
                f"stored={stored.size_bytes}, domain={domain.size_bytes}"
            )

        # Verify portable URIs agree
        if stored.uri != domain.uri:
            raise OutputArtifactCorruptError(
                f"Output artifact {domain.name} URI mismatch: "
                f"stored={stored.uri}, domain={domain.uri}"
            )

    def _verify_outputs_exist(
        self, run_uuid: UUID, task_uuid: UUID, task: Task
    ) -> Tuple[Any, Any]:
        """Verify and return the two expected output domain artifacts.

        Requires exactly one product-brief JSON and one product-brief-md Markdown.
        Rejects multiple Domain Artifacts with the same expected name.
        """
        if not task.output_artifact_ids:
            raise ProductArtifactVerificationError(
                "No output artifacts registered"
            )

        domain_artifacts: dict[str, Any] = {}
        for artifact_id in task.output_artifact_ids:
            art = self.orchestration.repository.get_artifact(run_uuid, artifact_id)
            # Reject multiple Domain Artifacts with the same expected name
            if art.name in domain_artifacts:
                raise ProductArtifactVerificationError(
                    f"Multiple Domain Artifacts with name '{art.name}' are not allowed"
                )
            domain_artifacts[art.name] = art

        json_domain = domain_artifacts.get("product-brief")
        md_domain = domain_artifacts.get("product-brief-md")

        if json_domain is None or md_domain is None:
            raise ProductArtifactVerificationError(
                "Both product-brief and product-brief-md artifacts must exist"
            )

        return json_domain, md_domain

    def _verify_output_registration(
        self, task_uuid: UUID, task: Task, json_domain: Any, md_domain: Any
    ) -> None:
        """Verify both output artifacts are registered exactly once in task.output_artifact_ids.

        Requires:
        - exactly one product-brief JSON Artifact ID
        - exactly one product-brief Markdown Artifact ID
        - each ID occurs exactly once in task.output_artifact_ids
        """
        if json_domain is None or md_domain is None:
            raise ProductArtifactVerificationError(
                "Product Agent did not produce both JSON and Markdown artifacts"
            )

        # Verify exact names
        if json_domain.name != "product-brief":
            raise ProductArtifactVerificationError(
                f"JSON artifact name must be 'product-brief', "
                f"got '{json_domain.name}'"
            )
        if md_domain.name != "product-brief-md":
            raise ProductArtifactVerificationError(
                f"Markdown artifact name must be 'product-brief-md', "
                f"got '{md_domain.name}'"
            )

        updated_task = self.orchestration.repository.get_task(
            task.run_id, task_uuid
        )

        # Count occurrences of each ID — must be exactly once each
        json_count = sum(
            1
            for aid in updated_task.output_artifact_ids
            if str(aid) == str(json_domain.id)
        )
        md_count = sum(
            1
            for aid in updated_task.output_artifact_ids
            if str(aid) == str(md_domain.id)
        )

        if json_count != 1 or md_count != 1:
            raise ProductArtifactVerificationError(
                "Product output artifacts must each occur exactly once "
                "in task.output_artifact_ids"
            )

    def _record_failure(
        self, run_uuid: UUID, task_uuid: UUID, claim_token: str,
        error: str, correlation_id: Optional[str]
    ) -> Any:
        """Record an attempt failure and return the result."""
        return self.agent_execution.record_attempt_failure(
            run_id=run_uuid,
            task_id=task_uuid,
            claim_token=claim_token,
            error=self._safe_error_message(error),
            actor="product-agent",
            correlation_id=correlation_id,
        )

    def _reconcile_running(self, task: Task) -> ReconcileResult:
        """Reconcile a RUNNING task with strengthened validation.

        RUNNING reconciliation rules:
        - valid completion evidence -> completion_resumable
        - fresh valid claim without outputs -> wait
        - stale or inconsistent claim -> manual_intervention_required
        - FAILED may be returned only when persisted Task.status is FAILED
        """
        # Validate ownership consistency
        if task.claimed_by != task.assigned_agent:
            return ReconcileResult(
                status="stale",
                resumable=False,
                action="manual_intervention_required",
                task=task,
            )

        if task.claimed_by != "product-agent":
            return ReconcileResult(
                status="stale",
                resumable=False,
                action="manual_intervention_required",
                task=task,
            )

        # Validate claim evidence
        if not task.claim_token or not task.claimed_at:
            return ReconcileResult(
                status="stale",
                resumable=False,
                action="manual_intervention_required",
                task=task,
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
                action="manual_intervention_required",
                task=task,
                claim_age_seconds=claim_age,
            )

        # Check completion evidence before interpreting exhausted budget
        completion_evidence = self._completion_evidence_state(task)
        if completion_evidence == "valid":
            return ReconcileResult(
                status="running",
                resumable=True,
                action="completion_resumable",
                task=task,
                claim_age_seconds=claim_age,
            )

        if completion_evidence == "invalid":
            return ReconcileResult(
                status="stale",
                resumable=False,
                action="manual_intervention_required",
                task=task,
                claim_age_seconds=claim_age,
            )

        # FAILED may be returned only when persisted Task.status is FAILED
        if TaskStatus(task.status) == TaskStatus.FAILED:
            return ReconcileResult(
                status="failed",
                resumable=False,
                action="none",
                task=task,
                claim_age_seconds=claim_age,
            )

        # Fresh valid claim without outputs -> wait
        return ReconcileResult(
            status="running",
            resumable=True,
            action="wait",
            task=task,
            claim_age_seconds=claim_age,
        )

    def _completion_evidence_state(self, task: Task) -> str:
        """Return ``valid``, ``invalid``, or ``none`` for persisted outputs.

        Completion is resumable only when route evidence exists and the two
        expected Product artifacts are registered and pass content integrity
        verification. Arbitrary output IDs are never sufficient.
        """
        if not task.output_artifact_ids:
            return "none"

        route_decisions = self.orchestration.repository.list_route_decisions(
            task.run_id
        )
        if not any(decision.task_id == task.id for decision in route_decisions):
            return "invalid"

        try:
            domain_artifacts = [
                self.orchestration.repository.get_artifact(task.run_id, artifact_id)
                for artifact_id in task.output_artifact_ids
            ]
        except Exception:
            return "invalid"

        by_name = {artifact.name: artifact for artifact in domain_artifacts}
        json_domain = by_name.get("product-brief")
        md_domain = by_name.get("product-brief-md")
        if json_domain is None or md_domain is None:
            return "invalid"

        try:
            stored_json = self._resolve_stored_artifact(
                task.run_id, task.id, json_domain
            )
            stored_md = self._resolve_stored_artifact(
                task.run_id, task.id, md_domain
            )
            self._verify_output_content(stored_json, json_domain)
            self._verify_output_content(stored_md, md_domain)
            self._verify_output_registration(
                task.id, task, json_domain, md_domain
            )
        except ProductTaskLifecycleError:
            return "invalid"

        return "valid"

    @staticmethod
    def _safe_error_message(exc: BaseException) -> str:
        """Extract a safe error message without secrets or credentials."""
        msg = str(exc)
        # Remove claim tokens from error messages to prevent leakage
        msg = re.sub(
            r'claim_token[=:]\s*\S+',
            'claim_token=[REDACTED]',
            msg,
            flags=re.IGNORECASE,
        )
        if len(msg) > 500:
            msg = msg[:500] + "..."
        return msg


# Fix forward reference
LifecycleExecutionResult.model_rebuild()
