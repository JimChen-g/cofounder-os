"""Product endpoints for the frozen insurance POC Evidence Package."""

from __future__ import annotations

from pathlib import Path
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.insurance_poc import (
    EvidenceExtractionError,
    DemoEvaluationResponse,
    EvidencePreviewRequest,
    EvidencePreviewResponse,
    ExplainableInsuranceRouter,
    FixtureResponse,
    GoldenWorkflowRequest,
    GoldenWorkflowResponse,
    InsurancePOCEvidenceService,
    InsurancePOCGoldenWorkflow,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
)
from app.artifacts import FileArtifactStore
from app.config import get_settings
from app.policy import DeterministicPolicyGate
from app.services.artifact_write import ArtifactRegistrationService
from app.services.product_api import ProductAPIService, build_product_api_service


router = APIRouter(prefix="/api/insurance-poc", tags=["insurance-poc"])
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "examples" / "insurance-poc"
EVALUATION_RESULTS = FIXTURE_DIR / "demo-evaluation-results.json"


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
        policy_gate=DeterministicPolicyGate(),
    )
    request.app.state.insurance_poc_workflow = created
    return created


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

    return ExplainableInsuranceRouter().route(body)


@router.post(
    "/runs",
    response_model=GoldenWorkflowResponse,
    status_code=201,
)
async def create_insurance_poc_run(
    request: Request,
    body: GoldenWorkflowRequest,
) -> GoldenWorkflowResponse | JSONResponse:
    """Execute the fixed, shared-evidence golden DAG to human approval."""

    try:
        evidence = _service(request).extract(body)
        return _workflow(request).execute(
            body,
            evidence,
            correlation_id=getattr(request.state, "request_id", None),
        )
    except EvidenceExtractionError as exc:
        return _error(request, exc)


@router.get("/evaluation", response_model=DemoEvaluationResponse)
async def get_insurance_poc_demo_evaluation() -> DemoEvaluationResponse:
    """Return the committed, reproducible small-sample comparison."""

    value = json.loads(EVALUATION_RESULTS.read_text(encoding="utf-8"))
    return DemoEvaluationResponse.model_validate(value)
