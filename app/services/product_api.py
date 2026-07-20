"""Application facade for the D11 Product API.

HTTP routes call this service instead of mutating durable models. The facade
composes the accepted Executive Orchestrator, Orchestration Service, Artifact
Store, Policy Gate, and Workflow Controller authorities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from app.artifacts import FileArtifactStore
from app.clients import GatewayClient
from app.config import Settings
from app.domain import ApprovalStatus, Artifact, AuditEvent
from app.orchestrators import (
    ExecutiveOrchestrator,
    ExecutivePlanningResult,
    MaterializedExecution,
    PlanValidationError,
)
from app.policy import DeterministicPolicyGate
from app.services.execution import AgentExecutionService
from app.services.finance_agent import FinanceAgentService
from app.services.orchestration import (
    ApprovalWorkflowResult,
    OrchestrationService,
    RunSnapshot,
)
from app.services.product_agent import ProductAgentService
from app.services.workflow_controller import WorkflowController, WorkflowRunResult
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer


PRODUCT_API_ACTOR = "product-api"
_MVP_AGENT_IDS = (
    "product-agent",
    "finance-agent",
    "executive-orchestrator",
)


class ProductAPIServiceError(RuntimeError):
    """Base error for Product API application operations."""


class ProductAPIApprovalError(ProductAPIServiceError):
    """Raised when an approval decision violates persisted policy evidence."""


class ProductAPIArtifactError(ProductAPIServiceError):
    """Raised when an artifact cannot be safely exposed."""


@dataclass(frozen=True)
class ProductRunCreation:
    """Result of materializing and driving a new workflow."""

    materialized: MaterializedExecution
    workflow: WorkflowRunResult


@dataclass(frozen=True)
class ProductApprovalResolution:
    """Result of resolving an approval and resuming the workflow."""

    resolution: ApprovalWorkflowResult
    workflow: WorkflowRunResult


@dataclass(frozen=True)
class ProductArtifactContent:
    """Artifact metadata plus optional verified text content."""

    artifact: Artifact
    content: str | None
    content_available: bool
    content_omitted_reason: str | None = None


class ProductAPIService:
    """Stable Product API boundary over existing workflow authorities."""

    def __init__(
        self,
        *,
        executive: ExecutiveOrchestrator,
        orchestration: OrchestrationService,
        workflow_controller: WorkflowController,
        artifact_store: FileArtifactStore,
        max_artifact_bytes: int = 1_048_576,
    ) -> None:
        if executive.service is not orchestration:
            raise ValueError(
                "ExecutiveOrchestrator and ProductAPIService must share orchestration"
            )
        if workflow_controller.orchestration is not orchestration:
            raise ValueError(
                "WorkflowController and ProductAPIService must share orchestration"
            )
        if workflow_controller.artifact_store is not artifact_store:
            raise ValueError(
                "WorkflowController and ProductAPIService must share artifact store"
            )
        if max_artifact_bytes < 1:
            raise ValueError("max_artifact_bytes must be positive")

        self.executive = executive
        self.orchestration = orchestration
        self.workflow_controller = workflow_controller
        self.artifact_store = artifact_store
        self.max_artifact_bytes = max_artifact_bytes

    async def create_run(
        self,
        *,
        objective: str,
        context: str | None,
        owner: str | None,
        correlation_id: str | None,
        max_cycles: int,
    ) -> ProductRunCreation:
        """Plan, persist, and drive one bounded MVP workflow."""

        planning_context = self._planning_context(context)
        planning = await self.executive.plan(
            objective,
            context=planning_context,
        )
        self._validate_mvp_plan(planning)
        materialized = self.executive.materialize(
            planning,
            owner=owner,
            founder_context=context,
            actor=PRODUCT_API_ACTOR,
            correlation_id=correlation_id,
        )
        workflow = await self.workflow_controller.run_until_terminal(
            materialized.run.id,
            correlation_id=correlation_id,
            max_cycles=max_cycles,
        )
        return ProductRunCreation(
            materialized=materialized,
            workflow=workflow,
        )

    def get_run(
        self,
        run_id: UUID | str,
        *,
        event_limit: int | None = None,
    ) -> RunSnapshot:
        """Return one consistent persisted Run snapshot."""

        return self.orchestration.get_snapshot(
            run_id,
            event_limit=event_limit,
        )

    def list_events(
        self,
        run_id: UUID | str,
        *,
        limit: int,
    ) -> list[AuditEvent]:
        """Return a bounded tail of persisted events after proving Run exists."""

        self.orchestration.repository.get_run(run_id)
        return self.orchestration.repository.list_events(
            run_id,
            limit=limit,
        )

    def list_artifacts(
        self,
        run_id: UUID | str,
        *,
        include_content: bool,
    ) -> list[ProductArtifactContent]:
        """Return registered artifacts and optional verified UTF-8 content."""

        self.orchestration.repository.get_run(run_id)
        artifacts = self.orchestration.repository.list_artifacts(run_id)
        return [
            self._artifact_resource(
                artifact,
                include_content=include_content,
            )
            for artifact in artifacts
        ]

    async def retry_run(
        self,
        run_id: UUID | str,
        *,
        correlation_id: str | None,
        max_cycles: int,
    ) -> WorkflowRunResult:
        """Drive recovery, bounded retry, or completed-run replay."""

        return await self.workflow_controller.run_until_terminal(
            run_id,
            correlation_id=correlation_id,
            max_cycles=max_cycles,
        )

    async def resolve_approval(
        self,
        run_id: UUID | str,
        approval_id: UUID | str,
        *,
        decision: Literal["approved", "rejected"],
        decided_by: str,
        reason: str,
        correlation_id: str | None,
        max_cycles: int,
    ) -> ProductApprovalResolution:
        """Validate reviewer evidence, resolve once, and resume the controller."""

        approval = self.orchestration.repository.get_approval(
            run_id,
            approval_id,
        )
        if ApprovalStatus(approval.status) != ApprovalStatus.PENDING:
            raise ProductAPIApprovalError(
                "Approval has already been resolved."
            )
        if (
            approval.expires_at is not None
            and approval.expires_at <= datetime.now(timezone.utc)
        ):
            raise ProductAPIApprovalError("Approval has expired.")

        reviewer_required = approval.metadata.get("reviewer_required")
        if (
            decision == "approved"
            and isinstance(reviewer_required, str)
            and reviewer_required
            and decided_by != reviewer_required
        ):
            raise ProductAPIApprovalError(
                "The approval requires a different reviewer."
            )

        resolution = self.orchestration.resolve_approval(
            run_id,
            approval_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            actor=PRODUCT_API_ACTOR,
            correlation_id=correlation_id,
        )
        workflow = await self.workflow_controller.run_until_terminal(
            run_id,
            correlation_id=correlation_id,
            max_cycles=max_cycles,
        )
        return ProductApprovalResolution(
            resolution=resolution,
            workflow=workflow,
        )

    @staticmethod
    def _planning_context(context: str | None) -> str:
        contract = (
            "D11 runnable MVP contract: create exactly three tasks: one "
            "product-agent task and one finance-agent task with no "
            "dependencies, followed by one executive-orchestrator synthesis "
            "task that directly depends on both. Do not assign any other "
            "agents. The workflow must produce the Product brief, Finance "
            "brief, and five decision artifacts."
        )
        if context and context.strip():
            return f"{context.strip()}\n\n{contract}"
        return contract

    @staticmethod
    def _validate_mvp_plan(planning: ExecutivePlanningResult) -> None:
        tasks = planning.plan.tasks
        assigned = [task.assigned_agent for task in tasks]
        if len(tasks) != 3 or sorted(assigned) != sorted(_MVP_AGENT_IDS):
            raise PlanValidationError(
                "D11 Product API requires exactly one Product, one Finance, "
                "and one Executive synthesis task."
            )

        by_agent = {task.assigned_agent: task for task in tasks}
        product = by_agent["product-agent"]
        finance = by_agent["finance-agent"]
        synthesis = by_agent["executive-orchestrator"]
        if product.dependency_keys or finance.dependency_keys:
            raise PlanValidationError(
                "Product and Finance tasks must be independent root tasks."
            )
        if set(synthesis.dependency_keys) != {product.key, finance.key}:
            raise PlanValidationError(
                "Executive synthesis must depend directly on Product and Finance."
            )

    def _artifact_resource(
        self,
        artifact: Artifact,
        *,
        include_content: bool,
    ) -> ProductArtifactContent:
        if not include_content:
            return ProductArtifactContent(
                artifact=artifact,
                content=None,
                content_available=False,
                content_omitted_reason="content_not_requested",
            )

        logical_name = artifact.metadata.get("logical_name")
        filename = artifact.metadata.get("filename")
        if not isinstance(logical_name, str) or not isinstance(filename, str):
            raise ProductAPIArtifactError(
                "Artifact addressing metadata is incomplete."
            )
        stored = self.artifact_store.verify(
            artifact.run_id,
            logical_name,
            filename,
            artifact.task_id,
        )
        if (
            not artifact.checksum_sha256
            or stored.checksum_sha256 != artifact.checksum_sha256
            or stored.uri != artifact.uri
            or (
                artifact.size_bytes is not None
                and stored.size_bytes != artifact.size_bytes
            )
        ):
            raise ProductAPIArtifactError(
                "Artifact content does not match its registered evidence."
            )
        if stored.size_bytes > self.max_artifact_bytes:
            return ProductArtifactContent(
                artifact=artifact,
                content=None,
                content_available=False,
                content_omitted_reason="content_too_large",
            )

        content_type = artifact.content_type or ""
        if not (
            content_type.startswith("text/")
            or content_type.startswith("application/json")
        ):
            return ProductArtifactContent(
                artifact=artifact,
                content=None,
                content_available=False,
                content_omitted_reason="unsupported_content_type",
            )

        try:
            content = self.artifact_store.read_text(
                artifact.run_id,
                logical_name,
                filename,
                artifact.task_id,
            )
        except UnicodeDecodeError as exc:
            raise ProductAPIArtifactError(
                "Artifact content is not valid UTF-8."
            ) from exc

        return ProductArtifactContent(
            artifact=artifact,
            content=content,
            content_available=True,
        )


def build_product_api_service(settings: Settings) -> ProductAPIService:
    """Compose the production Product API from frozen local authorities."""

    data_root = Path(settings.product_data_dir)
    repository = FileStateRepository(data_root / "runs")
    orchestration = OrchestrationService(repository)
    artifact_store = FileArtifactStore(data_root)
    gateway = GatewayClient.from_environment()
    product_agent = ProductAgentService(
        gateway,
        artifact_store,
        orchestration,
    )
    finance_agent = FinanceAgentService(
        gateway,
        artifact_store,
        orchestration,
    )
    workflow_controller = WorkflowController(
        orchestration=orchestration,
        agent_execution=AgentExecutionService(repository),
        artifact_store=artifact_store,
        product_agent_service=product_agent,
        finance_agent_service=finance_agent,
        artifact_synthesizer=ArtifactSynthesizer(
            artifact_store,
            orchestration,
        ),
        policy_gate=DeterministicPolicyGate(),
    )
    return ProductAPIService(
        executive=ExecutiveOrchestrator(gateway, orchestration),
        orchestration=orchestration,
        workflow_controller=workflow_controller,
        artifact_store=artifact_store,
        max_artifact_bytes=settings.product_max_artifact_bytes,
    )
