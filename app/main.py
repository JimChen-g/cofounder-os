"""FastAPI application entry point."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.api.evaluation import router as evaluation_router
from app.api.insurance_poc import router as insurance_poc_router
from app.api.product import router as product_router
from app.api.routes import router as api_router
from app.config import get_settings
from app.models import Provider
from app.providers.openai_compat import OpenAICompatProvider
from app.providers.registry import get_registry
from app.ui import router as ui_router
from app.ui.routes import STATIC_ROOT

# Configure logging
logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: register providers. Shutdown: nothing special."""
    settings = get_settings()
    registry = get_registry()

    # Register Qwen if API key is configured
    if settings.qwen_api_key:
        qwen = OpenAICompatProvider(
            name=Provider.QWEN,
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model=settings.qwen_model,
        )
        registry.register(qwen)
        logger.info("Registered provider: %s", qwen.name)

    # Register Step if API key is configured
    if settings.step_api_key:
        step = OpenAICompatProvider(
            name=Provider.STEP,
            api_key=settings.step_api_key,
            base_url=settings.step_base_url,
            model=settings.step_model,
        )
        registry.register(step)
        logger.info("Registered provider: %s", step.name)

    logger.info("Gateway v%s ready", settings.app_version)

    yield

    logger.info("Gateway shutting down")


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="Unified API gateway for Co-founder OS AI providers",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — permissive for development; tighten in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id_and_logging(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach a request ID and log every request."""
    request_id = f"req-{uuid.uuid4().hex[:16]}"
    request.state.request_id = request_id

    start = time.perf_counter()
    response = await call_next(request)
    elapsed = (time.perf_counter() - start) * 1000

    logger.info(
        "%s %s %s %d %.1fms",
        request.method,
        request.url.path,
        request_id,
        response.status_code,
        elapsed,
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def global_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Catch-all for unhandled exceptions."""
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "The server could not complete the request.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


# Include API routes at root — no /api prefix
app.include_router(api_router)
app.include_router(product_router)
app.include_router(evaluation_router)
app.include_router(insurance_poc_router)
app.include_router(ui_router)
app.mount(
    "/ui/assets",
    StaticFiles(directory=STATIC_ROOT),
    name="mission-control-assets",
)


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
        "mission_control": "/ui",
    }
