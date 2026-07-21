"""Bounded PDF/image extraction for the frozen insurance POC inputs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader

from app.insurance_poc.models import (
    AttachmentUpload,
    EvidenceCategory,
    EvidenceItem,
    EvidencePackage,
    EvidencePreviewRequest,
    EvidenceSource,
    FixtureResponse,
    PrivacyLevel,
    ProcessingStatus,
    SourceModality,
)


MAX_ATTACHMENT_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_PDF_TEXT_CHARS = 100_000
PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class EvidenceExtractionError(ValueError):
    """Explicit, recoverable failure at the Evidence boundary."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.recoverable = True


class InsurancePOCEvidenceService:
    """Extract the stable synthetic fixture without claiming live vision."""

    def __init__(self, fixture_dir: str | Path) -> None:
        self.fixture_dir = Path(fixture_dir).resolve()
        self.scenario = self._load_json("scenario.json")
        self.manifest = self._load_json("asset-manifest.json")
        self._asset_by_hash = {
            item["sha256"]: item
            for item in self.manifest.get("assets", [])
            if isinstance(item, dict) and isinstance(item.get("sha256"), str)
        }

    def fixture(self) -> FixtureResponse:
        """Return browser-ready stable inputs for one-click rehearsal."""

        manifest_entries = {
            item["filename"]: item for item in self.manifest["assets"] if isinstance(item, dict)
        }
        attachments: list[AttachmentUpload] = []
        fixture_inputs: tuple[tuple[str, Literal["application/pdf", "image/png"]], ...] = (
            ("insurance-poc-requirements.pdf", "application/pdf"),
            ("accident-intersection.png", "image/png"),
            ("accident-damage.png", "image/png"),
        )
        for filename, content_type in fixture_inputs:
            path = self.fixture_dir / filename
            payload = self._read_fixture_bytes(path)
            checksum = hashlib.sha256(payload).hexdigest()
            entry = manifest_entries.get(filename)
            if not isinstance(entry, dict) or checksum != entry.get("sha256"):
                raise EvidenceExtractionError(
                    "fixture_integrity_failed",
                    f"Stable fixture checksum failed for {filename}.",
                )
            attachments.append(
                AttachmentUpload(
                    filename=filename,
                    content_type=content_type,
                    base64_content=base64.b64encode(payload).decode("ascii"),
                    privacy_level=PrivacyLevel.RESTRICTED,
                )
            )
        return FixtureResponse(
            scenario_id=self.scenario["scenario_id"],
            mission=self.scenario["mission"],
            attachments=attachments,
        )

    def extract(self, request: EvidencePreviewRequest) -> EvidencePackage:
        """Validate, parse, and normalize one fixed multimodal evidence set."""

        decoded: list[tuple[AttachmentUpload, bytes, str]] = []
        total_bytes = 0
        seen_filenames: set[str] = set()
        seen_checksums: set[str] = set()
        for attachment in request.attachments:
            normalized_filename = attachment.filename.casefold()
            if normalized_filename in seen_filenames:
                raise EvidenceExtractionError(
                    "duplicate_attachment_filename",
                    f"{attachment.filename} is submitted more than once.",
                )
            seen_filenames.add(normalized_filename)
            payload = self._decode(attachment)
            total_bytes += len(payload)
            if total_bytes > MAX_TOTAL_BYTES:
                raise EvidenceExtractionError(
                    "total_upload_too_large",
                    "The submitted evidence exceeds the 10 MiB demo boundary.",
                )
            checksum = hashlib.sha256(payload).hexdigest()
            if checksum in seen_checksums:
                raise EvidenceExtractionError(
                    "duplicate_attachment_content",
                    (
                        f"{attachment.filename} duplicates an already submitted file. "
                        "Remove the duplicate and retry."
                    ),
                )
            seen_checksums.add(checksum)
            decoded.append((attachment, payload, checksum))

        pdfs = [item for item in decoded if item[0].content_type == "application/pdf"]
        images = [item for item in decoded if item[0].content_type == "image/png"]
        if len(pdfs) != 1 or not images:
            raise EvidenceExtractionError(
                "required_modalities_missing",
                "Submit exactly one PDF and at least one PNG accident image.",
            )

        pdf_attachment, pdf_payload, pdf_checksum = pdfs[0]
        pdf_text = self._extract_pdf_text(pdf_attachment.filename, pdf_payload)
        self._validate_requirements_text(pdf_text)

        sources = [
            EvidenceSource(
                source_file="founder-mission",
                source_type="founder_mission",
                modality=SourceModality.TEXT,
                content_type="text/plain",
                checksum_sha256=hashlib.sha256(request.mission.encode("utf-8")).hexdigest(),
                size_bytes=len(request.mission.encode("utf-8")),
                privacy_level=PrivacyLevel.INTERNAL,
                processing_status=ProcessingStatus.COMPLETE,
                adapter="mission_text_normalizer",
                adapter_mode="local_parser",
            ),
            self._source(
                pdf_attachment,
                pdf_checksum,
                len(pdf_payload),
                source_type="insurer_requirements",
                modality=SourceModality.DOCUMENT,
                adapter="local_pypdf_text_extractor",
                adapter_mode="local_parser",
            ),
        ]

        evidence = self._base_evidence(request.mission, pdf_attachment.filename, pdf_checksum)
        for attachment, payload, checksum in images:
            matched = self._asset_by_hash.get(checksum)
            if (
                not isinstance(matched, dict)
                or matched.get("filename") not in self.scenario["expected_image_findings"]
            ):
                raise EvidenceExtractionError(
                    "unsupported_image_fixture",
                    (
                        f"{attachment.filename} is a valid PNG but has no configured "
                        "multimodal Adapter. Retry with the stable synthetic fixture or "
                        "configure the formal image Adapter."
                    ),
                )
            canonical_name = str(matched["filename"])
            sources.append(
                self._source(
                    attachment,
                    checksum,
                    len(payload),
                    source_type="synthetic_accident_scene",
                    modality=SourceModality.IMAGE,
                    adapter="sha256_bound_synthetic_fixture_adapter",
                    adapter_mode="deterministic_fixture",
                )
            )
            for index, finding in enumerate(
                self.scenario["expected_image_findings"][canonical_name],
                start=1,
            ):
                evidence.append(
                    EvidenceItem(
                        evidence_id=(
                            f"E-IMG-{canonical_name.removesuffix('.png').upper()}-{index:03d}"
                        ),
                        category=EvidenceCategory.ACCIDENT,
                        content=finding,
                        source_file=attachment.filename,
                        source_type="synthetic_accident_scene",
                        modality=SourceModality.IMAGE,
                        confidence=0.98 if index == 1 else 0.92,
                        privacy_level=attachment.privacy_level,
                        used_by_agents=[
                            "product-agent",
                            "engineering-agent",
                            "risk-agent",
                        ],
                        adapter="sha256_bound_synthetic_fixture_adapter",
                        adapter_mode="deterministic_fixture",
                        cloud_eligible=False,
                        source_checksum_sha256=checksum,
                    )
                )

        sources.extend(self._structured_sources())
        return EvidencePackage(
            scenario_id=self.scenario["scenario_id"],
            mission=request.mission,
            synthetic=True,
            authoritative=False,
            sources=sources,
            evidence=evidence,
            constraints=[
                "Two-week delivery window (10 business days).",
                "Total budget CNY 50,000 including CNY 5,000 reserve.",
                "Restricted evidence remains local unless sanitized and explicitly approved.",
                "Liability output is a model recommendation plus human review, never an authoritative claim decision.",
            ],
            warnings=[
                self.scenario["known_demo_adapters"]["image_adapter_limitation"],
                self.scenario["disclaimer"],
            ],
        )

    def _base_evidence(
        self,
        mission: str,
        pdf_filename: str,
        pdf_checksum: str,
    ) -> list[EvidenceItem]:
        budget = self.scenario["budget"]
        return [
            EvidenceItem(
                evidence_id="E-MISSION-001",
                category=EvidenceCategory.BUSINESS,
                content=mission,
                source_file="founder-mission",
                source_type="founder_mission",
                modality=SourceModality.TEXT,
                confidence=1.0,
                privacy_level=PrivacyLevel.INTERNAL,
                used_by_agents=[
                    "executive-orchestrator",
                    "product-agent",
                    "finance-agent",
                    "engineering-agent",
                    "risk-agent",
                ],
                adapter="mission_text_normalizer",
                adapter_mode="local_parser",
                cloud_eligible=True,
            ),
            EvidenceItem(
                evidence_id="E-PDF-WINDOW-001",
                category=EvidenceCategory.BUSINESS,
                content="The insurer requires a ten-business-day POC for claims adjusters and team leads.",
                source_file=pdf_filename,
                source_type="insurer_requirements",
                modality=SourceModality.DOCUMENT,
                confidence=0.99,
                privacy_level=PrivacyLevel.RESTRICTED,
                used_by_agents=["product-agent", "engineering-agent", "finance-agent"],
                adapter="local_pypdf_text_extractor",
                adapter_mode="local_parser",
                cloud_eligible=True,
                source_checksum_sha256=pdf_checksum,
            ),
            EvidenceItem(
                evidence_id="E-PDF-GOVERNANCE-001",
                category=EvidenceCategory.COMPLIANCE_CONSTRAINT,
                content="No claim-facing or external write may occur before an explicit human decision.",
                source_file=pdf_filename,
                source_type="insurer_requirements",
                modality=SourceModality.DOCUMENT,
                confidence=0.99,
                privacy_level=PrivacyLevel.RESTRICTED,
                used_by_agents=["risk-agent", "verifier"],
                adapter="local_pypdf_text_extractor",
                adapter_mode="local_parser",
                cloud_eligible=False,
                source_checksum_sha256=pdf_checksum,
            ),
            EvidenceItem(
                evidence_id="E-BUDGET-001",
                category=EvidenceCategory.FINANCIAL,
                content=(
                    f"Maximum POC budget is CNY {budget['total']:,}, including a "
                    f"CNY {budget['reserve']:,} reserve."
                ),
                source_file="scenario.json",
                source_type="structured_budget",
                modality=SourceModality.STRUCTURED_DATA,
                confidence=1.0,
                privacy_level=PrivacyLevel.RESTRICTED,
                used_by_agents=["product-agent", "finance-agent", "executive-orchestrator"],
                adapter="structured_fixture_parser",
                adapter_mode="local_parser",
                cloud_eligible=False,
            ),
            EvidenceItem(
                evidence_id="E-TECH-001",
                category=EvidenceCategory.TECHNICAL,
                content=(
                    "The accepted platform already provides the governed Agent runtime, "
                    "Product API, Mission Control, Evaluation, Artifact Store, Policy Gate, "
                    "bounded recovery, and human Approval."
                ),
                source_file="scenario.json",
                source_type="structured_project_status",
                modality=SourceModality.STRUCTURED_DATA,
                confidence=1.0,
                privacy_level=PrivacyLevel.INTERNAL,
                used_by_agents=["product-agent", "engineering-agent"],
                adapter="structured_fixture_parser",
                adapter_mode="local_parser",
            ),
            EvidenceItem(
                evidence_id="E-TECH-LIMIT-001",
                category=EvidenceCategory.TECHNICAL,
                content=(
                    "No production traffic-liability or arbitrary-image Adapter is currently "
                    "available; the stable image path is a checksum-bound synthetic fixture."
                ),
                source_file="scenario.json",
                source_type="structured_project_status",
                modality=SourceModality.STRUCTURED_DATA,
                confidence=1.0,
                privacy_level=PrivacyLevel.INTERNAL,
                used_by_agents=["engineering-agent", "risk-agent", "verifier"],
                adapter="structured_fixture_parser",
                adapter_mode="local_parser",
            ),
        ]

    def _structured_sources(self) -> list[EvidenceSource]:
        scenario_path = self.fixture_dir / "scenario.json"
        payload = self._read_fixture_bytes(scenario_path)
        checksum = hashlib.sha256(payload).hexdigest()
        return [
            EvidenceSource(
                source_file="scenario.json",
                source_type="structured_budget_and_project_status",
                modality=SourceModality.STRUCTURED_DATA,
                content_type="application/json",
                checksum_sha256=checksum,
                size_bytes=len(payload),
                privacy_level=PrivacyLevel.RESTRICTED,
                processing_status=ProcessingStatus.COMPLETE,
                adapter="structured_fixture_parser",
                adapter_mode="local_parser",
            )
        ]

    @staticmethod
    def _source(
        attachment: AttachmentUpload,
        checksum: str,
        size_bytes: int,
        *,
        source_type: str,
        modality: SourceModality,
        adapter: str,
        adapter_mode: Literal["live", "deterministic_fixture", "local_parser"],
    ) -> EvidenceSource:
        return EvidenceSource(
            source_file=attachment.filename,
            source_type=source_type,
            modality=modality,
            content_type=attachment.content_type,
            checksum_sha256=checksum,
            size_bytes=size_bytes,
            privacy_level=attachment.privacy_level,
            processing_status=ProcessingStatus.COMPLETE,
            adapter=adapter,
            adapter_mode=adapter_mode,
        )

    def _load_json(self, filename: str) -> dict[str, Any]:
        path = self.fixture_dir / filename
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceExtractionError(
                "fixture_configuration_failed",
                f"Could not load {filename}.",
            ) from exc
        if not isinstance(value, dict):
            raise EvidenceExtractionError(
                "fixture_configuration_failed",
                f"{filename} must contain a JSON object.",
            )
        return value

    @staticmethod
    def _read_fixture_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise EvidenceExtractionError(
                "fixture_configuration_failed",
                f"Could not read stable fixture {path.name}.",
            ) from exc

    @staticmethod
    def _decode(attachment: AttachmentUpload) -> bytes:
        try:
            payload = base64.b64decode(attachment.base64_content, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EvidenceExtractionError(
                "invalid_base64",
                f"{attachment.filename} is not valid base64 input.",
            ) from exc
        if not payload:
            raise EvidenceExtractionError(
                "empty_attachment",
                f"{attachment.filename} is empty.",
            )
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise EvidenceExtractionError(
                "attachment_too_large",
                f"{attachment.filename} exceeds the 4 MiB per-file boundary.",
            )
        magic = PDF_MAGIC if attachment.content_type == "application/pdf" else PNG_MAGIC
        if not payload.startswith(magic):
            raise EvidenceExtractionError(
                "content_type_mismatch",
                f"{attachment.filename} does not match {attachment.content_type}.",
            )
        return payload

    @staticmethod
    def _extract_pdf_text(filename: str, payload: bytes) -> str:
        try:
            reader = PdfReader(io.BytesIO(payload), strict=True)
            if len(reader.pages) > MAX_PDF_PAGES:
                raise EvidenceExtractionError(
                    "pdf_page_limit_exceeded",
                    f"{filename} exceeds the {MAX_PDF_PAGES}-page demo boundary.",
                )
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
            if len(text) > MAX_PDF_TEXT_CHARS:
                raise EvidenceExtractionError(
                    "pdf_text_limit_exceeded",
                    f"{filename} exceeds the extracted-text demo boundary.",
                )
        except EvidenceExtractionError:
            raise
        except Exception as exc:
            raise EvidenceExtractionError(
                "pdf_parse_failed",
                f"{filename} could not be parsed. Replace it and retry.",
            ) from exc
        if not text:
            raise EvidenceExtractionError(
                "pdf_text_missing",
                f"{filename} contains no extractable text. OCR is not configured.",
            )
        return text

    @staticmethod
    def _validate_requirements_text(text: str) -> None:
        normalized = " ".join(text.lower().split())
        required_terms = (
            "ten business days",
            "evidence traceability",
            "external writes without approval",
            "cny 50,000",
        )
        missing = [term for term in required_terms if term not in normalized]
        if missing:
            raise EvidenceExtractionError(
                "requirements_not_recognized",
                "The PDF is readable but does not match the frozen POC requirements.",
            )
