"""Reproducible small-sample evaluation for the insurance POC demo."""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import ConfigDict, BaseModel, Field

from app.artifacts import FileArtifactStore
from app.domain import utc_now
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService
from app.state import FileStateRepository

from .evidence import InsurancePOCEvidenceService
from .models import (
    DemoEvaluationMetrics,
    DemoEvaluationResponse,
    GoldenWorkflowRequest,
)
from .routing import STEP
from .workflow import InsurancePOCGoldenWorkflow


BASELINE_MODEL = STEP
BASELINE_MODEL_LATENCY_MS = 6_000.0
BASELINE_MODEL_COST_USD = 0.04


class _EvaluationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    founder_task: str = Field(min_length=1)
    focus_task_key: str = Field(min_length=1)
    expected_selected_model: str = Field(min_length=1)
    requires_tool: bool
    requires_verifier: bool


class _EvaluationTaskSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["insurance-demo-evaluation-tasks-1.0"]
    label: Literal["demo evaluation"]
    disclosure: str
    tasks: list[_EvaluationTask] = Field(min_length=5, max_length=8)


class InsurancePOCDemoEvaluator:
    """Compare an executable single-model contract with persisted golden runs."""

    def __init__(self, *, fixture_dir: Path, data_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.data_dir = data_dir

    def run(self) -> DemoEvaluationResponse:
        task_set = self._load_tasks()
        evidence_service = InsurancePOCEvidenceService(self.fixture_dir)
        fixture = evidence_service.fixture()
        orchestration = OrchestrationService(FileStateRepository(self.data_dir / "runs"))
        store = FileArtifactStore(self.data_dir)
        workflow = InsurancePOCGoldenWorkflow(
            fixture_dir=self.fixture_dir,
            orchestration=orchestration,
            artifacts=ArtifactRegistrationService(store, orchestration),
        )

        sample_results: list[dict[str, object]] = []
        baseline_latencies: list[float] = []
        cofounder_harness_latencies: list[float] = []
        cofounder_route_latencies: list[float] = []
        baseline_completion = 0
        baseline_routing = 0
        baseline_tools = 0
        cofounder_completion = 0
        cofounder_routing = 0
        cofounder_local = 0
        cofounder_tools = 0
        tool_denominator = 0
        correction_count = 0
        intervention_count = 0
        baseline_cost = 0.0
        cofounder_cost = 0.0

        for task in task_set.tasks:
            baseline_started = time.perf_counter()
            baseline_route_correct = BASELINE_MODEL == task.expected_selected_model
            baseline_tool_success = not task.requires_tool
            baseline_verification_success = not task.requires_verifier
            baseline_task_completed = (
                baseline_route_correct and baseline_tool_success and baseline_verification_success
            )
            baseline_elapsed = (time.perf_counter() - baseline_started) * 1_000
            baseline_latencies.append(baseline_elapsed)
            baseline_completion += int(baseline_task_completed)
            baseline_routing += int(baseline_route_correct)
            baseline_cost += BASELINE_MODEL_COST_USD

            request = GoldenWorkflowRequest(
                mission=task.founder_task,
                attachments=fixture.attachments,
                owner="Demo Evaluation",
            )
            started = time.perf_counter()
            result = workflow.execute(
                request,
                evidence_service.extract(request),
                correlation_id=f"demo-eval:{task.task_id}",
            )
            cofounder_harness_latencies.append((time.perf_counter() - started) * 1_000)
            focus_task = next(
                item
                for item in result.snapshot.tasks
                if item.metadata.get("task_key") == task.focus_task_key
            )
            focus_route = next(
                item for item in result.snapshot.route_decisions if item.task_id == focus_task.id
            )
            route_correct = focus_route.selected_model == task.expected_selected_model
            verifier_report = next(
                item for item in result.snapshot.artifacts if item.name == "verification-report"
            )
            verification = store.read_json(
                verifier_report.run_id,
                str(verifier_report.metadata["logical_name"]),
                str(verifier_report.metadata["filename"]),
                verifier_report.task_id,
            )
            verified = verification["status"] == "revised_and_passed"
            tool_success = True
            if task.requires_tool:
                tool_denominator += 1
                tool_success = any(
                    artifact.name == "evidence-extraction-report"
                    and artifact.task_id == focus_task.id
                    for artifact in result.snapshot.artifacts
                )
                cofounder_tools += int(tool_success)
            completed = (
                focus_task.status == "completed"
                and route_correct
                and tool_success
                and (verified if task.requires_verifier else True)
            )
            cofounder_completion += int(completed)
            cofounder_routing += int(route_correct)
            cofounder_local += int(focus_route.selected_model == "cofounder-qwen")
            corrections = int(verification["issues_found"])
            correction_count += corrections
            interventions = len(result.snapshot.approvals)
            intervention_count += interventions
            route_latency = float(focus_route.estimated_latency_ms or 0)
            route_cost = float(focus_route.estimated_cost_usd or 0)
            cofounder_route_latencies.append(route_latency)
            cofounder_cost += route_cost
            if task.requires_tool:
                baseline_tools += int(baseline_tool_success)

            sample_results.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "focus_task_key": task.focus_task_key,
                    "expected_selected_model": task.expected_selected_model,
                    "baseline": {
                        "strategy": "single_model_no_router_no_verifier",
                        "selected_model": BASELINE_MODEL,
                        "task_completed": baseline_task_completed,
                        "routing_correct": baseline_route_correct,
                        "tool_success": (baseline_tool_success if task.requires_tool else None),
                        "verifier_available": False,
                        "external_policy_gate_available": False,
                    },
                    "cofounder_os": {
                        "run_id": str(result.run_id),
                        "route_decision_id": str(focus_route.id),
                        "selected_model": focus_route.selected_model,
                        "provider": focus_route.provider,
                        "route_execution_status": focus_route.execution_status,
                        "task_status": focus_task.status,
                        "task_completed": completed,
                        "routing_correct": route_correct,
                        "tool_success": tool_success if task.requires_tool else None,
                        "verifier_corrections": corrections,
                        "human_interventions": interventions,
                        "estimated_route_latency_ms": route_latency,
                        "estimated_cloud_api_cost_usd": route_cost,
                    },
                }
            )

        count = len(task_set.tasks)
        baseline_metrics = DemoEvaluationMetrics(
            task_completion_rate=baseline_completion / count,
            routing_accuracy=baseline_routing / count,
            local_model_share=0,
            tool_success_rate=(baseline_tools / tool_denominator if tool_denominator else 0),
            verifier_correction_count=0,
            human_intervention_count=0,
            average_latency_ms=BASELINE_MODEL_LATENCY_MS,
            measured_harness_latency_ms=mean(baseline_latencies),
            estimated_cloud_api_cost_usd=round(baseline_cost, 4),
        )
        cofounder_metrics = DemoEvaluationMetrics(
            task_completion_rate=cofounder_completion / count,
            routing_accuracy=cofounder_routing / count,
            local_model_share=cofounder_local / count,
            tool_success_rate=(cofounder_tools / tool_denominator if tool_denominator else 0),
            verifier_correction_count=correction_count,
            human_intervention_count=intervention_count,
            average_latency_ms=mean(cofounder_route_latencies),
            measured_harness_latency_ms=mean(cofounder_harness_latencies),
            estimated_cloud_api_cost_usd=round(cofounder_cost, 4),
        )
        return DemoEvaluationResponse(
            schema_version="insurance-demo-evaluation-1.0",
            label="demo evaluation",
            generated_at=utc_now(),
            sample_size=count,
            disclosure=(
                f"{task_set.disclosure} The baseline is an executable capability-contract "
                "harness, not a live single-model quality run. CoFounder OS values come from "
                "persisted deterministic golden-workflow records; all model routes are decision-only."
            ),
            metric_sources={
                "task_completion_rate": "persisted task status plus task-specific acceptance checks",
                "routing_accuracy": "selected route compared with the frozen expected route",
                "local_model_share": "focus tasks selecting cofounder-qwen only; local tools are excluded",
                "tool_success_rate": "registered Evidence Extraction output for the one tool-required sample",
                "verifier_correction_count": "persisted verification-report issues_found",
                "human_intervention_count": "persisted Approval records",
                "average_latency_ms": "persisted route latency estimate; not observed model latency",
                "measured_harness_latency_ms": "local wall-clock deterministic harness runtime",
                "estimated_cloud_api_cost_usd": "persisted focus-route estimate; no billing claim",
            },
            baseline=baseline_metrics,
            cofounder_os=cofounder_metrics,
            deltas={
                "task_completion_rate": (
                    cofounder_metrics.task_completion_rate - baseline_metrics.task_completion_rate
                ),
                "routing_accuracy": (
                    cofounder_metrics.routing_accuracy - baseline_metrics.routing_accuracy
                ),
                "local_model_share": (
                    cofounder_metrics.local_model_share - baseline_metrics.local_model_share
                ),
                "tool_success_rate": (
                    cofounder_metrics.tool_success_rate - baseline_metrics.tool_success_rate
                ),
                "estimated_cloud_api_cost_usd": (
                    cofounder_metrics.estimated_cloud_api_cost_usd
                    - baseline_metrics.estimated_cloud_api_cost_usd
                ),
            },
            sample_results=sample_results,
        )

    def _load_tasks(self) -> _EvaluationTaskSet:
        path = self.fixture_dir / "demo-evaluation-tasks.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        return _EvaluationTaskSet.model_validate(value)
