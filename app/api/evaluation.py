"""Read-only D13 Evaluation API routes."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from app.api.product_models import ProductErrorResponse
from app.config import get_settings
from app.evaluation import EvaluationService, EvaluationSummary, RunEvaluation
from app.services.product_api import ProductAPIService, build_product_api_service
from app.state import RecordNotFound


logger = logging.getLogger("evaluation-api")
router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _service(request: Request) -> EvaluationService:
    existing = getattr(request.app.state, "evaluation_service", None)
    if isinstance(existing, EvaluationService):
        return existing

    product = getattr(request.app.state, "product_api_service", None)
    if not isinstance(product, ProductAPIService):
        product = build_product_api_service(get_settings())
        request.app.state.product_api_service = product

    created = EvaluationService(
        product.orchestration,
        product.artifact_store,
    )
    request.app.state.evaluation_service = created
    return created


def _error_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, RecordNotFound):
        status_code = 404
        error = "not_found"
        detail = "The requested Run was not found."
    else:
        status_code = 500
        error = "evaluation_error"
        detail = "The persisted evaluation evidence could not be summarized."

    logger.warning(
        "Evaluation API request failed: request_id=%s error_type=%s",
        _request_id(request),
        type(exc).__name__,
        exc_info=exc,
    )
    payload = ProductErrorResponse(
        error=error,
        detail=detail,
        request_id=_request_id(request),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


@router.get(
    "/summary",
    response_model=EvaluationSummary,
    responses={500: {"model": ProductErrorResponse}},
    summary="Summarize deterministic Run evaluations",
)
async def evaluation_summary(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> EvaluationSummary | JSONResponse:
    """Return a bounded newest-first cross-Run dashboard snapshot."""

    try:
        return _service(request).summary(limit=limit)
    except Exception as exc:
        return _error_response(request, exc)


@router.get(
    "/runs/{run_id}",
    response_model=RunEvaluation,
    responses={
        404: {"model": ProductErrorResponse},
        500: {"model": ProductErrorResponse},
    },
    summary="Evaluate one persisted Run",
)
async def evaluate_run(
    request: Request,
    run_id: UUID,
) -> RunEvaluation | JSONResponse:
    """Return deterministic score dimensions and bounded evidence."""

    try:
        return _service(request).evaluate_run(run_id)
    except Exception as exc:
        return _error_response(request, exc)
