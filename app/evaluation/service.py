"""Deterministic, read-only evaluation over persisted product evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable
from uuid import UUID

from app.artifacts import ArtifactStoreError, FileArtifactStore
from app.domain import ApprovalStatus, RunStatus, Task, TaskStatus, utc_now
from app.evaluation.models import (
    AgentPerformance,
    DimensionStatus,
    DimensionKey,
    EvaluationDimension,
    EvaluationGrade,
    EvaluationSummary,
    RunEvaluation,
)
from app.services.orchestration import OrchestrationService, RunSnapshot


REQUIRED_ARTIFACTS = frozenset(
    {
        "product-brief",
        "product-brief-md",
        "finance-brief",
        "finance-brief-md",
        "executive-decision-memo",
        "prd-product-brief",
        "budget-summary",
        "risk-register",
        "action-plan",
    }
)

_DIMENSION_LABELS: dict[DimensionKey, str] = {
    "workflow": "Workflow outcome",
    "execution": "Execution reliability",
    "artifacts": "Artifact evidence",
    "governance": "Governance",
    "auditability": "Auditability",
}
_DIMENSION_WEIGHTS: dict[DimensionKey, float] = {
    "workflow": 0.25,
    "execution": 0.25,
    "artifacts": 0.25,
    "governance": 0.15,
    "auditability": 0.10,
}


def _percentage(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 1)


def _dimension_status(score: float) -> DimensionStatus:
    if score >= 85:
        return "pass"
    if score >= 50:
        return "attention"
    return "fail"


def _grade(score: float) -> EvaluationGrade:
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 50:
        return "attention"
    return "critical"


def _dimension(
    key: DimensionKey,
    score: float,
    *evidence: str,
) -> EvaluationDimension:
    bounded = round(max(0.0, min(100.0, score)), 1)
    return EvaluationDimension(
        key=key,
        label=_DIMENSION_LABELS[key],
        score=bounded,
        weight=_DIMENSION_WEIGHTS[key],
        status=_dimension_status(bounded),
        evidence=list(evidence),
    )


class EvaluationService:
    """Compute repeatable scores without changing authoritative product state."""

    def __init__(
        self,
        orchestration: OrchestrationService,
        artifact_store: FileArtifactStore,
    ) -> None:
        self.orchestration = orchestration
        self.artifact_store = artifact_store

    def evaluate_run(self, run_id: UUID | str) -> RunEvaluation:
        """Evaluate one Run from a consistent repository snapshot."""

        return self.evaluate_snapshot(
            self.orchestration.get_snapshot(run_id),
        )

    def evaluate_snapshot(self, snapshot: RunSnapshot) -> RunEvaluation:
        """Evaluate an already-loaded snapshot without writes or provider calls."""

        run = snapshot.run
        run_status = RunStatus(run.status)
        task_count = len(snapshot.tasks)
        completed_tasks = sum(
            TaskStatus(task.status) == TaskStatus.COMPLETED
            for task in snapshot.tasks
        )
        failed_tasks = sum(
            TaskStatus(task.status) == TaskStatus.FAILED
            for task in snapshot.tasks
        )
        retry_count = sum(
            max(0, task.attempt_count - 1)
            for task in snapshot.tasks
        )

        dimensions = [
            self._workflow_dimension(run_status),
            self._execution_dimension(
                task_count=task_count,
                completed_tasks=completed_tasks,
                retry_count=retry_count,
                first_pass_tasks=sum(
                    TaskStatus(task.status) == TaskStatus.COMPLETED
                    and task.attempt_count <= 1
                    for task in snapshot.tasks
                ),
            ),
        ]
        artifact_dimension, verified_artifacts = self._artifact_dimension(
            snapshot,
        )
        dimensions.append(artifact_dimension)
        dimensions.append(self._governance_dimension(snapshot))
        dimensions.append(self._auditability_dimension(snapshot))

        overall_score = round(
            sum(item.score * item.weight for item in dimensions),
            1,
        )
        pending_approvals = sum(
            ApprovalStatus(approval.status) == ApprovalStatus.PENDING
            for approval in snapshot.approvals
        )
        start = run.started_at or run.created_at
        end = run.completed_at or run.updated_at
        duration_seconds = max(0.0, (end - start).total_seconds())

        return RunEvaluation(
            run_id=run.id,
            objective=run.objective,
            owner=run.owner,
            status=run_status.value,
            created_at=run.created_at,
            updated_at=run.updated_at,
            duration_seconds=round(duration_seconds, 1),
            overall_score=overall_score,
            grade=_grade(overall_score),
            task_count=task_count,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            retry_count=retry_count,
            artifact_count=len(snapshot.artifacts),
            required_artifact_count=len(REQUIRED_ARTIFACTS),
            verified_artifact_count=verified_artifacts,
            pending_approval_count=pending_approvals,
            providers=sorted(
                {decision.provider for decision in snapshot.route_decisions}
            ),
            models=sorted(
                {
                    decision.selected_model
                    for decision in snapshot.route_decisions
                }
            ),
            dimensions=dimensions,
            agent_performance=self._agent_performance(snapshot),
        )

    def summary(self, *, limit: int = 50) -> EvaluationSummary:
        """Aggregate a bounded newest-first set of persisted Runs."""

        runs = self.orchestration.repository.list_runs(limit=limit)
        evaluations = [
            self.evaluate_run(run.id)
            for run in runs
        ]
        run_count = len(evaluations)
        completed_runs = sum(
            item.status == RunStatus.COMPLETED.value
            for item in evaluations
        )
        total_tasks = sum(item.task_count for item in evaluations)
        completed_tasks = sum(item.completed_tasks for item in evaluations)
        total_artifacts = sum(item.artifact_count for item in evaluations)
        verified_artifacts = sum(
            item.verified_artifact_count
            for item in evaluations
        )

        return EvaluationSummary(
            generated_at=utc_now(),
            run_count=run_count,
            completion_rate=_percentage(completed_runs, run_count),
            average_score=(
                round(
                    sum(item.overall_score for item in evaluations)
                    / run_count,
                    1,
                )
                if run_count
                else 0.0
            ),
            task_success_rate=_percentage(completed_tasks, total_tasks),
            artifact_integrity_rate=_percentage(
                verified_artifacts,
                total_artifacts,
            ),
            total_retries=sum(item.retry_count for item in evaluations),
            status_distribution=dict(
                sorted(Counter(item.status for item in evaluations).items())
            ),
            grade_distribution=dict(
                sorted(Counter(item.grade for item in evaluations).items())
            ),
            provider_distribution=dict(
                sorted(
                    Counter(
                        provider
                        for item in evaluations
                        for provider in item.providers
                    ).items()
                )
            ),
            agent_performance=self._aggregate_agents(evaluations),
            recent_runs=evaluations,
        )

    @staticmethod
    def _workflow_dimension(status: RunStatus) -> EvaluationDimension:
        score_by_status = {
            RunStatus.COMPLETED: 100.0,
            RunStatus.RUNNING: 50.0,
            RunStatus.WAITING_APPROVAL: 50.0,
            RunStatus.QUEUED: 25.0,
            RunStatus.FAILED: 0.0,
            RunStatus.CANCELLED: 0.0,
        }
        score = score_by_status[status]
        return _dimension(
            "workflow",
            score,
            f"Persisted Run status is {status.value}.",
        )

    @staticmethod
    def _execution_dimension(
        *,
        task_count: int,
        completed_tasks: int,
        retry_count: int,
        first_pass_tasks: int,
    ) -> EvaluationDimension:
        completion = _percentage(completed_tasks, task_count)
        first_pass = _percentage(first_pass_tasks, task_count)
        score = (completion * 0.70) + (first_pass * 0.30)
        return _dimension(
            "execution",
            score,
            f"{completed_tasks}/{task_count} governed tasks completed.",
            f"{first_pass_tasks}/{task_count} tasks completed on the first attempt.",
            f"{retry_count} bounded retries recorded.",
        )

    def _artifact_dimension(
        self,
        snapshot: RunSnapshot,
    ) -> tuple[EvaluationDimension, int]:
        present: set[str] = set()
        verified_required: set[str] = set()
        verified_count = 0

        for artifact in snapshot.artifacts:
            logical_name = artifact.metadata.get("logical_name")
            filename = artifact.metadata.get("filename")
            if not isinstance(logical_name, str):
                logical_name = artifact.name
            if logical_name in REQUIRED_ARTIFACTS:
                present.add(logical_name)
            if not isinstance(filename, str):
                continue

            try:
                stored = self.artifact_store.verify(
                    artifact.run_id,
                    logical_name,
                    filename,
                    artifact.task_id,
                )
            except (ArtifactStoreError, ValueError):
                continue

            matches = (
                bool(artifact.checksum_sha256)
                and stored.checksum_sha256 == artifact.checksum_sha256
                and stored.uri == artifact.uri
                and (
                    artifact.size_bytes is None
                    or stored.size_bytes == artifact.size_bytes
                )
            )
            if not matches:
                continue
            verified_count += 1
            if logical_name in REQUIRED_ARTIFACTS:
                verified_required.add(logical_name)

        completeness = _percentage(
            len(present),
            len(REQUIRED_ARTIFACTS),
        )
        required_integrity = _percentage(
            len(verified_required),
            len(REQUIRED_ARTIFACTS),
        )
        registry_integrity = _percentage(
            verified_count,
            len(snapshot.artifacts),
        )
        score = (
            completeness * 0.35
            + required_integrity * 0.45
            + registry_integrity * 0.20
        )
        missing = sorted(REQUIRED_ARTIFACTS - present)
        evidence = [
            (
                f"{len(present)}/{len(REQUIRED_ARTIFACTS)} required outputs "
                "are registered."
            ),
            (
                f"{len(verified_required)}/{len(REQUIRED_ARTIFACTS)} required "
                "outputs passed store integrity checks."
            ),
            (
                f"{verified_count}/{len(snapshot.artifacts)} registered "
                "artifacts match persisted checksum, URI, and size evidence."
            ),
        ]
        if missing:
            evidence.append(
                "Missing required outputs: " + ", ".join(missing) + "."
            )
        return _dimension("artifacts", score, *evidence), verified_count

    @staticmethod
    def _governance_dimension(
        snapshot: RunSnapshot,
    ) -> EvaluationDimension:
        total = len(snapshot.approvals)
        pending = sum(
            ApprovalStatus(approval.status) == ApprovalStatus.PENDING
            for approval in snapshot.approvals
        )
        resolved = total - pending
        score = 100.0 if total == 0 else _percentage(resolved, total)
        return _dimension(
            "governance",
            score,
            f"{resolved}/{total} approval decisions are resolved.",
            f"{pending} approval decisions remain pending.",
        )

    @staticmethod
    def _auditability_dimension(
        snapshot: RunSnapshot,
    ) -> EvaluationDimension:
        event_types = {event.event_type for event in snapshot.events}
        expected = {"run.created"}
        if snapshot.tasks:
            expected.update({"task.created", "route.recorded"})
        if snapshot.artifacts:
            expected.add("artifact.registered")
        if snapshot.approvals:
            expected.add("approval.requested")
        if any(
            ApprovalStatus(approval.status) != ApprovalStatus.PENDING
            for approval in snapshot.approvals
        ):
            expected.add("approval.resolved")

        present_events = expected & event_types
        event_coverage = _percentage(len(present_events), len(expected))
        task_ids = {task.id for task in snapshot.tasks}
        routed_task_ids = {
            decision.task_id
            for decision in snapshot.route_decisions
            if decision.task_id is not None
        }
        route_coverage = (
            _percentage(len(task_ids & routed_task_ids), len(task_ids))
            if task_ids
            else 100.0
        )
        score = (event_coverage * 0.60) + (route_coverage * 0.40)
        missing_events = sorted(expected - event_types)
        evidence = [
            (
                f"{len(present_events)}/{len(expected)} expected lifecycle "
                "event types are present."
            ),
            (
                f"{len(task_ids & routed_task_ids)}/{len(task_ids)} tasks "
                "have persisted route evidence."
            ),
        ]
        if missing_events:
            evidence.append(
                "Missing event evidence: " + ", ".join(missing_events) + "."
            )
        return _dimension("auditability", score, *evidence)

    @staticmethod
    def _agent_performance(
        snapshot: RunSnapshot,
    ) -> list[AgentPerformance]:
        grouped: dict[str, list[Task]] = defaultdict(list)
        for task in snapshot.tasks:
            grouped[task.assigned_agent or "unassigned"].append(task)

        performance: list[AgentPerformance] = []
        for agent_id, tasks in sorted(grouped.items()):
            completed = sum(
                TaskStatus(task.status) == TaskStatus.COMPLETED
                for task in tasks
            )
            failed = sum(
                TaskStatus(task.status) == TaskStatus.FAILED
                for task in tasks
            )
            retries = sum(
                max(0, task.attempt_count - 1)
                for task in tasks
            )
            attempts = sum(task.attempt_count for task in tasks)
            performance.append(
                AgentPerformance(
                    agent_id=agent_id,
                    tasks=len(tasks),
                    completed=completed,
                    failed=failed,
                    retries=retries,
                    success_rate=_percentage(completed, len(tasks)),
                    average_attempts=(
                        round(attempts / len(tasks), 2)
                        if tasks
                        else 0.0
                    ),
                )
            )
        return performance

    @staticmethod
    def _aggregate_agents(
        evaluations: Iterable[RunEvaluation],
    ) -> list[AgentPerformance]:
        totals: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "tasks": 0,
                "completed": 0,
                "failed": 0,
                "retries": 0,
                "attempts": 0.0,
            }
        )
        for evaluation in evaluations:
            for agent in evaluation.agent_performance:
                total = totals[agent.agent_id]
                total["tasks"] += agent.tasks
                total["completed"] += agent.completed
                total["failed"] += agent.failed
                total["retries"] += agent.retries
                total["attempts"] += agent.average_attempts * agent.tasks

        result: list[AgentPerformance] = []
        for agent_id, total in sorted(totals.items()):
            tasks = int(total["tasks"])
            result.append(
                AgentPerformance(
                    agent_id=agent_id,
                    tasks=tasks,
                    completed=int(total["completed"]),
                    failed=int(total["failed"]),
                    retries=int(total["retries"]),
                    success_rate=_percentage(
                        int(total["completed"]),
                        tasks,
                    ),
                    average_attempts=(
                        round(total["attempts"] / tasks, 2)
                        if tasks
                        else 0.0
                    ),
                )
            )
        return result
