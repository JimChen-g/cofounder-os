"""Complete bounded Workflow Controller with recovery and replay (D10)."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts import FileArtifactStore
from app.domain import (
    ApprovalStatus,
    Artifact,
    ArtifactSynthesisRequest,
    DependencyArtifactSummary,
    FinanceAgentRequest,
    FinanceAgentResultV1,
    FinanceTaskContext,
    ProductAgentRequest,
    ProductAgentResultV1,
    ProductTaskContext,
    RunStatus,
    Task,
    TaskStatus,
    utc_now,
)
from app.policy import (
    DeterministicPolicyGate,
    PolicyAction,
    PolicyDecision,
    PolicyDisposition,
)
from app.services.execution import AgentExecutionService
from app.services.finance_agent import FinanceAgentService
from app.services.orchestration import OrchestrationService, RunSnapshot
from app.services.product_agent import ProductAgentService
from app.synthesizers import ArtifactSynthesizer


WORKFLOW_CONTROLLER_ID = "workflow-controller"
SYNTHESIS_TASK_TYPE = "artifact_synthesis"
_APPROVAL_TTL = timedelta(hours=1)

_REQUIRED_OUTPUTS = {
    "product-agent": frozenset({"product-brief", "product-brief-md"}),
    "finance-agent": frozenset({"finance-brief", "finance-brief-md"}),
    SYNTHESIS_TASK_TYPE: frozenset(
        {
            "executive-decision-memo",
            "prd-product-brief",
            "budget-summary",
            "risk-register",
            "action-plan",
        }
    ),
}


class WorkflowControllerError(RuntimeError):
    """Base Workflow Controller error."""


class UnsupportedWorkflowTask(WorkflowControllerError):
    """Raised when a task has no implemented execution adapter."""


class WorkflowRunResult(BaseModel):
    """Bounded controller outcome and replay evidence."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    status: RunStatus
    cycles: int = Field(ge=0)
    executed_task_ids: list[UUID] = Field(default_factory=list)
    retried_task_ids: list[UUID] = Field(default_factory=list)
    reconciled_task_ids: list[UUID] = Field(default_factory=list)
    approval_ids: list[UUID] = Field(default_factory=list)
    replayed: bool = False
    stalled: bool = False
    terminal_failure: bool = False
    snapshot: RunSnapshot


