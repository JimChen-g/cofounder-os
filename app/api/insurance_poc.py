"""Product endpoints for the frozen insurance POC Evidence Package."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
from pathlib import Path
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.insurance_poc import (
    EvidenceExtractionError,
    DemoEvaluationResponse,
    EvidencePackage,
    EvidencePreviewRequest,
    EvidencePreviewResponse,
    ExplainableInsuranceRouter,
    FixtureResponse,
    GoldenWorkflowJobAccepted,
    GoldenWorkflowJobError,
    GoldenWorkflowJobStatus,
    GoldenWorkflowRequest,
    GoldenWorkflowResponse,
    InsurancePOCEvidenceService,
    InsurancePOCExecutionError,
    InsurancePOCGoldenWorkflow,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
)
from app.artifacts import FileArtifactStore
from app.config import get_settings
from app.services.artifact_write import ArtifactRegistrationService
from app.services.product_api import ProductAPIService, build_product_api_service
from app.providers.registry import get_registry
from app.insurance_poc.routing import QWEN, STEP


router = APIRouter(prefix="/api/insurance-poc", tags=["insurance-poc"])
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "insurance_poc" / "fixtures"
EVALUATION_RESULTS = FIXTURE_DIR / "demo-evaluation-results.json"
logger = logging.getLogger("gateway.insurance_poc")


@dataclass
class _WorkflowJob:
    job_id: UUID
    status: Literal["running", "completed", "failed"]
    capability: str
    result: GoldenWorkflowResponse | None = None
    error: GoldenWorkflowJobError | None = None
    task: asyncio.Task[None] | None = None


def _workflow_jobs(request: Request) -> dict[UUID, _WorkflowJob]:
    existing = getattr(request.app.state, "insurance_poc_workflow_jobs", None)
    if isinstance(existing, dict):
        return existing
    created: dict[UUID, _WorkflowJob] = {}
    request.app.state.insurance_poc_workflow_jobs = created
    return created


def _service(request: Request) -> InsurancePOCEvidenceService:
    existing = getattr(request.app.state, "insurance_poc_evidence_service", None)
    if isinstance(existing, InsurancePOCEvidenceService):
        return existing
    created = InsurancePOCEvidenceService(FIXTURE_DIR)
    request.app.state.insurance_poc_evidence_service = created
    return created


def _product_service(request: Request) -> ProductAPIService:
    existing = getattr(request.app.state, "product_api_service", None)
    if isinstance(existing, ProductAPIService):
        return existing
    created = build_product_api_service(get_settings())
    request.app.state.product_api_service = created
    return created


def _workflow(request: Request) -> InsurancePOCGoldenWorkflow:
    existing = getattr(request.app.state, "insurance_poc_workflow", None)
    if isinstance(existing, InsurancePOCGoldenWorkflow):
        return existing
    product = _product_service(request)
    artifact_store = product.artifact_store
    if not isinstance(artifact_store, FileArtifactStore):
        raise TypeError("Insurance POC requires the accepted FileArtifactStore")
    created = InsurancePOCGoldenWorkflow(
        fixture_dir=FIXTURE_DIR,
        orchestration=product.orchestration,
        artifacts=ArtifactRegistrationService(
            artifact_store,
            product.orchestration,
        ),
        workflow_controller=product.workflow_controller,
    )
    request.app.state.insurance_poc_workflow = created
    return created


async def _measured_provider_health() -> tuple[dict[str, bool], dict[str, float]]:
    """Return server-trusted health; callers cannot assert provider availability."""

    statuses = await get_registry().health_status()
    healthy = {QWEN: False, STEP: False}
    latencies: dict[str, float] = {}
    for status in statuses:
        model = str(status["provider"])
        healthy[model] = status["status"] == "healthy"
        latency = status.get("latency_ms")
        if latency is not None:
            latencies[model] = float(latency)
    return healthy, latencies


def _error(request: Request, exc: EvidenceExtractionError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": "evidence_extraction_failed",
            "code": exc.code,
            "detail": exc.detail,
            "recoverable": exc.recoverable,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def _workflow_error(request: Request, exc: InsurancePOCExecutionError) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": "insurance_workflow_unavailable",
            "code": exc.code,
            "detail": exc.detail,
            "recoverable": exc.recoverable,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


def _set_approval_cookie(
    response: Response,
    result: GoldenWorkflowResponse,
    capability: str,
) -> None:
    response.set_cookie(
        key=f"cofounder_approval_{result.run_id.hex}",
        value=capability,
        max_age=3600,
        httponly=True,
        samesite="strict",
        secure=get_settings().environment != "development",
        path="/api",
    )


async def _execute_workflow_job(
    job: _WorkflowJob,
    *,
    workflow: InsurancePOCGoldenWorkflow,
    body: GoldenWorkflowRequest,
    evidence: EvidencePackage,
    correlation_id: str | None,
    provider_health: dict[str, bool],
    provider_latency_ms: dict[str, float],
) -> None:
    try:
        job.result = await workflow.execute(
            body,
            evidence,
            correlation_id=correlation_id,
            provider_health=provider_health,
            provider_latency_ms=provider_latency_ms,
            approval_capability_sha256=hashlib.sha256(
                job.capability.encode("utf-8")
            ).hexdigest(),
        )
        job.status = "completed"
    except InsurancePOCExecutionError as exc:
        job.error = GoldenWorkflowJobError(
            code=exc.code,
            detail=exc.detail,
            recoverable=exc.recoverable,
        )
        job.status = "failed"
    except Exception:
        logger.exception("Asynchronous insurance POC workflow failed")
        job.error = GoldenWorkflowJobError(
            code="workflow_job_failed",
            detail="The governed workflow stopped unexpectedly.",
            recoverable=True,
        )
        job.status = "failed"


@router.get("/fixture", response_model=FixtureResponse)
async def get_insurance_poc_fixture(
    request: Request,
) -> FixtureResponse | JSONResponse:
    """Return the checksum-verified synthetic rehearsal files."""

    try:
        return _service(request).fixture()
    except EvidenceExtractionError as exc:
        return _error(request, exc)


@router.post("/evidence", response_model=EvidencePreviewResponse)
async def preview_insurance_poc_evidence(
    request: Request,
    body: EvidencePreviewRequest,
) -> EvidencePreviewResponse | JSONResponse:
    """Create a bounded Evidence Package without a model or state transition."""

    try:
        return EvidencePreviewResponse(
            evidence_package=_service(request).extract(body),
        )
    except EvidenceExtractionError as exc:
        return _error(request, exc)


@router.post("/routing", response_model=RoutingPreviewResponse)
async def preview_insurance_poc_routing(
    body: RoutingPreviewRequest,
) -> RoutingPreviewResponse:
    """Explain model/tool choices without claiming execution."""

    health, latency = await _measured_provider_health()
    trusted = body.model_copy(
        update={
            "provider_health": health,
            "provider_latency_ms": latency,
        }
    )
    return ExplainableInsuranceRouter().route(trusted)


@router.post(
    "/runs",
    response_model=GoldenWorkflowResponse,
    status_code=201,
)
async def create_insurance_poc_run(
    request: Request,
    response: Response,
    body: GoldenWorkflowRequest,
) -> GoldenWorkflowResponse | JSONResponse:
    """Execute the fixed, shared-evidence golden DAG to human approval."""

    try:
        evidence = _service(request).extract(body)
        provider_health, provider_latency = await _measured_provider_health()
        capability = secrets.token_urlsafe(32)
        result = await _workflow(request).execute(
            body,
            evidence,
            correlation_id=getattr(request.state, "request_id", None),
            provider_health=provider_health,
            provider_latency_ms=provider_latency,
            approval_capability_sha256=hashlib.sha256(
                capability.encode("utf-8")
            ).hexdigest(),
        )
        _set_approval_cookie(response, result, capability)
        return result
    except EvidenceExtractionError as exc:
        return _error(request, exc)
    except InsurancePOCExecutionError as exc:
        return _workflow_error(request, exc)


@router.post(
    "/run-jobs",
    response_model=GoldenWorkflowJobAccepted,
    status_code=202,
)
async def create_insurance_poc_run_job(
    request: Request,
    body: GoldenWorkflowRequest,
) -> GoldenWorkflowJobAccepted | JSONResponse:
    """Start the long-running live workflow without holding an edge request."""

    try:
        evidence = _service(request).extract(body)
        provider_health, provider_latency = await _measured_provider_health()
    except EvidenceExtractionError as exc:
        return _error(request, exc)

    job = _WorkflowJob(
        job_id=uuid4(),
        status="running",
        capability=secrets.token_urlsafe(32),
    )
    _workflow_jobs(request)[job.job_id] = job
    job.task = asyncio.create_task(
        _execute_workflow_job(
            job,
            workflow=_workflow(request),
            body=body,
            evidence=evidence,
            correlation_id=getattr(request.state, "request_id", None),
            provider_health=provider_health,
            provider_latency_ms=provider_latency,
        )
    )
    return GoldenWorkflowJobAccepted(job_id=job.job_id)


@router.get(
    "/run-jobs/{job_id}",
    response_model=GoldenWorkflowJobStatus,
)
async def get_insurance_poc_run_job(
    request: Request,
    response: Response,
    job_id: UUID,
) -> GoldenWorkflowJobStatus | JSONResponse:
    """Poll a live workflow and recover its approval cookie on completion."""

    job = _workflow_jobs(request).get(job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={
                "error": "workflow_job_not_found",
                "detail": "The workflow job does not exist in this runtime.",
                "recoverable": False,
                "request_id": getattr(request.state, "request_id", None),
            },
        )
    if job.status == "completed" and job.result is not None:
        _set_approval_cookie(response, job.result, job.capability)
    return GoldenWorkflowJobStatus(
        job_id=job.job_id,
        status=job.status,
        result=job.result,
        error=job.error,
    )


@router.get("/evaluation", response_model=DemoEvaluationResponse)
async def get_insurance_poc_demo_evaluation() -> DemoEvaluationResponse:
    """Return the committed, reproducible small-sample comparison."""

    value = json.loads(EVALUATION_RESULTS.read_text(encoding="utf-8"))
    return DemoEvaluationResponse.model_validate(value)
