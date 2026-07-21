"""Synthetic traffic-liability Demo acceptance tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.demo import TrafficLiabilityDemoError, seed_traffic_liability_demo
from app.domain import ApprovalStatus, RunStatus, TaskStatus
from app.evaluation import EvaluationService
from app.evaluation.service import REQUIRED_ARTIFACTS
from app.services.product_api import build_product_api_service


CASE_FILE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "traffic-liability-demo-case.json"
)


def test_seeded_case_is_video_ready_and_explicitly_non_authoritative(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "traffic-demo"
    result = seed_traffic_liability_demo(data_dir, CASE_FILE)
    service = build_product_api_service(
        Settings(_env_file=None, PRODUCT_DATA_DIR=str(data_dir))
    )
    snapshot = service.orchestration.get_snapshot(result.run_id)

    assert result.created is True
    assert RunStatus(snapshot.run.status) == RunStatus.WAITING_APPROVAL
    assert snapshot.run.metadata["synthetic"] is True
    assert snapshot.run.metadata["authoritative"] is False
    assert (
        snapshot.run.metadata["inference_mode"]
        == "deterministic_demo_fixture"
    )
    assert len(snapshot.tasks) == 3
    assert all(
        TaskStatus(task.status) == TaskStatus.COMPLETED
        for task in snapshot.tasks
    )
    assert all(task.attempt_count == 1 for task in snapshot.tasks)
    assert {artifact.name for artifact in snapshot.artifacts} == REQUIRED_ARTIFACTS
    assert len(snapshot.artifacts) == 9
    assert len(snapshot.approvals) == 1
    assert ApprovalStatus(snapshot.approvals[0].status) == ApprovalStatus.PENDING
    assert snapshot.approvals[0].metadata["reviewer_required"] == "founder"
    assert all(
        decision.selected_model == "qwen-traffic-liability-demo-fixture-v0"
        for decision in snapshot.route_decisions
    )

    evaluation = EvaluationService(
        service.orchestration,
        service.artifact_store,
    ).evaluate_run(result.run_id)
    assert evaluation.grade == "good"
    assert evaluation.overall_score == 72.5


def test_seed_is_idempotent_unless_force_new_is_requested(
    tmp_path: Path,
) -> None:
    first = seed_traffic_liability_demo(tmp_path, CASE_FILE)
    replay = seed_traffic_liability_demo(tmp_path, CASE_FILE)
    second = seed_traffic_liability_demo(tmp_path, CASE_FILE, force_new=True)

    assert replay.created is False
    assert replay.run_id == first.run_id
    assert second.created is True
    assert second.run_id != first.run_id


@pytest.mark.asyncio
async def test_founder_approval_resumes_existing_controller_and_completes_run(
    tmp_path: Path,
) -> None:
    result = seed_traffic_liability_demo(tmp_path, CASE_FILE)
    service = build_product_api_service(
        Settings(_env_file=None, PRODUCT_DATA_DIR=str(tmp_path))
    )

    resolution = await service.resolve_approval(
        result.run_id,
        result.approval_id,
        decision="approved",
        decided_by="founder",
        reason="Synthetic evidence and non-authoritative boundary reviewed.",
        correlation_id="traffic-demo-test",
        max_cycles=10,
    )

    assert resolution.workflow.status == RunStatus.COMPLETED
    assert RunStatus(resolution.workflow.snapshot.run.status) == RunStatus.COMPLETED
    assert (
        ApprovalStatus(resolution.resolution.approval.status)
        == ApprovalStatus.APPROVED
    )
    replay = await service.retry_run(
        result.run_id,
        correlation_id="traffic-demo-replay",
        max_cycles=10,
    )
    assert replay.replayed is True
    assert replay.status == RunStatus.COMPLETED


def test_seed_rejects_fixture_that_could_be_mistaken_for_real_inference(
    tmp_path: Path,
) -> None:
    fixture = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    fixture["synthetic"] = False
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text(json.dumps(fixture), encoding="utf-8")

    with pytest.raises(TrafficLiabilityDemoError, match="synthetic=true"):
        seed_traffic_liability_demo(tmp_path / "data", unsafe)