class WorkflowController:
    """Own task activation, execution, retries, recovery, and run completion."""

    def __init__(
        self,
        *,
        orchestration: OrchestrationService,
        agent_execution: AgentExecutionService,
        artifact_store: FileArtifactStore,
        product_agent_service: ProductAgentService,
        finance_agent_service: FinanceAgentService,
        artifact_synthesizer: ArtifactSynthesizer,
        policy_gate: Optional[DeterministicPolicyGate] = None,
    ) -> None:
        if agent_execution.repository is not orchestration.repository:
            raise ValueError(
                "AgentExecutionService and OrchestrationService must share a repository"
            )
        self.orchestration = orchestration
        self.agent_execution = agent_execution
        self.artifact_store = artifact_store
        self.product_agent_service = product_agent_service
        self.finance_agent_service = finance_agent_service
        self.artifact_synthesizer = artifact_synthesizer
        self.policy_gate = policy_gate or DeterministicPolicyGate()

    async def run_until_terminal(
        self,
        run_id: UUID | str,
        *,
        actor: str = WORKFLOW_CONTROLLER_ID,
        correlation_id: Optional[str] = None,
        max_cycles: int = 100,
    ) -> WorkflowRunResult:
        """Drive one run until terminal, approval wait, or bounded stall."""
        if max_cycles < 1 or max_cycles > 1000:
            raise ValueError("max_cycles must be between 1 and 1000")

        run_uuid = UUID(str(run_id))
        snapshot = self.orchestration.get_snapshot(run_uuid)
        initial_status = RunStatus(snapshot.run.status)
        if initial_status == RunStatus.COMPLETED:
            self._verify_completed_snapshot(snapshot)
            return self._result(
                snapshot,
                cycles=0,
                replayed=True,
            )
        if initial_status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            return self._result(
                snapshot,
                cycles=0,
                replayed=True,
                terminal_failure=initial_status == RunStatus.FAILED,
            )
        if initial_status == RunStatus.WAITING_APPROVAL:
            return self._result(snapshot, cycles=0, stalled=True)
        if initial_status == RunStatus.QUEUED:
            self.orchestration.start_run(
                run_uuid,
                actor=actor,
                reason="Workflow Controller started execution.",
                correlation_id=correlation_id,
            )

        executed: list[UUID] = []
        retried: list[UUID] = []
        reconciled: list[UUID] = []
        approvals: list[UUID] = []

        for cycle in range(1, max_cycles + 1):
            progress = False
            snapshot = self.orchestration.get_snapshot(run_uuid)

            for task in self._tasks_with_status(snapshot, TaskStatus.RUNNING):
                if await self._recover_running_task(
                    snapshot,
                    task,
                    actor=actor,
                    correlation_id=correlation_id,
                ):
                    reconciled.append(task.id)
                    progress = True

            snapshot = self.orchestration.get_snapshot(run_uuid)
            for task in self._tasks_with_status(snapshot, TaskStatus.BLOCKED):
                if task.attempt_count < task.max_attempts:
                    self.agent_execution.prepare_retry(
                        run_uuid,
                        task.id,
                        actor=actor,
                        correlation_id=correlation_id,
                    )
                    retried.append(task.id)
                else:
                    self.orchestration.fail_task(
                        run_uuid,
                        task.id,
                        actor=actor,
                        reason="Task exhausted its configured attempt budget.",
                        correlation_id=correlation_id,
                    )
                progress = True

            snapshot = self.orchestration.get_snapshot(run_uuid)
            if any(TaskStatus(task.status) == TaskStatus.FAILED for task in snapshot.tasks):
                return self._fail_run(
                    run_uuid,
                    actor,
                    correlation_id,
                    cycle,
                    executed,
                    retried,
                    reconciled,
                    approvals,
                    "A task reached terminal failure.",
                )

            for task in self._tasks_with_status(snapshot, TaskStatus.PENDING):
                if self._dependencies_completed(task, snapshot):
                    self.orchestration.mark_task_ready(
                        run_uuid,
                        task.id,
                        actor=actor,
                        reason="All task dependencies completed.",
                        correlation_id=correlation_id,
                    )
                    progress = True

            snapshot = self.orchestration.get_snapshot(run_uuid)
            for task in self._tasks_with_status(snapshot, TaskStatus.READY):
                try:
                    action = self._policy_action(task)
                    decision = self.policy_gate.evaluate(action)
                except Exception as exc:
                    self._terminally_fail_ready_task(
                        run_uuid,
                        task,
                        f"Invalid policy action: {self._safe_error(exc)}",
                        actor,
                        correlation_id,
                    )
                    progress = True
                    continue
                if decision.disposition == PolicyDisposition.DENY:
                    self._terminally_deny_task(
                        run_uuid,
                        task,
                        decision,
                        actor,
                        correlation_id,
                    )
                    progress = True
                    continue
                if (
                    decision.disposition == PolicyDisposition.REQUIRE_APPROVAL
                    and not self._has_valid_approved_task(
                        snapshot,
                        task,
                        action,
                        decision,
                    )
                ):
                    try:
                        claim = self.agent_execution.claim_task(
                            run_uuid,
                            task.id,
                            agent_id=task.assigned_agent or "",
                            correlation_id=correlation_id,
                        )
                        action_digest = self._policy_action_digest(action)
                        approval_correlation_id = (
                            correlation_id or f"policy:{run_uuid}:{task.id}:{action_digest[:16]}"
                        )
                        approval = self.orchestration.request_approval(
                            run_uuid,
                            task_id=task.id,
                            requested_by=actor,
                            actor=actor,
                            reason="; ".join(decision.reasons),
                            expires_at=utc_now() + _APPROVAL_TTL,
                            correlation_id=approval_correlation_id,
                            metadata={
                                "policy_rule_ids": decision.rule_ids,
                                "policy_action_sha256": action_digest,
                                "policy_action_schema_version": (action.schema_version),
                                "reviewer_required": (decision.reviewer_required),
                                "claim_token": claim.claim_token,
                                "policy_correlation_id": (approval_correlation_id),
                            },
                        )
                    except Exception as exc:
                        current = self.orchestration.repository.get_task(
                            run_uuid,
                            task.id,
                        )
                        if TaskStatus(current.status) == TaskStatus.RUNNING:
                            self.agent_execution.record_attempt_failure(
                                run_uuid,
                                task.id,
                                claim_token=current.claim_token or "",
                                error=self._safe_error(exc),
                                actor=current.claimed_by or "",
                                correlation_id=correlation_id,
                            )
                        else:
                            self._terminally_fail_ready_task(
                                run_uuid,
                                task,
                                self._safe_error(exc),
                                actor,
                                correlation_id,
                            )
                        progress = True
                        continue
                    approvals.append(approval.approval.id)
                    progress = True
                    continue

                try:
                    await self._execute_ready_task(
                        run_uuid,
                        task,
                        correlation_id,
                    )
                except Exception as exc:
                    current = self.orchestration.repository.get_task(
                        run_uuid,
                        task.id,
                    )
                    if TaskStatus(current.status) == TaskStatus.READY:
                        self._terminally_fail_ready_task(
                            run_uuid,
                            current,
                            self._safe_error(exc),
                            actor,
                            correlation_id,
                        )
                executed.append(task.id)
                progress = True

            snapshot = self.orchestration.get_snapshot(run_uuid)
            if any(TaskStatus(task.status) == TaskStatus.FAILED for task in snapshot.tasks):
                return self._fail_run(
                    run_uuid,
                    actor,
                    correlation_id,
                    cycle,
                    executed,
                    retried,
                    reconciled,
                    approvals,
                    "A task reached terminal failure.",
                )

            if snapshot.tasks and all(
                TaskStatus(task.status) in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
                for task in snapshot.tasks
            ):
                self.orchestration.complete_run(
                    run_uuid,
                    actor=actor,
                    reason="All workflow tasks completed.",
                    correlation_id=correlation_id,
                )
                return self._result(
                    self.orchestration.get_snapshot(run_uuid),
                    cycles=cycle,
                    executed=executed,
                    retried=retried,
                    reconciled=reconciled,
                    approvals=approvals,
                )

            if any(
                TaskStatus(task.status) == TaskStatus.WAITING_APPROVAL for task in snapshot.tasks
            ):
                return self._result(
                    snapshot,
                    cycles=cycle,
                    executed=executed,
                    retried=retried,
                    reconciled=reconciled,
                    approvals=approvals,
                    stalled=True,
                )

            if not progress:
                return self._fail_run(
                    run_uuid,
                    actor,
                    correlation_id,
                    cycle,
                    executed,
                    retried,
                    reconciled,
                    approvals,
                    "Workflow made no progress; dependencies may be cyclic or invalid.",
                )

        return self._fail_run(
            run_uuid,
            actor,
            correlation_id,
            max_cycles,
            executed,
            retried,
            reconciled,
            approvals,
            "Workflow exceeded its bounded controller cycle limit.",
        )

    async def _execute_ready_task(
        self,
        run_id: UUID,
        task: Task,
        correlation_id: Optional[str],
    ) -> None:
        claim = self.agent_execution.claim_task(
            run_id,
            task.id,
            agent_id=task.assigned_agent or "",
            correlation_id=correlation_id,
        )
        await self._execute_claimed_task(
            run_id,
            task,
            claim.claim_token,
            correlation_id,
        )

    async def _execute_claimed_task(
        self,
        run_id: UUID,
        task: Task,
        claim_token: str,
        correlation_id: Optional[str],
    ) -> bool:
        try:
            await self._dispatch(task, correlation_id)
            current = self.orchestration.repository.get_task(run_id, task.id)
            if not self._verify_task_outputs(current):
                raise WorkflowControllerError("Task output bundle is incomplete or corrupt")
        except Exception as exc:
            self.agent_execution.record_attempt_failure(
                run_id,
                task.id,
                claim_token=claim_token,
                error=self._safe_error(exc),
                actor=task.assigned_agent or "",
                correlation_id=correlation_id,
            )
            return False

        self.agent_execution.complete_claimed_task(
            run_id,
            task.id,
            claim_token=claim_token,
            actor=task.assigned_agent or "",
            correlation_id=correlation_id,
        )
        return True

    async def _recover_running_task(
        self,
        snapshot: RunSnapshot,
        task: Task,
        *,
        actor: str,
        correlation_id: Optional[str],
    ) -> bool:
        if not task.claim_token or not task.claimed_by:
            self.orchestration.block_task(
                task.run_id,
                task.id,
                actor=actor,
                reason="RUNNING task has no durable claim evidence.",
                correlation_id=correlation_id,
            )
            return True

        if self._verify_task_outputs(task):
            self.agent_execution.complete_claimed_task(
                task.run_id,
                task.id,
                claim_token=task.claim_token,
                actor=task.claimed_by,
                correlation_id=correlation_id,
            )
            return True

        try:
            action = self._policy_action(task)
            decision = self.policy_gate.evaluate(action)
        except Exception as exc:
            self.agent_execution.record_attempt_failure(
                task.run_id,
                task.id,
                claim_token=task.claim_token,
                error=f"Policy recovery failed: {self._safe_error(exc)}",
                actor=task.claimed_by,
                correlation_id=correlation_id,
            )
            return True

        if decision.disposition == PolicyDisposition.DENY:
            self.agent_execution.record_attempt_failure(
                task.run_id,
                task.id,
                claim_token=task.claim_token,
                error=("Policy denied recovered action: " + "; ".join(decision.reasons)),
                actor=task.claimed_by,
                correlation_id=correlation_id,
            )
            return True

        if decision.disposition == PolicyDisposition.ALLOW:
            await self._execute_claimed_task(
                task.run_id,
                task,
                task.claim_token,
                correlation_id,
            )
            return True

        if self._has_valid_approved_task(snapshot, task, action, decision):
            await self._execute_claimed_task(
                task.run_id,
                task,
                task.claim_token,
                correlation_id,
            )
            return True

        self.agent_execution.record_attempt_failure(
            task.run_id,
            task.id,
            claim_token=task.claim_token,
            error=(
                "Recovered approval is missing, expired, or does not match "
                "the current policy action."
            ),
            actor=task.claimed_by,
            correlation_id=correlation_id,
        )
        return True

    async def _dispatch(
        self,
        task: Task,
        correlation_id: Optional[str],
    ) -> None:
        snapshot = self.orchestration.get_snapshot(task.run_id)
        run = snapshot.run
        inputs = self._input_summaries(task, snapshot)
        constraints = self._string_list(run.metadata.get("constraints", []))
        founder_context = run.metadata.get("founder_context")
        if founder_context is not None and not isinstance(founder_context, str):
            raise WorkflowControllerError("founder_context must be a string")

        if task.assigned_agent == "product-agent":
            product_request = ProductAgentRequest(
                context=ProductTaskContext(
                    run_id=task.run_id,
                    task_id=task.id,
                    correlation_id=correlation_id,
                    objective=run.objective,
                    task_title=task.title,
                    task_description=task.description or task.title,
                    required_deliverable=str(
                        task.metadata.get(
                            "required_deliverable",
                            "Product brief",
                        )
                    ),
                    founder_context=founder_context,
                    constraints=constraints,
                    dependency_artifact_ids=[summary.artifact_id for summary in inputs],
                    dependency_artifact_checksums={
                        str(summary.artifact_id): summary.checksum for summary in inputs
                    },
                    dependency_artifact_summaries=inputs,
                )
            )
            await self.product_agent_service.execute(
                product_request,
                created_by="product-agent",
                correlation_id=correlation_id,
            )
            return

        if task.assigned_agent == "finance-agent":
            finance_request = FinanceAgentRequest(
                context=FinanceTaskContext(
                    run_id=task.run_id,
                    task_id=task.id,
                    correlation_id=correlation_id,
                    objective=run.objective,
                    task_title=task.title,
                    task_description=task.description or task.title,
                    required_deliverable=str(
                        task.metadata.get(
                            "required_deliverable",
                            "Finance brief",
                        )
                    ),
                    founder_context=founder_context,
                    constraints=constraints,
                    dependency_artifact_ids=[summary.artifact_id for summary in inputs],
                    dependency_artifact_summaries=inputs,
                )
            )
            await self.finance_agent_service.execute(
                finance_request,
                created_by="finance-agent",
                correlation_id=correlation_id,
            )
            return

        if (
            task.assigned_agent == "executive-orchestrator"
            and task.metadata.get("task_type") == SYNTHESIS_TASK_TYPE
        ):
            product, finance, product_artifact_id, finance_artifact_id = (
                self._load_synthesis_inputs(task, snapshot)
            )
            self.artifact_synthesizer.synthesize(
                ArtifactSynthesisRequest(
                    run_id=task.run_id,
                    task_id=task.id,
                    objective=run.objective,
                    product=product,
                    finance=finance,
                    product_artifact_id=product_artifact_id,
                    finance_artifact_id=finance_artifact_id,
                    correlation_id=correlation_id,
                )
            )
            return

        raise UnsupportedWorkflowTask(
            f"No implemented adapter for task {task.id}: "
            f"agent={task.assigned_agent!r}, "
            f"task_type={task.metadata.get('task_type')!r}"
        )

    def _input_summaries(
        self,
        task: Task,
        snapshot: RunSnapshot,
    ) -> list[DependencyArtifactSummary]:
        if len(task.input_artifact_ids) > 20:
            raise WorkflowControllerError("A task cannot include more than 20 input artifacts")
        if len(task.input_artifact_ids) != len(set(task.input_artifact_ids)):
            raise WorkflowControllerError("Duplicate task input_artifact_ids are not allowed")

        artifacts_by_id = {artifact.id: artifact for artifact in snapshot.artifacts}
        valid_lineage = {task.id, *task.dependency_ids}
        summaries: list[DependencyArtifactSummary] = []
        for artifact_id in task.input_artifact_ids:
            artifact = artifacts_by_id.get(artifact_id)
            if artifact is None:
                raise WorkflowControllerError(f"Input artifact {artifact_id} does not exist")
            if artifact.run_id != task.run_id:
                raise WorkflowControllerError(f"Input artifact {artifact.id} has the wrong run")
            if artifact.task_id not in valid_lineage:
                raise WorkflowControllerError(
                    f"Input artifact {artifact.id} has invalid task lineage"
                )
            if artifact.metadata.get("relation") != "input":
                raise WorkflowControllerError(
                    f"Input artifact {artifact.id} is not registered as input"
                )
            self._verify_artifact(artifact)
            summaries.append(
                DependencyArtifactSummary(
                    artifact_id=artifact.id,
                    checksum=artifact.checksum_sha256 or "",
                    summary=(f"{artifact.name} from verified input lineage {artifact.task_id}"),
                )
            )
        return summaries

    def _load_synthesis_inputs(
        self,
        task: Task,
        snapshot: RunSnapshot,
    ) -> tuple[ProductAgentResultV1, FinanceAgentResultV1, UUID, UUID]:
        dependency_ids = set(task.dependency_ids)
        product_artifacts = [
            artifact
            for artifact in snapshot.artifacts
            if artifact.task_id in dependency_ids and artifact.name == "product-brief"
        ]
        finance_artifacts = [
            artifact
            for artifact in snapshot.artifacts
            if artifact.task_id in dependency_ids and artifact.name == "finance-brief"
        ]
        if len(product_artifacts) != 1 or len(finance_artifacts) != 1:
            raise WorkflowControllerError(
                "Synthesis requires exactly one Product JSON and one Finance JSON"
            )
        product_artifact = product_artifacts[0]
        finance_artifact = finance_artifacts[0]
        product_value = self._read_json_artifact(product_artifact)
        finance_value = self._read_json_artifact(finance_artifact)
        return (
            ProductAgentResultV1.model_validate(product_value),
            FinanceAgentResultV1.model_validate(finance_value),
            product_artifact.id,
            finance_artifact.id,
        )

    def _read_json_artifact(self, artifact: Artifact) -> Any:
        self._verify_artifact(artifact)
        return self.artifact_store.read_json(
            artifact.run_id,
            str(artifact.metadata["logical_name"]),
            str(artifact.metadata["filename"]),
            artifact.task_id,
        )

    def _verify_task_outputs(self, task: Task) -> bool:
        if task.metadata.get("execution_mode") == "deterministic_acceptance_fixture":
            return self._verify_declared_fixture_outputs(task)
        task_key = (
            SYNTHESIS_TASK_TYPE
            if task.metadata.get("task_type") == SYNTHESIS_TASK_TYPE
            else task.assigned_agent
        )
        required = _REQUIRED_OUTPUTS.get(str(task_key))
        if required is None:
            return False
        try:
            artifacts = [
                self.orchestration.repository.get_artifact(
                    task.run_id,
                    artifact_id,
                )
                for artifact_id in task.output_artifact_ids
            ]
            if len(artifacts) != len(required):
                return False
            by_name: dict[str, list[Artifact]] = {}
            for artifact in artifacts:
                if (
                    artifact.run_id != task.run_id
                    or artifact.task_id != task.id
                    or artifact.metadata.get("relation") != "output"
                ):
                    return False
                by_name.setdefault(artifact.name, []).append(artifact)
            if set(by_name) != required or any(
                len(by_name.get(name, [])) != 1 for name in required
            ):
                return False
            for name in required:
                self._verify_artifact(by_name[name][0])
            return True
        except Exception:
            return False

    def _verify_declared_fixture_outputs(self, task: Task) -> bool:
        """Verify a completed fixture task without dispatching an unimplemented Agent.

        This is an integrity-only replay path. It does not execute the declared
        model route or turn fixture output into a live model claim.
        """

        expected = task.metadata.get("expected_output_names")
        if (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(name, str) and name for name in expected)
        ):
            return False
        try:
            artifacts = [
                self.orchestration.repository.get_artifact(task.run_id, artifact_id)
                for artifact_id in task.output_artifact_ids
            ]
            if len(artifacts) != len(expected):
                return False
            names = [artifact.name for artifact in artifacts]
            if sorted(names) != sorted(expected):
                return False
            for artifact in artifacts:
                if (
                    artifact.run_id != task.run_id
                    or artifact.task_id != task.id
                    or artifact.metadata.get("relation") != "output"
                ):
                    return False
                self._verify_artifact(artifact)
            return True
        except Exception:
            return False

    def _verify_artifact(self, artifact: Artifact) -> None:
        logical_name = artifact.metadata.get("logical_name")
        filename = artifact.metadata.get("filename")
        if not logical_name or not filename:
            raise WorkflowControllerError(f"Artifact {artifact.id} lacks store addressing metadata")
        stored = self.artifact_store.verify(
            artifact.run_id,
            str(logical_name),
            str(filename),
            artifact.task_id,
        )
        if not artifact.checksum_sha256 or stored.checksum_sha256 != artifact.checksum_sha256:
            raise WorkflowControllerError(
                f"Artifact {artifact.id} checksum evidence does not match"
            )
        if artifact.size_bytes is not None and (stored.size_bytes != artifact.size_bytes):
            raise WorkflowControllerError(f"Artifact {artifact.id} size evidence does not match")
        if stored.uri != artifact.uri:
            raise WorkflowControllerError(f"Artifact {artifact.id} URI evidence does not match")

    def _verify_completed_snapshot(self, snapshot: RunSnapshot) -> None:
        invalid_task_ids = [
            task.id
            for task in snapshot.tasks
            if TaskStatus(task.status) == TaskStatus.COMPLETED
            and not self._verify_task_outputs(task)
        ]
        inconsistent_task_ids = [
            task.id
            for task in snapshot.tasks
            if TaskStatus(task.status) not in {TaskStatus.COMPLETED, TaskStatus.CANCELLED}
        ]
        invalid_task_ids.extend(inconsistent_task_ids)
        if invalid_task_ids:
            identifiers = ", ".join(str(task_id) for task_id in invalid_task_ids)
            raise WorkflowControllerError(
                "Completed run replay failed artifact integrity validation "
                f"for task(s): {identifiers}"
            )

    @staticmethod
    def _dependencies_completed(task: Task, snapshot: RunSnapshot) -> bool:
        by_id = {candidate.id: candidate for candidate in snapshot.tasks}
        return all(
            dependency_id in by_id
            and TaskStatus(by_id[dependency_id].status) == TaskStatus.COMPLETED
            for dependency_id in task.dependency_ids
        )

    @staticmethod
    def _tasks_with_status(
        snapshot: RunSnapshot,
        status: TaskStatus,
    ) -> list[Task]:
        return sorted(
            (task for task in snapshot.tasks if TaskStatus(task.status) == status),
            key=lambda task: (task.created_at, str(task.id)),
        )

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise WorkflowControllerError("constraints must be a list of strings")
        return value

    @staticmethod
    def _policy_action(task: Task) -> PolicyAction:
        value = task.metadata.get("policy_action")
        if value is not None:
            if not isinstance(value, dict):
                raise WorkflowControllerError("policy_action must be an object")
            return PolicyAction.model_validate(value)
        return PolicyAction(
            actor=task.assigned_agent or WORKFLOW_CONTROLLER_ID,
            operation="execute",
            tool_name=f"{task.assigned_agent or 'unknown'}-runtime",
        )

    @staticmethod
    def _policy_action_digest(action: PolicyAction) -> str:
        payload = json.dumps(
            action.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _has_valid_approved_task(
        cls,
        snapshot: RunSnapshot,
        task: Task,
        action: PolicyAction,
        decision: PolicyDecision,
    ) -> bool:
        if task.approval_id is None or task.claim_token is None:
            return False
        approval = next(
            (candidate for candidate in snapshot.approvals if candidate.id == task.approval_id),
            None,
        )
        if (
            approval is None
            or approval.task_id != task.id
            or ApprovalStatus(approval.status) != ApprovalStatus.APPROVED
            or not approval.decided_by
            or approval.decided_by != decision.reviewer_required
            or not approval.decision_reason
            or approval.decided_at is None
            or approval.expires_at is None
        ):
            return False

        expires_at = approval.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        else:
            expires_at = expires_at.astimezone(timezone.utc)
        if expires_at <= utc_now():
            return False

        metadata = approval.metadata
        return (
            metadata.get("policy_action_sha256") == cls._policy_action_digest(action)
            and metadata.get("policy_action_schema_version") == action.schema_version
            and metadata.get("policy_rule_ids") == decision.rule_ids
            and metadata.get("reviewer_required") == decision.reviewer_required
            and metadata.get("claim_token") == task.claim_token
            and bool(metadata.get("policy_correlation_id"))
        )

    def _terminally_deny_task(
        self,
        run_id: UUID,
        task: Task,
        decision: PolicyDecision,
        actor: str,
        correlation_id: Optional[str],
    ) -> None:
        reason = "; ".join(decision.reasons)
        self.orchestration.block_task(
            run_id,
            task.id,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )
        self.orchestration.fail_task(
            run_id,
            task.id,
            actor=actor,
            reason=f"Policy denied action: {reason}",
            correlation_id=correlation_id,
        )

    def _terminally_fail_ready_task(
        self,
        run_id: UUID,
        task: Task,
        reason: str,
        actor: str,
        correlation_id: Optional[str],
    ) -> None:
        self.orchestration.block_task(
            run_id,
            task.id,
            actor=actor,
            reason=reason,
            correlation_id=correlation_id,
        )
        self.orchestration.fail_task(
            run_id,
            task.id,
            actor=actor,
            reason=f"Task is not executable: {reason}",
            correlation_id=correlation_id,
        )

    def _fail_run(
        self,
        run_id: UUID,
        actor: str,
        correlation_id: Optional[str],
        cycles: int,
        executed: list[UUID],
        retried: list[UUID],
        reconciled: list[UUID],
        approvals: list[UUID],
        reason: str,
    ) -> WorkflowRunResult:
        snapshot = self.orchestration.get_snapshot(run_id)
        if RunStatus(snapshot.run.status) == RunStatus.RUNNING:
            self.orchestration.fail_run(
                run_id,
                actor=actor,
                reason=reason,
                correlation_id=correlation_id,
            )
        return self._result(
            self.orchestration.get_snapshot(run_id),
            cycles=cycles,
            executed=executed,
            retried=retried,
            reconciled=reconciled,
            approvals=approvals,
            terminal_failure=True,
        )

    @staticmethod
    def _result(
        snapshot: RunSnapshot,
        *,
        cycles: int,
        executed: Optional[list[UUID]] = None,
        retried: Optional[list[UUID]] = None,
        reconciled: Optional[list[UUID]] = None,
        approvals: Optional[list[UUID]] = None,
        replayed: bool = False,
        stalled: bool = False,
        terminal_failure: bool = False,
    ) -> WorkflowRunResult:
        return WorkflowRunResult(
            run_id=snapshot.run.id,
            status=RunStatus(snapshot.run.status),
            cycles=cycles,
            executed_task_ids=executed or [],
            retried_task_ids=retried or [],
            reconciled_task_ids=reconciled or [],
            approval_ids=approvals or [],
            replayed=replayed,
            stalled=stalled,
            terminal_failure=terminal_failure,
            snapshot=snapshot,
        )

    @staticmethod
    def _safe_error(exc: BaseException) -> str:
        value = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
        return value[:1000] or type(exc).__name__
