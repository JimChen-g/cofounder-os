from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"


def _load_json(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def test_insurance_poc_scenario_freezes_the_golden_mission() -> None:
    scenario = _load_json("scenario.json")

    assert scenario["scenario_id"] == "insurance-poc-golden-001"
    assert scenario["synthetic"] is True
    assert scenario["authoritative"] is False
    assert scenario["insurer_requirements"]["duration_business_days"] == 10
    assert scenario["budget"]["total"] == 50_000
    assert scenario["budget"]["reserve"] == 5_000
    assert scenario["known_demo_adapters"]["live_image_model_call"] is False
    assert set(scenario["expected_image_findings"]) == {
        "accident-intersection.png",
        "accident-damage.png",
    }


def test_insurance_poc_binary_manifest_matches_committed_assets() -> None:
    manifest = _load_json("asset-manifest.json")

    assert manifest["schema_version"] == "insurance-poc-assets-1.0"
    assets = manifest["assets"]
    assert len(assets) == 3
    assert {item["filename"] for item in assets} == {
        "insurance-poc-requirements.pdf",
        "accident-intersection.png",
        "accident-damage.png",
    }
    for item in assets:
        path = FIXTURE_DIR / item["filename"]
        assert path.is_file()
        assert path.stat().st_size == item["size_bytes"]
        assert _sha256(path) == item["sha256"]
        assert item["synthetic"] is True


def test_insurance_poc_pdf_and_images_have_expected_container_shape() -> None:
    pdf = (FIXTURE_DIR / "insurance-poc-requirements.pdf").read_bytes()

    assert pdf.startswith(b"%PDF-")
    assert len(re.findall(rb"/Type\s*/Page\b", pdf)) == 2
    assert _png_size(FIXTURE_DIR / "accident-intersection.png") == (1400, 900)
    assert _png_size(FIXTURE_DIR / "accident-damage.png") == (1400, 900)
