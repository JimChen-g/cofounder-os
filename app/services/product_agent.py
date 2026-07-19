"""Application service for Product Agent execution (D06-C).

This service coordinates Product Agent execution, result validation,
routing evidence recording, and artifact creation/registration.

It does not claim Tasks, mutate Task lifecycle state, or perform
automatic Task retry — those responsibilities belong to D06-D.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional
from uuid import UUID

from app.agents.product_agent import (
    PRODUCT_AGENT_ID,
    ProductAgent,
    ProductAgentValidationFailure,
    ProductGatewayProtocol,
)
from app.artifacts import FileArtifactStore, StoredArtifact
from app.artifacts.store import _canonical_json
from app.domain import ProductAgentRequest, ProductAgentResultV1, ProductTaskContext
from app.services import (
    ArtifactRegistrationService,
    OrchestrationService,
)
from app.clients.gateway import GatewayClient, GatewayCompletion


def _compute_idempotency_key(
    schema_version: str,
    run_id: UUID,
    task_id: UUID,
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
        str(task_id),
        relation,
        logical_name,
        filename,
        checksum,
    ]
    identity = ":".join(parts)
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class ProductAgentServiceError(RuntimeError):
    """Base error for Product Agent service operations."""


class ProductAgentContextError(ProductAgentServiceError):
    """Raised when the Product Agent request context is invalid."""


class ProductAgentRouteEvidenceError(ProductAgentServiceError):
    """Raised when route evidence cannot be recorded."""


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
    - Validates task_id is present (required for Task-output artifacts)
    - Calls ProductAgent with structured context
    - Validates the result against ProductAgentResultV1
    - Records Gateway routing evidence from actual completion fields
    - Renders JSON (canonical) and Markdown artifacts
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
        """Execute the Product Agent and produce registered Task-output artifacts.

        Preconditions:
          - request.context.task_id must be set (required for output relation)

        Returns:
            (result, completion, json_artifact, md_artifact, json_domain, md_domain)
        """
        context = request.context

        # Validate task_id before any Gateway call or artifact creation
        if context.task_id is None:
            raise ProductAgentContextError(
                "ProductAgentService requires context.task_id for Task-output artifacts"
            )

        # Execute Product Agent and produce artifacts
        try:
            result, completion = await self.agent.execute(request)

            # Record route decision from actual Gateway completion fields
            self._record_route_decision(
                context=context,
                completion=completion,
                correlation_id=correlation_id,
            )

            # Render Markdown
            md_content = _render_markdown(result)

            # Write and register JSON artifact via canonical JSON path
            json_stored, json_domain, json_event = (
                self._write_json_artifact(
                    run_id=context.run_id,
                    task_id=context.task_id,
                    logical_name="product-brief",
                    filename="product-brief.json",
                    value=result.model_dump(mode="json", exclude_none=True),
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
        except ProductAgentValidationFailure as exc:
            raise ProductAgentExecutionError(
                f"Product Agent execution failed: {exc}"
            ) from exc
        except ProductAgentRouteEvidenceError as exc:
            raise ProductAgentExecutionError(
                f"Product Agent route evidence failed: {exc}"
            ) from exc
        except Exception as exc:
            raise ProductAgentExecutionError(
                f"Product Agent execution failed: {exc}"
            ) from exc

        return (
            result,
            completion,
            json_stored,
            md_stored,
            json_domain,
            md_domain,
        )

    def _write_json_artifact(
        self,
        run_id: UUID,
        task_id: UUID,
        logical_name: str,
        filename: str,
        value: Any,
        content_type: str,
        created_by: str,
        correlation_id: Optional[str] = None,
    ) -> tuple[StoredArtifact, Any, Any]:
        """Write canonical JSON artifact and register in orchestration."""
        # Produce the exact canonical bytes that FileArtifactStore.write_bytes
        # will checksum.  Use the same _canonical_json function the store uses.
        canonical_str = _canonical_json(value)
        canonical_bytes = canonical_str.encode("utf-8")
        content_checksum = hashlib.sha256(canonical_bytes).hexdigest()

        idempotency_key = _compute_idempotency_key(
            schema_version="1.0",
            run_id=run_id,
            task_id=task_id,
            relation="output",
            logical_name=logical_name,
            filename=filename,
            checksum=content_checksum,
        )

        # Write directly via the store's write_bytes to avoid a second
        # canonical-serialization pass through write_json.
        stored = self.artifact_store.write_bytes(
            run_id=run_id,
            logical_name=logical_name,
            filename=filename,
            content=canonical_bytes,
            created_by=created_by,
            task_id=task_id,
            content_type=content_type,
            idempotency_key=idempotency_key,
        )

        # Register in orchestration
        domain_artifact, event = self.orchestration.register_artifact(
            run_id=run_id,
            kind="data",
            name=logical_name,
            uri=stored.uri,
            created_by=created_by,
            actor=created_by,
            relation="output",
            task_id=task_id,
            content_type=content_type,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            correlation_id=correlation_id,
            metadata={
                "filename": stored.filename,
                "logical_name": stored.logical_name,
                "format_version": stored.format_version,
                "idempotency_key": stored.idempotency_key,
                "provenance": stored.provenance,
                "relation": "output",
            },
        )

        return stored, domain_artifact, event

    def _write_and_register(
        self,
        run_id: UUID,
        task_id: UUID,
        logical_name: str,
        filename: str,
        content: bytes,
        content_type: str,
        created_by: str,
        correlation_id: Optional[str] = None,
    ) -> tuple[StoredArtifact, Any, Any]:
        """Write artifact content and register in orchestration."""

        writer = ArtifactRegistrationService(self.artifact_store, self.orchestration)

        # Content checksum from the exact bytes that will be stored
        content_checksum = hashlib.sha256(content).hexdigest()

        idempotency_key = _compute_idempotency_key(
            schema_version="1.0",
            run_id=run_id,
            task_id=task_id,
            relation="output",
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
            relation="output",
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )

        return stored, domain, event

    def _record_route_decision(
        self,
        context: ProductTaskContext,
        completion: GatewayCompletion,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Record Gateway routing evidence from actual completion fields.

        Uses only real GatewayCompletion data.  Missing required fields
        cause a controlled ProductAgentRouteEvidenceError.  No synthetic
        values are fabricated.
        """
        # Validate required fields from actual completion
        if completion.selected_model is None:
            raise ProductAgentRouteEvidenceError(
                "GatewayCompletion.selected_model is required for route evidence"
            )
        if completion.selected_provider is None:
            raise ProductAgentRouteEvidenceError(
                "GatewayCompletion.selected_provider is required for route evidence"
            )
        if completion.routing_reason is None:
            raise ProductAgentRouteEvidenceError(
                "GatewayCompletion.routing_reason is required for route evidence"
            )

        # Use latency from raw_metadata only when present and valid
        latency_ms = None
        raw_meta = completion.raw_metadata or {}
        raw_latency = raw_meta.get("latency_ms")
        if raw_latency is not None:
            try:
                latency_ms = float(raw_latency)
            except (TypeError, ValueError):
                latency_ms = None

        self.orchestration.record_route_decision(
            run_id=context.run_id,
            task_id=context.task_id,
            requested_model=completion.requested_model,
            selected_model=completion.selected_model,
            provider=completion.selected_provider,
            reason=completion.routing_reason,
            fallback_used=completion.fallback_used,
            latency_ms=latency_ms,
            correlation_id=correlation_id,
            metadata={
                "agent_id": PRODUCT_AGENT_ID,
                "schema_version": "1.0",
                "product_agent_version": "1.0",
            },
        )
