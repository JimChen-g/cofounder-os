"""Application service for Product Agent execution (D06-C).

This service coordinates Product Agent execution, result validation,
routing evidence recording, and artifact creation/registration.

It does not claim Tasks, mutate Task lifecycle state, or perform
automatic Task retry — those responsibilities belong to D06-D.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Optional
from uuid import UUID

from app.agents.product_agent import (
    PRODUCT_AGENT_ID,
    ProductAgent,
    ProductGatewayProtocol,
)
from app.artifacts import FileArtifactStore, StoredArtifact
from app.domain import ProductAgentRequest, ProductAgentResultV1, ProductTaskContext
from app.services import (
    ArtifactRegistrationService,
    OrchestrationService,
)
from app.clients.gateway import GatewayClient, GatewayCompletion


def _compute_idempotency_key(
    schema_version: str,
    run_id: UUID,
    task_id: Optional[UUID],
    relation: str,
    logical_name: str,
    filename: str,
    checksum: str,
) -> str:
    """Compute a deterministic scoped idempotency key.

    The key binds artifact identity to the Product Agent schema version,
    the run/task scope, the logical name and filename, and the content
    checksum.  Timestamps and correlation IDs are intentionally excluded
    so that identical retries produce the same key.
    """
    parts = [
        "product-agent",
        f"v{schema_version}",
        str(run_id),
        str(task_id) if task_id is not None else "run",
        relation,
        logical_name,
        filename,
        checksum,
    ]
    identity = ":".join(parts)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class ProductAgentServiceError(RuntimeError):
    """Base error for Product Agent service operations."""


class ProductAgentValidationFailure(ProductAgentServiceError):
    """Raised when Product Agent validation fails after repair."""


class ProductAgentExecutionError(ProductAgentServiceError):
    """Raised when Product Agent execution fails."""


def _render_markdown(result: ProductAgentResultV1) -> str:
    """Render a validated ProductAgentResultV1 to deterministic Markdown."""
    lines = [
        f"# Product Brief: {result.schema_version}",
        "",
        "## Problem Statement",
        result.problem_statement,
        "",
        "## Target Users",
    ]

    for user in result.target_users:
        lines.extend([
            f"### {user.segment} ({user.priority})",
            user.description,
            "",
        ])

    lines.extend([
        "## User Pains",
    ])
    for pain in result.user_pains:
        evidence = f" — {pain.evidence}" if pain.evidence else ""
        lines.append(f"- **{pain.severity.upper()}** ({pain.frequency}): {pain.pain}{evidence}")
    lines.append("")

    lines.extend([
        "## Assumptions",
    ])
    for assumption in result.assumptions:
        lines.append(f"- {assumption}")
    lines.append("")

    lines.extend([
        "## Product Scope",
        result.product_scope,
        "",
        "## Requirements",
    ])
    for req in result.requirements:
        lines.extend([
            f"- **[{req.priority.upper()}]** {req.requirement}",
            f"  - Rationale: {req.rationale}",
        ])
        if req.acceptance_criteria:
            lines.append(f"  - Acceptance: {req.acceptance_criteria}")
    lines.append("")

    lines.extend([
        "## Success Metrics",
    ])
    for metric in result.success_metrics:
        timeframe = f" ({metric.timeframe})" if metric.timeframe else ""
        lines.append(f"- **{metric.metric}**: {metric.target} — {metric.measurement}{timeframe}")
    lines.append("")

    lines.extend([
        "## Milestones",
    ])
    for milestone in result.milestones:
        date = f" (target: {milestone.target_date})" if milestone.target_date else ""
        lines.extend([
            f"### {milestone.name}{date}",
            milestone.description,
        ])
        if milestone.deliverables:
            lines.append("Deliverables:")
            for d in milestone.deliverables:
                lines.append(f"- {d}")
        lines.append("")

    lines.extend([
        "## Risks",
    ])
    for risk in result.risks:
        mitigation = f"\n  - Mitigation: {risk.mitigation}" if risk.mitigation else ""
        lines.append(f"- **{risk.probability.upper()}/{risk.impact.upper()}**: {risk.risk}{mitigation}")
    lines.append("")

    lines.extend([
        "## Open Questions",
    ])
    for question in result.open_questions:
        lines.append(f"- {question}")
    lines.append("")

    lines.extend([
        "## Recommended Actions",
    ])
    for action in result.recommended_actions:
        owner = f" (owner: {action.owner})" if action.owner else ""
        lines.extend([
            f"- **[{action.priority.upper()}]** {action.action}{owner}",
            f"  - Rationale: {action.rationale}",
        ])
    lines.append("")

    return "\n".join(lines)


class ProductAgentService:
    """Coordinate Product Agent execution and artifact production.

    This service:
    - Calls ProductAgent with structured context
    - Validates the result against ProductAgentResultV1
    - Records Gateway routing evidence
    - Renders JSON and Markdown artifacts
    - Writes and registers artifacts through ArtifactRegistrationService

    It does NOT:
    - Claim Tasks
    - Change Task lifecycle state
    - Perform automatic Task retry
    - Activate dependencies
    """

    def __init__(
        self,
        gateway_client: GatewayClient,
        artifact_store: FileArtifactStore,
        orchestration_service: OrchestrationService,
        protocol: Optional[ProductGatewayProtocol] = None,
    ) -> None:
        self.gateway = gateway_client
        self.artifact_store = artifact_store
        self.orchestration = orchestration_service
        self.agent = ProductAgent(gateway_client, protocol)
        self.protocol = protocol or ProductGatewayProtocol()

    async def execute(
        self,
        request: ProductAgentRequest,
        created_by: str = "product-agent",
        correlation_id: Optional[str] = None,
    ) -> tuple[
        ProductAgentResultV1,
        GatewayCompletion,
        Optional[StoredArtifact],
        Optional[StoredArtifact],
        Optional[Any],
        Optional[Any],
    ]:
        """Execute the Product Agent and produce registered artifacts.

        Returns:
            (result, completion, json_artifact, md_artifact, json_domain, md_domain)
        """
        context = request.context
        start_time = time.time()

        # Execute Product Agent
        try:
            result, completion = await self.agent.execute(request)
        except Exception as exc:
            raise ProductAgentExecutionError(
                f"Product Agent execution failed: {exc}"
            ) from exc

        latency_ms = (time.time() - start_time) * 1000.0

        # Record route decision
        self._record_route_decision(
            context=context,
            completion=completion,
            latency_ms=latency_ms,
            correlation_id=correlation_id,
        )

        # Render artifacts
        json_content = result.model_dump_json(indent=2, exclude_none=True) + "\n"
        md_content = _render_markdown(result)

        # Write and register JSON artifact
        json_stored, json_domain, json_event = (
            self._write_and_register(
                run_id=context.run_id,
                task_id=context.task_id,
                logical_name="product-brief",
                filename="product-brief.json",
                content=json_content.encode("utf-8"),
                content_type="application/json; charset=utf-8",
                created_by=created_by,
                correlation_id=correlation_id,
            )
        )

        # Write and register Markdown artifact
        md_stored, md_domain, md_event = (
            self._write_and_register(
                run_id=context.run_id,
                task_id=context.task_id,
                logical_name="product-brief-md",
                filename="product-brief.md",
                content=md_content.encode("utf-8"),
                content_type="text/markdown; charset=utf-8",
                created_by=created_by,
                correlation_id=correlation_id,
            )
        )

        return (
            result,
            completion,
            json_stored,
            md_stored,
            json_domain,
            md_domain,
        )

    def _write_and_register(
        self,
        run_id: UUID,
        task_id: Optional[UUID],
        logical_name: str,
        filename: str,
        content: bytes,
        content_type: str,
        created_by: str,
        correlation_id: Optional[str] = None,
    ) -> tuple[StoredArtifact, Any, Any]:
        """Write artifact content and register in orchestration."""

        writer = ArtifactRegistrationService(self.artifact_store, self.orchestration)

        # Content checksum (used for both idempotency key and integrity)
        content_checksum = hashlib.sha256(content).hexdigest()

        relation = "run"
        if task_id is not None:
            relation = "output"

        idempotency_key = _compute_idempotency_key(
            schema_version="1.0",
            run_id=run_id,
            task_id=task_id,
            relation=relation,
            logical_name=logical_name,
            filename=filename,
            checksum=content_checksum,
        )

        stored, domain, event = writer.write_text(
            run_id=run_id,
            logical_name=logical_name,
            filename=filename,
            text=content.decode("utf-8"),
            created_by=created_by,
            task_id=task_id,
            content_type=content_type,
            relation=relation,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

        return stored, domain, event

    def _record_route_decision(
        self,
        context: ProductTaskContext,
        completion: GatewayCompletion,
        latency_ms: float,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Record Gateway routing evidence."""
        try:
            self.orchestration.record_route_decision(
                run_id=context.run_id,
                task_id=context.task_id,
                requested_model=self.protocol.virtual_model,
                selected_model=completion.selected_model or self.protocol.virtual_model,
                provider=completion.selected_provider or "gateway",
                reason=completion.routing_reason or "Product Agent default routing",
                fallback_used=completion.fallback_used,
                latency_ms=latency_ms,
                correlation_id=correlation_id,
                metadata={
                    "agent_id": PRODUCT_AGENT_ID,
                    "schema_version": "1.0",
                    "product_agent_version": "1.0",
                },
            )
        except Exception:
            # Route recording failure must not prevent artifact creation
            pass
