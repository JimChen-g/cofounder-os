from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfWriter

from app.api.insurance_poc import router
from app.insurance_poc import (
    AttachmentUpload,
    EvidenceExtractionError,
    EvidencePreviewRequest,
    InsurancePOCEvidenceService,
)
from app.main import RequestBodyLimitMiddleware


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"


@pytest.fixture
def evidence_service() -> InsurancePOCEvidenceService:
    return InsurancePOCEvidenceService(FIXTURE_DIR)


def _request(service: InsurancePOCEvidenceService) -> EvidencePreviewRequest:
    fixture = service.fixture()
    return EvidencePreviewRequest(
        mission=fixture.mission,
        attachments=fixture.attachments,
    )


def test_extracts_source_linked_multimodal_evidence(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    package = evidence_service.extract(_request(evidence_service))

    assert package.schema_version == "insurance-evidence-1.0"
    assert package.synthetic is True
    assert package.authoritative is False
    assert len(package.sources) == 5
    assert len(package.evidence) == 10
    assert {item.category for item in package.evidence} == {
        "business",
        "accident",
        "technical",
        "financial",
        "compliance/constraint",
    }
    assert {source.modality for source in package.sources} == {
        "text",
        "document",
        "image",
        "structured_data",
    }
    image_evidence = [item for item in package.evidence if item.modality == "image"]
    assert len(image_evidence) == 4
    assert all(
        item.adapter == "sha256_bound_synthetic_fixture_adapter"
        and item.adapter_mode == "deterministic_fixture"
        and item.source_checksum_sha256
        for item in image_evidence
    )
    assert any(
        item.source_file == "insurance-poc-requirements.pdf"
        and "product-agent" in item.used_by_agents
        for item in package.evidence
    )
    assert any(
        item.modality == "image" and "risk-agent" in item.used_by_agents
        for item in package.evidence
    )


def test_unknown_image_fails_explicitly_and_recoverably(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    request = _request(evidence_service)
    changed = request.attachments[1].model_copy(deep=True)
    payload = bytearray(base64.b64decode(changed.base64_content))
    payload[-1] ^= 1
    changed.base64_content = base64.b64encode(payload).decode("ascii")
    request.attachments[1] = changed

    with pytest.raises(EvidenceExtractionError) as raised:
        evidence_service.extract(request)

    assert raised.value.code == "unsupported_image_fixture"
    assert raised.value.recoverable is True
    assert "formal image Adapter" in raised.value.detail


def test_duplicate_attachment_names_and_content_fail_recoverably(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    request = _request(evidence_service)
    renamed_duplicate = request.attachments[1].model_copy(
        update={"filename": "renamed-duplicate.png"}
    )
    request.attachments = [
        request.attachments[0],
        request.attachments[1],
        renamed_duplicate,
    ]
    with pytest.raises(EvidenceExtractionError) as raised:
        evidence_service.extract(request)
    assert raised.value.code == "duplicate_attachment_content"

    request = _request(evidence_service)
    duplicate_name = request.attachments[2].model_copy(
        update={"filename": request.attachments[1].filename.upper()}
    )
    request.attachments = [
        request.attachments[0],
        request.attachments[1],
        duplicate_name,
    ]
    with pytest.raises(EvidenceExtractionError) as raised:
        evidence_service.extract(request)
    assert raised.value.code == "duplicate_attachment_filename"


def test_corrupt_pdf_never_silently_becomes_empty_evidence(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    request = _request(evidence_service)
    corrupt = request.attachments[0].model_copy(deep=True)
    corrupt.base64_content = base64.b64encode(b"%PDF-not-a-document").decode("ascii")
    request.attachments[0] = corrupt

    with pytest.raises(EvidenceExtractionError) as raised:
        evidence_service.extract(request)

    assert raised.value.code == "pdf_parse_failed"
    assert "Replace it and retry" in raised.value.detail


def test_contract_accepts_one_pdf_and_one_image(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    request = _request(evidence_service)
    request.attachments = request.attachments[:2]

    package = evidence_service.extract(request)

    assert len(package.sources) == 4
    assert any(item.modality == "image" for item in package.evidence)


def test_upload_and_pdf_resource_limits_fail_recoverably(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    request = _request(evidence_service)
    oversized = request.attachments[1].model_copy(deep=True)
    oversized.base64_content = base64.b64encode(
        b"\x89PNG\r\n\x1a\n" + b"x" * (4 * 1024 * 1024)
    ).decode("ascii")
    request.attachments = [request.attachments[0], oversized]
    with pytest.raises(EvidenceExtractionError) as raised:
        evidence_service.extract(request)
    assert raised.value.code == "attachment_too_large"

    writer = PdfWriter()
    for _ in range(21):
        writer.add_blank_page(width=100, height=100)
    buffer = io.BytesIO()
    writer.write(buffer)
    request = _request(evidence_service)
    pdf = request.attachments[0].model_copy(deep=True)
    pdf.base64_content = base64.b64encode(buffer.getvalue()).decode("ascii")
    request.attachments = [pdf, request.attachments[1]]
    with pytest.raises(EvidenceExtractionError) as raised:
        evidence_service.extract(request)
    assert raised.value.code == "pdf_page_limit_exceeded"


def test_attachment_contract_rejects_paths_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        AttachmentUpload(
            filename="../claim.pdf",
            content_type="application/pdf",
            base64_content="YWJjZA==",
        )
    with pytest.raises(ValidationError):
        EvidencePreviewRequest.model_validate(
            {
                "mission": "Valid",
                "attachments": [],
                "unexpected": True,
            }
        )


def test_insurance_poc_evidence_api_exposes_fixture_and_bounded_error(
    evidence_service: InsurancePOCEvidenceService,
) -> None:
    app = FastAPI()
    app.state.insurance_poc_evidence_service = evidence_service
    app.include_router(router)
    with TestClient(app) as client:
        fixture_response = client.get("/api/insurance-poc/fixture")
        assert fixture_response.status_code == 200
        fixture = fixture_response.json()
        assert len(fixture["attachments"]) == 3

        preview = client.post(
            "/api/insurance-poc/evidence",
            json={
                "mission": fixture["mission"],
                "attachments": fixture["attachments"],
            },
        )
        assert preview.status_code == 200
        evidence = preview.json()["evidence_package"]["evidence"]
        assert len(evidence) == 10

        routing = client.post(
            "/api/insurance-poc/routing",
            json={
                "evidence_package": preview.json()["evidence_package"],
                "unavailable_models": ["product-agent-local"],
            },
        )
        assert routing.status_code == 200
        assert len(routing.json()["decisions"]) == 10
        assert routing.json()["live_model_calls"] == 0
        assert any(decision["fallback_used"] for decision in routing.json()["decisions"])

        invalid = fixture["attachments"]
        invalid[1]["base64_content"] = base64.b64encode(b"\x89PNG\r\n\x1a\nunknown").decode("ascii")
        failed = client.post(
            "/api/insurance-poc/evidence",
            json={"mission": fixture["mission"], "attachments": invalid},
        )
        assert failed.status_code == 422
        assert failed.json()["error"] == "evidence_extraction_failed"
        assert failed.json()["code"] == "unsupported_image_fixture"
        assert failed.json()["recoverable"] is True


@pytest.mark.asyncio
async def test_request_body_limit_rejects_chunked_d14_payload() -> None:
    downstream_called = False

    async def downstream(scope, receive, send) -> None:
        nonlocal downstream_called
        downstream_called = True

    messages = iter(
        [
            {"type": "http.request", "body": b"1234", "more_body": True},
            {"type": "http.request", "body": b"5678", "more_body": False},
        ]
    )
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return next(messages)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=6)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/insurance-poc/evidence",
            "headers": [],
            "query_string": b"",
            "http_version": "1.1",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        },
        receive,
        send,
    )

    assert downstream_called is False
    assert sent[0]["status"] == 413
