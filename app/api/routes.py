"""API route definitions."""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.audit.logger import get_audit_logger
from app.config import get_settings
from app.models import (
    ChatRequest,
    ChatResponse,
    ErrorResponse,
    HealthResponse,
    ModelInfo,
    Provider,
    ProviderHealth,
)
from app.providers.base import ProviderError
from app.providers.registry import get_registry
from app.router.selector import route_chat

router = APIRouter()

logger = logging.getLogger("gateway")


def _check_auth(x_audit_token: Optional[str]) -> bool:
    """Verify the audit endpoint token; deny access when none is configured."""
    expected = get_settings().gateway_audit_token
    if not expected or not x_audit_token:
        return False
    return secrets.compare_digest(x_audit_token.encode(), expected.encode())


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health(request: Request) -> HealthResponse:
    """Return gateway health status and provider availability."""
    settings = get_settings()
    registry = get_registry()
    provider_healths = await registry.health_status()

    overall = "healthy"
    for ph in provider_healths:
        if ph["status"] == "unavailable":
            overall = "degraded"
            break

    return HealthResponse(
        status=overall,
        version=settings.app_version,
        providers=[
            ProviderHealth(
                provider=ph["provider"],
                status=ph["status"],
                latency_ms=ph.get("latency_ms"),
            )
            for ph in provider_healths
        ],
    )


@router.get(
    "/v1/models",
    response_model=list[ModelInfo],
    tags=["chat"],
    summary="List available models",
)
async def list_models(request: Request) -> list[ModelInfo]:
    """Return the list of virtual models available through this gateway.

    Only the three virtual model names are exposed; upstream model identifiers
    are never surfaced here.
    """
    return [
        ModelInfo(id="cofounder-auto", provider=Provider.QWEN, owned_by="cofounder-os"),
        ModelInfo(id="cofounder-qwen", provider=Provider.QWEN, owned_by="cofounder-os"),
        ModelInfo(id="cofounder-step", provider=Provider.STEP, owned_by="cofounder-os"),
    ]


@router.post(
    "/v1/chat/completions",
    response_model=ChatResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    tags=["chat"],
    summary="Create a chat completion",
)
async def chat_completions(
    request: Request, body: ChatRequest
) -> ChatResponse | JSONResponse:
    """Unified chat completion endpoint with automatic provider fallback."""
    try:
        response = await route_chat(
            body,
            user_agent=request.headers.get("user-agent"),
            request_id=getattr(request.state, "request_id", None),
        )
        return response
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(error="bad_request", detail=str(exc)).model_dump(),
        )
    except ProviderError as exc:
        return JSONResponse(
            status_code=502,
            content=ErrorResponse(
                error="upstream_error",
                detail=str(exc),
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )
    except Exception:
        logger.exception("Unhandled chat completion error")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="provider_error",
                detail="The gateway could not complete the request.",
                request_id=getattr(request.state, "request_id", None),
            ).model_dump(),
        )


@router.get("/audit/recent", tags=["system"], summary="Get recent audit records")
async def audit_recent(
    request: Request,
    x_audit_token: Optional[str] = Header(None, alias="X-Audit-Token"),
) -> JSONResponse:
    """Return recent audit log entries (requires GATEWAY_AUDIT_TOKEN)."""
    if not _check_auth(x_audit_token):
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "detail": "Invalid audit token"},
        )

    audit = get_audit_logger()
    records = audit.read_recent(max_records=200)
    return JSONResponse(content={"records": records, "count": len(records)})
