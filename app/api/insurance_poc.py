"""Product endpoints for the frozen insurance POC Evidence Package."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.insurance_poc import (
    EvidenceExtractionError,
    EvidencePreviewRequest,
    EvidencePreviewResponse,
    ExplainableInsuranceRouter,
    FixtureResponse,
    InsurancePOCEvidenceService,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
)


router = APIRouter(prefix="/api/insurance-poc", tags=["insurance-poc"])
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "examples" / "insurance-poc"


def _service(request: Request) -> InsurancePOCEvidenceService:
    existing = getattr(request.app.state, "insurance_poc_evidence_service", None)
    if isinstance(existing, InsurancePOCEvidenceService):
        return existing
    created = InsurancePOCEvidenceService(FIXTURE_DIR)
    request.app.state.insurance_poc_evidence_service = created
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
