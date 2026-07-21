"""Materialize and drive the fixed D14 insurance POC workflow."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.domain import ApprovalStatus
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService
from app.services.workflow_controller import WorkflowController

from .models import (
    ConflictRecord,
    EvidencePackage,
    GoldenWorkflowRequest,
    GoldenWorkflowResponse,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
)
from .routing import ExplainableInsuranceRouter
from .runtime import D14_EXPECTED_OUTPUTS, EXECUTION_BACKEND, InsurancePOCTaskRuntime


WORKFLOW_ACTOR = "insurance-poc-workflow"
EXECUTION_MODE = "resumable_deterministic_local_agents"
EXECUTION_DISCLOSURE = (
    "The accepted Workflow Controller executed separate deterministic local Agent "
    "adapters over persisted upstream artifacts. No live Qwen, Step, code execution, "
    "test success, or external delivery is claimed."
)


@dataclass(frozen=True)
class _TaskSpec:
    key: str
    title: str
    description: str
    agent: str
    dependency_keys: tuple[str, ...]
    policy_action: dict[str, Any]
    expected_policy_disposition: str | None = None


_LOCAL_READ = {
    "operation": "read",
    "tool_name": "insurance-poc-local-agent-runtime",
    "external_write": False,
    "private_data": False,
    "production_change": False,
}

TASK_SPECS = (
    _TaskSpec(
        "evidence-extraction",
        "Normalize multimodal evidence",
        "Validate source integrity and materialize the shared Evidence Package.",
        "evidence-extractor",
        (),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "executive-orchestration",
        "Materialize the bounded insurance POC plan",
        "Persist the fixed DAG and shared execution constraints.",
        "executive-orchestrator",
        ("evidence-extraction",),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "product-analysis",
        "Propose the two-week POC scope",
        "Create a source-linked Product proposal before Finance and Risk review.",
        "product-agent",
        ("executive-orchestration",),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "finance-analysis",
        "Reconcile scope with the delivery budget",
        "Independently calculate the shared proposed scope against the budget ceiling.",
        "finance-agent",
        ("executive-orchestration",),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "engineering-plan",
        "Create the bounded implementation plan",
        "Plan only verified capabilities and disclose unavailable code execution.",
        "engineering-agent",
        ("product-analysis", "finance-analysis"),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "risk-review",
        "Apply authority and privacy controls",
        "Read Product and Finance outputs and replace unsafe authority language.",
        "risk-agent",
        ("product-analysis", "finance-analysis"),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "private-upload-policy",
        "Evaluate raw private-evidence upload",
        "Submit the proposed external private upload to the real Policy Gate.",
        "risk-agent",
        ("risk-review",),
        {
            "operation": "upload",
            "tool_name": "insurer-file-upload",
            "target": "external-insurer-endpoint",
            "external_write": True,
            "private_data": True,
            "production_change": False,
        },
        "deny",
    ),
    _TaskSpec(
        "artifact-synthesis",
        "Synthesize the draft delivery package",
        "Combine persisted Product, Finance, Engineering, and Risk outputs.",
        "artifact-synthesizer",
        ("engineering-plan", "risk-review", "private-upload-policy"),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "verification",
        "Verify and revise the delivery package",
        "Independently compare the draft against persisted Finance and Risk controls.",
        "verifier",
        ("artifact-synthesis",),
        _LOCAL_READ,
    ),
    _TaskSpec(
        "release-approval",
        "Authorize sanitized POC package release",
        "Require Founder approval and record a local release receipt only.",
        "release-agent",
        ("verification",),
        {
            "operation": "message",
            "tool_name": "sanitized-poc-dispatch",
            "target": "external-insurer-reviewer",
            "external_write": True,
            "private_data": False,
            "production_change": False,
        },
        "require_approval",
    ),
)

STAGE_BY_KEY = {
    "evidence-extraction": 1,
    "executive-orchestration": 2,
    "product-analysis": 3,
    "finance-analysis": 3,
    "engineering-plan": 4,
    "risk-review": 4,
    "private-upload-policy": 5,
    "artifact-synthesis": 6,
    "verification": 7,
    "release-approval": 8,
}


class InsurancePOCGoldenWorkflow:
    """Persist the frozen DAG and delegate all execution to D10."""

    def __init__(
        self,
        *,
        fixture_dir: Path,
        orchestration: OrchestrationService,
        artifacts: ArtifactRegistrationService,
        workflow_controller: WorkflowController,
        router: ExplainableInsuranceRouter | None = None,
    ) -> None:
        self.fixture_dir = fixture_dir
        self.orchestration = orchestration
        self.artifacts = artifacts
        self.workflow_controller = workflow_controller
        self.router = router or ExplainableInsuranceRouter()
        existing_runtime = next(
            (
                adapter
                for adapter in workflow_controller.task_adapters
                if isinstance(adapter, InsurancePOCTaskRuntime)
            ),
            None,
        )
        self.runtime = existing_runtime or InsurancePOCTaskRuntime(
            orchestration=orchestration,
            artifact_store=artifacts.artifact_store,
        )
        workflow_controller.register_task_adapter(self.runtime)

    async def execute(
        self,
        request: GoldenWorkflowRequest,
        evidence_package: EvidencePackage,
        *,
        correlation_id: str | None = None,
        approval_capability_sha256: str | None = None,
        failure_injection_task: str | None = None,
    ) -> GoldenWorkflowResponse:
        scenario = self._scenario()
        routing = self.router.route(
            RoutingPreviewRequest(
                evidence_package=evidence_package,
                unavailable_models=request.unavailable_models,
            )
        )
        metadata: dict[str, Any] = {
            "scenario_id": evidence_package.scenario_id,
            "demo_primary": True,
            "synthetic": True,
            "authoritative": False,
            "execution_mode": EXECUTION_MODE,
            "execution_backend": EXECUTION_BACKEND,
            "live_model_calls": 0,
            "execution_disclosure": EXECUTION_DISCLOSURE,
            "constraints": list(evidence_package.constraints),
            "evidence_package": evidence_package.model_dump(mode="json"),
            "scenario_context": scenario,
            "routing_plan": routing.model_dump(mode="json"),
        }
        if approval_capability_sha256:
            metadata["approval_capability_sha256"] = approval_capability_sha256
            metadata["approval_capability_required"] = True
        if failure_injection_task:
            metadata["failure_injection_task"] = failure_injection_task

        run, _ = self.orchestration.create_run(
            objective=request.mission,
            owner=request.owner,
            actor=WORKFLOW_ACTOR,
            correlation_id=correlation_id,
            metadata=metadata,
        )
        _, evidence_artifact, _ = self.artifacts.write_json(
            run.id,
            "evidence-package",
            "evidence-package.json",
            evidence_package.model_dump(mode="json"),
            WORKFLOW_ACTOR,
            idempotency_key=f"{run.id}:evidence-package:v1",
            correlation_id=correlation_id,
            provenance={
                "artifact_version": "1.0",
                "source_agents": ["evidence-extractor"],
                "source_evidence": [item.evidence_id for item in evidence_package.evidence],
                "validation_status": "source_integrity_validated",
                "authoritative": False,
            },
        )
        tasks = self._create_tasks(
            run.id,
            evidence_package,
            evidence_artifact.id,
            correlation_id,
        )
        self._persist_routes(run.id, tasks, routing, correlation_id)
        result = await self.workflow_controller.run_until_terminal(
            run.id,
            correlation_id=correlation_id,
            max_cycles=100,
        )
        snapshot = result.snapshot
        pending = [
            item
            for item in snapshot.approvals
            if ApprovalStatus(item.status) == ApprovalStatus.PENDING
        ]
        if len(pending) != 1:
            raise RuntimeError("D14 must stop at exactly one pending Founder approval")
        conflicts = self._load_conflicts(snapshot)
        executed_routing = RoutingPreviewResponse(
            package_id=routing.package_id,
            decisions=[
                decision.model_copy(update={"execution_status": "executed"})
                if any(
                    route.task_id == tasks[decision.task_key].id
                    and route.execution_status == "executed"
                    for route in snapshot.route_decisions
                )
                else decision
                for decision in routing.decisions
            ],
            live_model_calls=0,
            simulation_disclosure=routing.simulation_disclosure,
        )
        return GoldenWorkflowResponse(
            run_id=run.id,
            status=str(snapshot.run.status),
            approval_id=pending[0].id,
            snapshot=snapshot,
            evidence_package=evidence_package,
            routing_plan=executed_routing,
            conflicts=conflicts,
            execution_disclosure=EXECUTION_DISCLOSURE,
        )

    def _scenario(self) -> dict[str, Any]:
        value = json.loads((self.fixture_dir / "scenario.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("synthetic") is not True:
            raise ValueError("Insurance POC scenario must be a synthetic object")
        return value

    def _create_tasks(
        self,
        run_id: UUID,
        evidence: EvidencePackage,
        evidence_artifact_id: UUID,
        correlation_id: str | None,
    ) -> dict[str, Any]:
        tasks: dict[str, Any] = {}
        evidence_ids = [item.evidence_id for item in evidence.evidence]
        for spec in TASK_SPECS:
            metadata: dict[str, Any] = {
                "task_key": spec.key,
                "task_type": f"insurance-poc.{spec.key}",
                "stage": STAGE_BY_KEY[spec.key],
                "execution_mode": EXECUTION_MODE,
                "expected_output_names": sorted(D14_EXPECTED_OUTPUTS[spec.key]),
                "evidence_package_id": str(evidence.package_id),
                "shared_evidence_artifact_id": str(evidence_artifact_id),
                "shared_evidence_ids": evidence_ids,
                "constraints": list(evidence.constraints),
                "policy_action": {"actor": spec.agent, **spec.policy_action},
            }
            if spec.expected_policy_disposition:
                metadata["expected_policy_disposition"] = spec.expected_policy_disposition
            task, _ = self.orchestration.create_task(
                run_id,
                title=spec.title,
                description=spec.description,
                assigned_agent=spec.agent,
                dependency_ids=[tasks[key].id for key in spec.dependency_keys],
                actor=WORKFLOW_ACTOR,
                correlation_id=correlation_id,
                metadata=metadata,
            )
            tasks[spec.key] = task
        return tasks

    def _persist_routes(
        self,
        run_id: UUID,
        tasks: dict[str, Any],
        routing: RoutingPreviewResponse,
        correlation_id: str | None,
    ) -> None:
        by_key = {decision.task_key: decision for decision in routing.decisions}
        missing = set(tasks) - set(by_key)
        if missing:
            raise RuntimeError(f"Router did not decide task(s): {sorted(missing)}")
        for task_key, task in tasks.items():
            decision = by_key[task_key]
            self.orchestration.record_route_decision(
                run_id,
                task_id=task.id,
                requested_model=decision.requested_model,
                selected_model=decision.selected_model,
                provider=decision.provider,
                reason=decision.reason,
                candidate_models=decision.candidate_models,
                excluded_models=decision.excluded_models,
                required_capabilities=decision.required_capabilities,
                input_modalities=[value.value for value in decision.input_modalities],
                privacy_level=decision.privacy_level.value,
                complexity=decision.complexity,
                context_length=decision.context_length,
                tool_requirement=decision.tool_requirement,
                latency_budget_ms=decision.latency_budget_ms,
                cost_budget_usd=decision.cost_budget_usd,
                estimated_latency_ms=decision.estimated_latency_ms,
                estimated_cost_usd=decision.estimated_cost_usd,
                privacy_decision=decision.privacy_decision,
                fallback_model=decision.fallback_model,
                validation_required=decision.validation_required,
                validation_requirement=decision.validation_requirement,
                execution_status="decision_only",
                fallback_used=decision.fallback_used,
                actor="explainable-insurance-router",
                correlation_id=correlation_id,
                metadata={"task_key": task_key, "selected_for_execution": True},
            )

    def _load_conflicts(self, snapshot: Any) -> list[ConflictRecord]:
        artifacts = [item for item in snapshot.artifacts if item.name == "conflict-resolution-log"]
        if len(artifacts) != 1:
            raise RuntimeError("D14 verifier must persist one conflict-resolution-log")
        artifact = artifacts[0]
        value = self.artifacts.artifact_store.read_json(
            artifact.run_id,
            str(artifact.metadata["logical_name"]),
            str(artifact.metadata["filename"]),
            artifact.task_id,
        )
        return [ConflictRecord.model_validate(item) for item in value["conflicts"]]
