"""D09 Product + Finance artifact synthesis tests."""

from __future__ import annotations

import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from app.artifacts import FileArtifactStore
from app.domain import (
    ArtifactSynthesisRequest,
    FinanceAgentResultV1,
    ProductAgentResultV1,
)
from app.services import OrchestrationService
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer, ArtifactSynthesizerError
from tests.test_finance_agent import VALID_FINANCE_RESULT
from tests.test_product_agent import VALID_RESULT_DICT


def _setup(tmp_path):
    repository = FileStateRepository(tmp_path / "runs")
    orchestration = OrchestrationService(repository)
    run, _ = orchestration.create_run(
        objective="Launch a milestone tracker",
        actor="founder",
    )
    task, _ = orchestration.create_task(
        run.id,
        title="Synthesize decision artifacts",
        description="Merge product and finance",
        assigned_agent="executive-orchestrator",
        actor="executive-orchestrator",
    )
    synthesizer = ArtifactSynthesizer(
        FileArtifactStore(tmp_path / "artifacts"),
        orchestration,
    )
    request = ArtifactSynthesisRequest(
        run_id=run.id,
        task_id=task.id,
        objective=run.objective,
        product=ProductAgentResultV1.model_validate(VALID_RESULT_DICT),
        finance=FinanceAgentResultV1.model_validate(VALID_FINANCE_RESULT),
    )
    return synthesizer, orchestration, request


def test_synthesizer_public_import_works_in_fresh_process():
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.synthesizers import ArtifactSynthesizer; "
                "print(ArtifactSynthesizer.__name__)"
            ),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ArtifactSynthesizer"


def test_synthesizer_creates_exact_five_registered_artifacts(tmp_path):
    synthesizer, orchestration, request = _setup(tmp_path)
    result = synthesizer.synthesize(request)

    expected = {
        "executive-decision-memo",
        "prd-product-brief",
        "budget-summary",
        "risk-register",
        "action-plan",
    }
    assert set(result.stored_artifacts) == expected
    assert set(result.domain_artifacts) == expected
    snapshot = orchestration.get_snapshot(request.run_id)
    assert len(snapshot.artifacts) == 5
    assert all(
        artifact.created_by == "artifact-synthesizer"
        for artifact in snapshot.artifacts
    )


def test_synthesized_artifacts_merge_product_and_finance_content(tmp_path):
    synthesizer, _, request = _setup(tmp_path)
    synthesizer.synthesize(request)

    memo = synthesizer.artifact_store.read_text(
        request.run_id,
        "executive-decision-memo",
        "executive-decision-memo.md",
        request.task_id,
    )
    risks = synthesizer.artifact_store.read_text(
        request.run_id,
        "risk-register",
        "risk-register.md",
        request.task_id,
    )
    assert "Users struggle to track product milestones." in memo
    assert "LTV/CAC" in memo
    assert "Threshold review required" in memo
    assert "Proceed conditionally" not in memo
    assert "Competitor launches similar product" in risks
    assert "Inference costs exceed plan" in risks


def test_synthesis_replay_is_idempotent(tmp_path):
    synthesizer, orchestration, request = _setup(tmp_path)
    first = synthesizer.synthesize(request)
    second = synthesizer.synthesize(request)

    assert {
        key: value.checksum_sha256
        for key, value in first.stored_artifacts.items()
    } == {
        key: value.checksum_sha256
        for key, value in second.stored_artifacts.items()
    }
    assert len(orchestration.get_snapshot(request.run_id).artifacts) == 5


def test_memo_does_not_recommend_proceeding_with_failed_economics(tmp_path):
    synthesizer, _, request = _setup(tmp_path)
    finance_value = deepcopy(VALID_FINANCE_RESULT)
    finance_value["unit_economics"]["contribution_margin_per_unit"] = -100
    finance_value["unit_economics"]["contribution_margin_ratio"] = -1
    finance_value["unit_economics"]["ltv_cac_ratio"] = 0
    base = next(
        scenario
        for scenario in finance_value["budget_scenarios"]
        if scenario["name"] == "base"
    )
    base["monthly_revenue"] = 0
    base["monthly_cost"] = 1_000_000
    stop_request = request.model_copy(
        update={
            "finance": FinanceAgentResultV1.model_validate(finance_value),
        },
        deep=True,
    )

    synthesizer.synthesize(stop_request)
    memo = synthesizer.artifact_store.read_text(
        request.run_id,
        "executive-decision-memo",
        "executive-decision-memo.md",
        request.task_id,
    )
    assert "Do not proceed with launch" in memo
    assert "Proceed conditionally" not in memo


def test_changed_replay_cannot_overwrite_accepted_artifact(tmp_path):
    synthesizer, _, request = _setup(tmp_path)
    synthesizer.synthesize(request)
    changed_product = deepcopy(request.product)
    changed_product.problem_statement = "A different accepted product problem."
    changed_request = request.model_copy(
        update={"product": changed_product},
        deep=True,
    )
    with pytest.raises(ArtifactSynthesizerError, match="failed"):
        synthesizer.synthesize(changed_request)
