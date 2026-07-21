from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.insurance_poc import router
from app.insurance_poc import (
    AttachmentUpload,
    EvidenceExtractionError,
    EvidencePreviewRequest,
    InsurancePOCEvidenceService,
)


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
