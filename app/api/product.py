"""FastAPI routes for the D11 Product API."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.api.product_models import (
    ApprovalResponse,
    ArtifactListResponse,
    ArtifactResource,
    CreateRunRequest,
    CreateRunResponse,
    EventListResponse,
    ProductErrorResponse,
    ProductHealthResponse,
    ResolveApprovalRequest,
    RetryRunRequest,
)
from app.artifacts import ArtifactStoreError
from app.clients import GatewayClientError
from app.config import get_settings
from app.orchestrators import ExecutiveOrchestratorError
from app.services.orchestration import (
    ApprovalResolutionError,
    OrchestrationError,
    RunSnapshot,
)
from app.services.product_api import (
    ProductAPIApprovalError,
    ProductAPIArtifactError,
    ProductAPIService,
    ProductAPIServiceError,
    build_product_api_service,
)
from app.services.workflow_controller import WorkflowControllerError, WorkflowRunResult
from app.state import InvalidTransition, RecordNotFound


logger = logging.getLogger("product-api")
router = APIRouter(prefix="/api", tags=["product"])


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _service(request: Request) -> ProductAPIService:
    existing = getattr(request.app.state, "product_api_service", None)
    if isinstance(existing, ProductAPIService):
        return existing

    created = build_product_api_service(get_settings())
    request.app.state.product_api_service = created
    return created


def _error_response(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    request_id = _request_id(request)
    if isinstance(exc, RecordNotFound):
        status_code = 404
        error = "not_found"
        detail = "The requested Run, approval, or record was not found."
    elif isinstance(
        exc,
        (
            ApprovalResolutionError,
            InvalidTransition,
            ProductAPIApprovalError,
        ),
    ):
        status_code = 409
        error = "approval_conflict"
        detail = "The approval cannot be resolved in its current state."
    elif isinstance(
        exc,
        (
            ArtifactStoreError,
            ProductAPIArtifactError,
            WorkflowControllerError,
            OrchestrationError,
        ),
    ):
        status_code = 409
        error = "workflow_conflict"
        detail = "The workflow cannot continue with its current persisted state."
    elif isinstance(exc, (GatewayClientError, ExecutiveOrchestratorError)):
        status_code = 502
        error = "gateway_error"
        detail = "The Gateway did not return an accepted workflow result."
    elif isinstance(exc, ProductAPIServiceError):
        status_code = 409
        error = "product_api_conflict"
        detail = "The Product API operation conflicts with persisted state."
    else:
        status_code = 500
        error = "internal_error"
        detail = "The Product API operation failed."

    logger.warning(
        "Product API request failed: request_id=%s error_type=%s",
        request_id,
        type(exc).__name__,
        exc_info=exc,
    )
    payload = ProductErrorResponse(
        error=error,
        detail=detail,
        request_id=request_id,
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


@router.get(
    "/health",
    response_model=ProductHealthResponse,
    responses={503: {"model": ProductErrorResponse}},
    summary="Check Product API readiness",
)
async def product_health(
    request: Request,
) -> ProductHealthResponse | JSONResponse:
    """Report readiness without calling an upstream model."""

    try:
        _service(request)
        settings = get_settings()
        return ProductHealthResponse(
            status="healthy",
            version=settings.app_version,
            state_store="ready",
            artifact_store="ready",
            gateway_boundary="configured",
        )
    except Exception as exc:
        response = _error_response(request, exc)
        response.status_code = 503
        return response


@router.post(
    "/runs",
    response_model=CreateRunResponse,
    status_code=201,
    responses={
        409: {"model": ProductErrorResponse},
        502: {"model": ProductErrorResponse},
    },
    summary="Create and drive a Founder workflow",
)
async def create_run(
    request: Request,
    body: CreateRunRequest,
) -> CreateRunResponse | JSONResponse:
    """Plan once, persist through authorities, and run to a bounded stop."""

    try:
        result = await _service(request).create_run(
            objective=body.objective,
            context=body.context,
            owner=body.owner,
            correlation_id=_request_id(request),
            max_cycles=body.max_cycles,
        )
        materialized = result.materialized
        return CreateRunResponse(
            run_id=materialized.run.id,
            status=result.workflow.status,
            plan_message_id=materialized.plan_message_id,
            ready_task_ids=[
                UUID(task_id)
                for task_id in materialized.ready_task_ids
            ],
            approval_id=(
                materialized.approval.id
                if materialized.approval is not None
                else None
            ),
            workflow=result.workflow,
        )
    except Exception as exc:
        return _error_response(request, exc)


@router.get(
    "/runs/{run_id}",
    response_model=RunSnapshot,
    responses={404: {"model": ProductErrorResponse}},
    summary="Get a Run snapshot",
)
async def get_run(
    request: Request,
    run_id: UUID,
    event_limit: int = Query(default=200, ge=0, le=1000),
) -> RunSnapshot | JSONResponse:
    """Return a consistent Run, task, approval, routing, and artifact view."""

    try:
        return _service(request).get_run(
            run_id,
            event_limit=event_limit,
        )
    except Exception as exc:
        return _error_response(request, exc)


@router.get(
    "/runs/{run_id}/events",
    response_model=EventListResponse,
    responses={404: {"model": ProductErrorResponse}},
    summary="Get a Run audit trace",
)
async def get_run_events(
    request: Request,
    run_id: UUID,
    limit: int = Query(default=200, ge=0, le=1000),
) -> EventListResponse | JSONResponse:
    """Return a bounded tail of append-only workflow events."""

    try:
        events = _service(request).list_events(run_id, limit=limit)
        return EventListResponse(
            run_id=run_id,
            count=len(events),
            events=events,
        )
    except Exception as exc:
        return _error_response(request, exc)


@router.get(
    "/runs/{run_id}/artifacts",
    response_model=ArtifactListResponse,
    responses={
        404: {"model": ProductErrorResponse},
        409: {"model": ProductErrorResponse},
    },
    summary="Get registered Run artifacts",
)
async def get_run_artifacts(
    request: Request,
    run_id: UUID,
    include_content: bool = Query(default=True),
) -> ArtifactListResponse | JSONResponse:
    """Return artifact metadata and verified text content for the D12 viewer."""

    try:
        resources = _service(request).list_artifacts(
            run_id,
            include_content=include_content,
        )
        artifacts = [
            ArtifactResource(
                artifact=resource.artifact,
                content=resource.content,
                content_available=resource.content_available,
                content_omitted_reason=resource.content_omitted_reason,
            )
            for resource in resources
        ]
        return ArtifactListResponse(
            run_id=run_id,
            count=len(artifacts),
            artifacts=artifacts,
        )
    except Exception as exc:
        return _error_response(request, exc)


@router.post(
    "/runs/{run_id}/approvals/{approval_id}",
    response_model=ApprovalResponse,
    responses={
        404: {"model": ProductErrorResponse},
        409: {"model": ProductErrorResponse},
    },
    summary="Resolve an approval and resume",
)
async def resolve_run_approval(
    request: Request,
    run_id: UUID,
    approval_id: UUID,
    body: ResolveApprovalRequest,
) -> ApprovalResponse | JSONResponse:
    """Resolve exactly one approval and resume through the controller."""

    try:
        result = await _service(request).resolve_approval(
            run_id,
            approval_id,
            decision=body.decision,
            decided_by=body.decided_by,
            reason=body.reason,
            correlation_id=_request_id(request),
            max_cycles=body.max_cycles,
        )
        return ApprovalResponse(
            approval=result.resolution.approval,
            workflow=result.workflow,
        )
    except Exception as exc:
        return _error_response(request, exc)


@router.post(
    "/runs/{run_id}/retry",
    response_model=WorkflowRunResult,
    responses={
        404: {"model": ProductErrorResponse},
        409: {"model": ProductErrorResponse},
    },
    summary="Retry, recover, or replay a Run",
)
async def retry_run(
    request: Request,
    run_id: UUID,
    body: RetryRunRequest | None = None,
) -> WorkflowRunResult | JSONResponse:
    """Invoke the bounded D10 controller; never bypass retry limits."""

    try:
        return await _service(request).retry_run(
            run_id,
            correlation_id=_request_id(request),
            max_cycles=body.max_cycles if body is not None else 100,
        )
    except Exception as exc:
        return _error_response(request, exc)
