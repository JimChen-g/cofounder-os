from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.insurance_poc import DemoEvaluationResponse
from app.insurance_poc.evaluation import InsurancePOCDemoEvaluator
from app.main import app


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "examples" / "insurance-poc"


def test_demo_evaluation_runs_six_persisted_workflows(tmp_path: Path) -> None:
    result = InsurancePOCDemoEvaluator(
        fixture_dir=FIXTURE_DIR,
        data_dir=tmp_path,
    ).run()

    assert result.label == "demo evaluation"
    assert result.sample_size == 6
    assert result.baseline.task_completion_rate == 1 / 6
    assert result.cofounder_os.task_completion_rate == 1
    assert result.baseline.routing_accuracy == 1 / 6
    assert result.cofounder_os.routing_accuracy == 1
    assert result.cofounder_os.local_model_share == 0.5
    assert result.baseline.tool_success_rate == 0
    assert result.cofounder_os.tool_success_rate == 1
    assert result.cofounder_os.verifier_correction_count == 12
    assert result.cofounder_os.human_intervention_count == 6
    assert result.baseline.estimated_cloud_api_cost_usd == 0.24
    assert result.cofounder_os.estimated_cloud_api_cost_usd == 0.04
    assert (
        len(
            [
                path
                for path in (tmp_path / "runs").iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ]
        )
        == 6
    )
    assert all(
        sample["cofounder_os"]["route_execution_status"] == "decision_only"
        for sample in result.sample_results
    )
    assert "not a live single-model quality run" in result.disclosure


def test_committed_demo_evaluation_result_matches_contract() -> None:
    path = FIXTURE_DIR / "demo-evaluation-results.json"
    result = DemoEvaluationResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))

    assert result.sample_size == 6
    assert result.label == "demo evaluation"
    assert "no billing claim" in result.metric_sources["estimated_cloud_api_cost_usd"]


def test_demo_evaluation_api_exposes_small_sample_disclosure() -> None:
    with TestClient(app) as client:
        response = client.get("/api/insurance-poc/evaluation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["sample_size"] == 6
    assert payload["label"] == "demo evaluation"
    assert "do not establish statistical model quality" in payload["disclosure"]
