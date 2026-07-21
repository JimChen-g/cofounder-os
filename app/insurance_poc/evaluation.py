"""Reproducible, run-record-based D14 demo evaluation."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts import FileArtifactStore
from app.clients import GatewayClient
from app.domain import utc_now
from app.policy import DeterministicPolicyGate
from app.services.artifact_write import ArtifactRegistrationService
from app.services.execution import AgentExecutionService
from app.services.finance_agent import FinanceAgentService
from app.services.orchestration import OrchestrationService
from app.services.product_agent import ProductAgentService
from app.services.workflow_controller import WorkflowController
from app.state import FileStateRepository
from app.synthesizers import ArtifactSynthesizer

from .evidence import InsurancePOCEvidenceService
from .models import DemoEvaluationMetrics, DemoEvaluationResponse, GoldenWorkflowRequest
from .workflow import InsurancePOCGoldenWorkflow


class _EvaluationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    founder_task: str = Field(min_length=1)
    focus_task_key: str = Field(min_length=1)
    requires_tool: bool
    requires_verifier: bool
    source_case_id: str = Field(min_length=1)


class _EvaluationTaskSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["insurance-demo-evaluation-tasks-2.0"]
    label: Literal["demo evaluation"]
    disclosure: str
    source_dataset: str
    tasks: list[_EvaluationTask] = Field(min_length=5, max_length=8)


class InsurancePOCDemoEvaluator:
    """Measure real persisted D14 runs and disclose an unavailable live baseline."""

    def __init__(self, *, fixture_dir: Path, data_dir: Path) -> None:
        self.fixture_dir = fixture_dir
        self.data_dir = data_dir

    def run(self) -> DemoEvaluationResponse:
        return asyncio.run(self._run())

    async def _run(self) -> DemoEvaluationResponse:
        task_set = self._load_tasks()
        evidence_service = InsurancePOCEvidenceService(self.fixture_dir)
        fixture = evidence_service.fixture()
        orchestration = OrchestrationService(FileStateRepository(self.data_dir / "runs"))
        store = FileArtifactStore(self.data_dir)
        gateway = GatewayClient("http://127.0.0.1:1", timeout_seconds=0.1)
        controller = WorkflowController(
            orchestration=orchestration,
            agent_execution=AgentExecutionService(orchestration.repository),
            artifact_store=store,
            product_agent_service=ProductAgentService(gateway, store, orchestration),
            finance_agent_service=FinanceAgentService(gateway, store, orchestration),
            artifact_synthesizer=ArtifactSynthesizer(store, orchestration),
            policy_gate=DeterministicPolicyGate(),
        )
        workflow = InsurancePOCGoldenWorkflow(
            fixture_dir=self.fixture_dir,
            orchestration=orchestration,
            artifacts=ArtifactRegistrationService(store, orchestration),
            workflow_controller=controller,
        )

        sample_results: list[dict[str, object]] = []
        harness_latencies: list[float] = []
        route_latencies: list[float] = []
        completed_count = 0
        routing_compliance_count = 0
        local_count = 0
        tool_success_count = 0
        tool_denominator = 0
        correction_count = 0
        intervention_count = 0
        estimated_cost = 0.0

        for task in task_set.tasks:
            request = GoldenWorkflowRequest(
                mission=task.founder_task,
                attachments=fixture.attachments,
                owner="Demo Evaluation",
            )
            started = time.perf_counter()
            result = await workflow.execute(
                request,
                evidence_service.extract(request),
                correlation_id=f"demo-eval:{task.task_id}",
            )
            harness_latencies.append((time.perf_counter() - started) * 1_000)
            focus_task = next(
                item
                for item in result.snapshot.tasks
                if item.metadata.get("task_key") == task.focus_task_key
            )
            focus_route = next(
                item for item in result.snapshot.route_decisions if item.task_id == focus_task.id
            )
            route_contract_compliant = (
                focus_route.execution_status == "executed"
                and focus_route.metadata.get("execution_backend")
                == "deterministic_local_agent"
                and len(focus_route.excluded_models)
                == len(focus_route.candidate_models) - 1
                and focus_route.estimated_cost_usd == 0
            )
            routing_compliance_count += int(route_contract_compliant)
            local = focus_route.provider.startswith("dgx-")
            local_count += int(local)
            route_latencies.append(float(focus_route.latency_ms or 0))
            estimated_cost += float(focus_route.estimated_cost_usd or 0)

            verification_artifact = next(
                item for item in result.snapshot.artifacts if item.name == "verification-report"
            )
            verification = store.read_json(
                verification_artifact.run_id,
                str(verification_artifact.metadata["logical_name"]),
                str(verification_artifact.metadata["filename"]),
                verification_artifact.task_id,
            )
            verified = verification["status"] in {
                "revised_and_passed",
                "passed_without_revision",
            }
            tool_success = True
            if task.requires_tool:
                tool_denominator += 1
                tool_success = any(
                    artifact.name == "evidence-extraction-report"
                    and artifact.task_id == focus_task.id
                    for artifact in result.snapshot.artifacts
                )
                tool_success_count += int(tool_success)
            completed = (
                focus_task.status == "completed"
                and route_contract_compliant
                and tool_success
                and (verified if task.requires_verifier else True)
            )
            completed_count += int(completed)
            corrections = int(verification["issues_found"])
            correction_count += corrections
            interventions = len(result.snapshot.approvals)
            intervention_count += interventions

            sample_results.append(
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "source_case_id": task.source_case_id,
                    "focus_task_key": task.focus_task_key,
                    "baseline": {
                        "measurement_status": "unavailable",
                        "reason": (
                            "No configured live single-model service was available; no score "
                            "or cost was fabricated."
                        ),
                    },
                    "cofounder_os": {
                        "measurement_status": "measured",
                        "run_id": str(result.run_id),
                        "route_decision_id": str(focus_route.id),
                        "selected_model": focus_route.selected_model,
                        "provider": focus_route.provider,
                        "route_execution_status": focus_route.execution_status,
                        "routing_contract_compliant": route_contract_compliant,
                        "task_status": focus_task.status,
                        "task_completed": completed,
                        "tool_success": tool_success if task.requires_tool else None,
                        "verifier_corrections": corrections,
                        "human_interventions": interventions,
                        "measured_route_latency_ms": focus_route.latency_ms,
                        "estimated_cloud_api_cost_usd": focus_route.estimated_cost_usd,
                    },
                }
            )

        count = len(task_set.tasks)
        unavailable_baseline = DemoEvaluationMetrics(
            measurement_status="unavailable",
            unavailability_reason=(
                "No configured live single-model service; rerun with an approved provider "
                "harness before claiming a baseline comparison."
            ),
            task_completion_rate=0,
            routing_accuracy=0,
            local_model_share=0,
            tool_success_rate=0,
            verifier_correction_count=0,
            human_intervention_count=0,
            average_latency_ms=0,
            measured_harness_latency_ms=0,
            estimated_cloud_api_cost_usd=0,
        )
        cofounder_metrics = DemoEvaluationMetrics(
            measurement_status="measured",
            task_completion_rate=completed_count / count,
            routing_accuracy=routing_compliance_count / count,
            local_model_share=local_count / count,
            tool_success_rate=(
                tool_success_count / tool_denominator if tool_denominator else 0
            ),
            verifier_correction_count=correction_count,
            human_intervention_count=intervention_count,
            average_latency_ms=mean(route_latencies),
            measured_harness_latency_ms=mean(harness_latencies),
            estimated_cloud_api_cost_usd=round(estimated_cost, 4),
        )
        return DemoEvaluationResponse(
            schema_version="insurance-demo-evaluation-1.0",
            label="demo evaluation",
            generated_at=utc_now(),
            sample_size=count,
            disclosure=(
                f"{task_set.disclosure} CoFounder OS metrics come only from persisted run, "
                "artifact, routing, Policy Gate, and Verifier records. The live single-model "
                "baseline is explicitly unavailable and no comparative delta is claimed."
            ),
            source_dataset=task_set.source_dataset,
            source_case_ids=sorted({task.source_case_id for task in task_set.tasks}),
            metric_sources={
                "task_completion_rate": "persisted task status plus acceptance assertions",
                "routing_accuracy": (
                    "routing contract compliance: actual backend binding, complete exclusions, "
                    "and zero unclaimed cloud cost; not model-quality accuracy"
                ),
                "local_model_share": "persisted selected provider on measured focus tasks",
                "tool_success_rate": "checksum-verified Evidence Extraction artifact",
                "verifier_correction_count": "persisted verification-report issues_found",
                "human_intervention_count": "persisted Approval records",
                "average_latency_ms": "persisted measured local Agent handler latency",
                "measured_harness_latency_ms": "wall-clock workflow duration",
                "estimated_cloud_api_cost_usd": "persisted route estimate; no billing claim",
            },
            baseline=unavailable_baseline,
            cofounder_os=cofounder_metrics,
            deltas={},
            sample_results=sample_results,
        )

    def _load_tasks(self) -> _EvaluationTaskSet:
        value = json.loads(
            (self.fixture_dir / "demo-evaluation-tasks.json").read_text(encoding="utf-8")
        )
        return _EvaluationTaskSet.model_validate(value)
