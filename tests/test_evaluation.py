"""D13 deterministic Evaluation service and API tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from app.api.evaluation import router
from app.artifacts import FileArtifactStore
from app.evaluation import EvaluationService
from app.evaluation.service import REQUIRED_ARTIFACTS
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService
from app.state import FileStateRepository


def _stack(
    tmp_path: Path,
) -> tuple[
    FileStateRepository,
    OrchestrationService,
    FileArtifactStore,
    ArtifactRegistrationService,
    EvaluationService,
]:
    repository = FileStateRepository(tmp_path / "data" / "runs")
    orchestration = OrchestrationService(repository)
    artifact_store = FileArtifactStore(tmp_path / "data")
    registration = ArtifactRegistrationService(
        artifact_store,
        orchestration,
    )
    return (
        repository,
        orchestration,
        artifact_store,
        registration,
        EvaluationService(orchestration, artifact_store),
    )


def _completed_run(
    orchestration: OrchestrationService,
    registration: ArtifactRegistrationService,
) -> UUID:
    run, _ = orchestration.create_run(
        objective="Should we launch the governed pilot?",
        owner="Founder",
        actor="founder",
    )
    orchestration.start_run(
        run.id,
        actor="workflow-controller",
        reason="Begin.",
    )
    for agent_id in (
        "product-agent",
        "finance-agent",
        "executive-orchestrator",
    ):
        task, _ = orchestration.create_task(
            run.id,
            title=f"{agent_id} brief",
            assigned_agent=agent_id,
            actor="executive-orchestrator",
        )
        orchestration.mark_task_ready(
            run.id,
            task.id,
            actor="workflow-controller",
            reason="Dependencies ready.",
        )
        orchestration.start_task(
            run.id,
            task.id,
            actor=agent_id,
            reason="Execute.",
        )
        attempted = orchestration.repository.get_task(run.id, task.id)
        attempted.attempt_count = 1
        orchestration.repository.save_task(attempted)
        orchestration.record_route_decision(
            run.id,
            task_id=(
                None
                if agent_id == "executive-orchestrator"
                else task.id
            ),
            selected_model="qwen3.6-local",
            provider="qwen",
            reason="Local route.",
        )
        orchestration.complete_task(
            run.id,
            task.id,
            actor=agent_id,
            reason="Evidence stored.",
        )

    for logical_name in sorted(REQUIRED_ARTIFACTS):
        registration.write_text(
            run.id,
            logical_name,
            f"{logical_name}.md",
            f"# {logical_name}\n",
            "artifact-synthesizer",
            relation="run",
        )

    orchestration.complete_run(
        run.id,
        actor="workflow-controller",
        reason="All governed tasks complete.",
    )
    return run.id


def _failed_run(orchestration: OrchestrationService) -> UUID:
    run, _ = orchestration.create_run(
        objective="Evaluate a failed workflow",
        actor="founder",
    )
    orchestration.start_run(
        run.id,
        actor="workflow-controller",
        reason="Begin.",
    )
    task, _ = orchestration.create_task(
        run.id,
        title="Product brief",
        assigned_agent="product-agent",
        actor="executive-orchestrator",
    )
    orchestration.mark_task_ready(
        run.id,
        task.id,
        actor="workflow-controller",
        reason="Ready.",
    )
    orchestration.start_task(
        run.id,
        task.id,
        actor="product-agent",
        reason="Execute.",
    )
    orchestration.record_route_decision(
        run.id,
        task_id=task.id,
        selected_model="qwen3.6-local",
        provider="qwen",
        reason="Local route.",
    )
    orchestration.fail_task(
        run.id,
        task.id,
        actor="product-agent",
        reason="Bounded attempts exhausted.",
    )
    orchestration.fail_run(
        run.id,
        actor="workflow-controller",
        reason="Required task failed.",
    )
    return run.id


def _client(service: EvaluationService) -> TestClient:
    test_app = FastAPI()

    @test_app.middleware("http")
    async def request_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = "req-d13-test"
        return await call_next(request)

    test_app.state.evaluation_service = service
    test_app.include_router(router)
    return TestClient(test_app)


def test_completed_run_receives_explainable_full_score(
    tmp_path: Path,
) -> None:
    _, orchestration, _, registration, service = _stack(tmp_path)
    run_id = _completed_run(orchestration, registration)

    result = service.evaluate_run(run_id)

    assert result.overall_score == 100.0
    assert result.grade == "excellent"
    assert result.status == "completed"
    assert result.task_count == 3
    assert result.completed_tasks == 3
    assert result.verified_artifact_count == 9
    assert result.providers == ["qwen"]
    assert {item.key for item in result.dimensions} == {
        "workflow",
        "execution",
        "artifacts",
        "governance",
        "auditability",
    }
    assert all(item.evidence for item in result.dimensions)
    assert all(item.score == 100 for item in result.dimensions)


def test_failed_run_scores_below_completed_run(tmp_path: Path) -> None:
    _, orchestration, _, registration, service = _stack(tmp_path)
    completed_id = _completed_run(orchestration, registration)
    failed_id = _failed_run(orchestration)

    completed = service.evaluate_run(completed_id)
    failed = service.evaluate_run(failed_id)

    assert failed.status == "failed"
    assert failed.failed_tasks == 1
    assert failed.overall_score < completed.overall_score
    assert failed.grade == "critical"


def test_corrupt_artifact_lowers_integrity_without_failing_evaluation(
    tmp_path: Path,
) -> None:
    _, orchestration, store, registration, service = _stack(tmp_path)
    run_id = _completed_run(orchestration, registration)
    before = service.evaluate_run(run_id)
    artifact = orchestration.get_snapshot(run_id).artifacts[0]
    logical_name = str(artifact.metadata["logical_name"])
    filename = str(artifact.metadata["filename"])
    content_path = (
        store.root
        / "runs"
        / str(run_id)
        / "artifacts"
        / "run"
        / logical_name
        / filename
    )
    content_path.write_text("corrupt", encoding="utf-8")

    after = service.evaluate_run(run_id)

    assert after.verified_artifact_count == before.verified_artifact_count - 1
    assert after.overall_score < before.overall_score
    artifact_dimension = next(
        item for item in after.dimensions if item.key == "artifacts"
    )
    assert artifact_dimension.score < 100
    assert str(tmp_path) not in " ".join(artifact_dimension.evidence)


def test_non_regular_artifact_lowers_score_and_summary_remains_available(
    tmp_path: Path,
) -> None:
    _, orchestration, store, registration, service = _stack(tmp_path)
    run_id = _completed_run(orchestration, registration)
    artifact = orchestration.get_snapshot(run_id).artifacts[0]
    logical_name = str(artifact.metadata["logical_name"])
    filename = str(artifact.metadata["filename"])
    content_path = (
        store.root
        / "runs"
        / str(run_id)
        / "artifacts"
        / "run"
        / logical_name
        / filename
    )
    content_path.unlink()
    content_path.mkdir()

    evaluated = service.evaluate_run(run_id)
    summary = service.summary(limit=10)
    client = _client(service)
    try:
        response = client.get("/api/evaluation/summary?limit=10")
    finally:
        client.close()

    assert evaluated.verified_artifact_count == 8
    assert evaluated.overall_score < 100
    assert summary.run_count == 1
    assert summary.recent_runs[0].run_id == run_id
    assert response.status_code == 200
    assert response.json()["recent_runs"][0]["verified_artifact_count"] == 8


def test_completed_task_with_zero_attempts_is_not_first_pass(
    tmp_path: Path,
) -> None:
    _, orchestration, _, registration, service = _stack(tmp_path)
    run_id = _completed_run(orchestration, registration)
    task = orchestration.get_snapshot(run_id).tasks[0]
    task.attempt_count = 0
    orchestration.repository.save_task(task)

    evaluated = service.evaluate_run(run_id)
    execution = next(
        item for item in evaluated.dimensions if item.key == "execution"
    )

    assert execution.score == 90.0
    assert "2/3 tasks completed on the first attempt." in execution.evidence
    assert evaluated.overall_score < 100


def test_summary_aggregates_runs_agents_and_providers(
    tmp_path: Path,
) -> None:
    _, orchestration, _, registration, service = _stack(tmp_path)
    completed_id = _completed_run(orchestration, registration)
    failed_id = _failed_run(orchestration)

    summary = service.summary(limit=50)

    assert summary.run_count == 2
    assert summary.completion_rate == 50.0
    assert summary.status_distribution == {"completed": 1, "failed": 1}
    assert summary.provider_distribution == {"qwen": 2}
    assert summary.total_retries == 0
    assert {item.run_id for item in summary.recent_runs} == {
        completed_id,
        failed_id,
    }
    product = next(
        item
        for item in summary.agent_performance
        if item.agent_id == "product-agent"
    )
    assert product.tasks == 2
    assert product.completed == 1
    assert product.failed == 1
    assert product.success_rate == 50.0


def test_evaluation_api_is_bounded_and_read_only(tmp_path: Path) -> None:
    _, orchestration, _, registration, service = _stack(tmp_path)
    run_id = _completed_run(orchestration, registration)
    client = _client(service)
    try:
        summary = client.get("/api/evaluation/summary?limit=10")
        run = client.get(f"/api/evaluation/runs/{run_id}")
        missing = client.get(f"/api/evaluation/runs/{uuid4()}")
        invalid_limit = client.get("/api/evaluation/summary?limit=0")

        assert summary.status_code == 200
        assert summary.json()["run_count"] == 1
        assert run.status_code == 200
        assert run.json()["overall_score"] == 100.0
        assert missing.status_code == 404
        assert missing.json() == {
            "error": "not_found",
            "detail": "The requested Run was not found.",
            "request_id": "req-d13-test",
        }
        assert invalid_limit.status_code == 422
        assert orchestration.get_snapshot(run_id).run.status == "completed"
    finally:
        client.close()
