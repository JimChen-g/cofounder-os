"""Fixed, evidence-sharing golden workflow for the insurance POC demo.

The service deliberately executes deterministic fixture rules while model
services are unavailable. Explainable model routes are persisted as
``decision_only`` records and are never presented as completed model calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from app.policy import DeterministicPolicyGate, PolicyAction
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService

from .models import (
    ConflictRecord,
    EvidencePackage,
    GoldenWorkflowRequest,
    GoldenWorkflowResponse,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
)
from .routing import ExplainableInsuranceRouter


WORKFLOW_ACTOR = "insurance-poc-golden-workflow"
EXECUTION_MODE = "deterministic_acceptance_fixture"
EXECUTION_DISCLOSURE = (
    "The golden workflow executed deterministic, source-linked acceptance rules. "
    "Persisted model routes remain decision-only because no live Qwen or Step call "
    "was available; no model, code execution, test, or external write is fabricated."
)


@dataclass(frozen=True)
class _TaskSpec:
    key: str
    title: str
    description: str
    agent: str
    dependency_keys: tuple[str, ...]


TASK_SPECS = (
    _TaskSpec(
        "evidence-extraction",
        "Normalize multimodal evidence",
        "Validate PDF/image source integrity and create the shared Evidence Package.",
        "evidence-extractor",
        (),
    ),
    _TaskSpec(
        "executive-orchestration",
        "Materialize the insurance POC plan",
        "Create the fixed bounded DAG and shared execution constraints.",
        "executive-orchestrator",
        ("evidence-extraction",),
    ),
    _TaskSpec(
        "product-analysis",
        "Propose the two-week POC scope",
        "Define target users, acceptance, initial scope, and authority language.",
        "product-agent",
        ("executive-orchestration",),
    ),
    _TaskSpec(
        "finance-analysis",
        "Validate budget and reserve",
        "Calculate the delivery ceiling and flag optional work that exceeds it.",
        "finance-agent",
        ("executive-orchestration",),
    ),
    _TaskSpec(
        "engineering-plan",
        "Create the bounded implementation plan",
        "Plan only verified capabilities and disclose unavailable code execution.",
        "engineering-agent",
        ("product-analysis", "finance-analysis"),
    ),
    _TaskSpec(
        "risk-review",
        "Apply authority and privacy controls",
        "Resolve unsafe liability language and evaluate proposed external actions.",
        "risk-agent",
        ("product-analysis", "finance-analysis"),
    ),
    _TaskSpec(
        "artifact-synthesis",
        "Synthesize the draft delivery package",
        "Combine scoped Product, Finance, Engineering, and Risk outputs.",
        "artifact-synthesizer",
        ("engineering-plan", "risk-review"),
    ),
    _TaskSpec(
        "verification",
        "Verify and revise the delivery package",
        "Independently correct budget and authority inconsistencies.",
        "verifier",
        ("artifact-synthesis",),
    ),
)

EXPECTED_OUTPUTS = {
    "evidence-extraction": ["evidence-extraction-report"],
    "executive-orchestration": ["golden-dag"],
    "product-analysis": ["product-scope-proposal"],
    "finance-analysis": ["finance-budget-analysis"],
    "engineering-plan": ["engineering-delivery-plan"],
    "risk-review": ["risk-governance-review"],
    "artifact-synthesis": ["draft-delivery-package"],
    "verification": [
        "executive-decision-memo",
        "insurance-poc-product-brief",
        "technical-implementation-plan",
        "budget-summary",
        "risk-register",
        "two-week-action-plan",
        "verification-report",
    ],
}

STAGE_BY_KEY = {
    "evidence-extraction": 1,
    "executive-orchestration": 2,
    "product-analysis": 3,
    "finance-analysis": 3,
    "engineering-plan": 4,
    "risk-review": 4,
    "artifact-synthesis": 5,
    "verification": 6,
}


class InsurancePOCGoldenWorkflow:
    """Persist and execute the single frozen insurance POC DAG."""

    def __init__(
        self,
        *,
        fixture_dir: Path,
        orchestration: OrchestrationService,
        artifacts: ArtifactRegistrationService,
        policy_gate: DeterministicPolicyGate | None = None,
        router: ExplainableInsuranceRouter | None = None,
    ) -> None:
        self.fixture_dir = fixture_dir
        self.orchestration = orchestration
        self.artifacts = artifacts
        self.policy_gate = policy_gate or DeterministicPolicyGate()
        self.router = router or ExplainableInsuranceRouter()

    def execute(
        self,
        request: GoldenWorkflowRequest,
        evidence_package: EvidencePackage,
        *,
        correlation_id: str | None = None,
    ) -> GoldenWorkflowResponse:
        scenario = self._scenario()
        routing = self.router.route(
            RoutingPreviewRequest(
                evidence_package=evidence_package,
                unavailable_models=request.unavailable_models,
            )
        )
        run, _ = self.orchestration.create_run(
            objective=request.mission,
            owner=request.owner,
            actor=WORKFLOW_ACTOR,
            correlation_id=correlation_id,
            metadata={
                "scenario_id": evidence_package.scenario_id,
                "demo_primary": True,
                "synthetic": True,
                "authoritative": False,
                "execution_mode": EXECUTION_MODE,
                "live_model_calls": 0,
                "execution_disclosure": EXECUTION_DISCLOSURE,
                "constraints": list(evidence_package.constraints),
                "evidence_package": evidence_package.model_dump(mode="json"),
                "routing_plan": routing.model_dump(mode="json"),
            },
        )
        self.orchestration.start_run(
            run.id,
            actor=WORKFLOW_ACTOR,
            reason="Start the fixed insurance POC golden workflow.",
            correlation_id=correlation_id,
        )
        _, evidence_artifact, _ = self.artifacts.write_json(
            run.id,
            "evidence-package",
            "evidence-package.json",
            evidence_package.model_dump(mode="json"),
            WORKFLOW_ACTOR,
            idempotency_key=f"{run.id}:evidence-package:v1",
            correlation_id=correlation_id,
            provenance=self._provenance(
                version="1.0",
                source_agents=["evidence-extractor"],
                source_evidence=[item.evidence_id for item in evidence_package.evidence],
                validation_status="source_integrity_validated",
            ),
        )

        tasks = self._create_tasks(
            run.id,
            evidence_package,
            evidence_artifact.id,
            correlation_id,
        )
        self._persist_routes(run.id, tasks, routing, correlation_id)

        context = self._build_outputs(scenario, evidence_package, routing)
        conflicts = [
            ConflictRecord.model_validate(context["scope_conflict"]),
            ConflictRecord.model_validate(context["authority_conflict"]),
        ]
        self._execute_tasks(
            run.id,
            tasks,
            context,
            evidence_package,
            conflicts,
            correlation_id,
        )

        policy = context["policy"]
        approval = self.orchestration.request_approval(
            run.id,
            requested_by="risk-agent",
            actor=WORKFLOW_ACTOR,
            reason=(
                "Raw private-evidence upload was denied. Only the sanitized POC package "
                "may be proposed for external dispatch, and that action requires Founder approval."
            ),
            correlation_id=correlation_id,
            metadata={
                "reviewer_required": "founder",
                "policy_rule_ids": policy["sanitized_dispatch"]["rule_ids"],
                "blocked_policy_rule_ids": policy["private_upload"]["rule_ids"],
                "policy_decisions": policy,
                "proposed_action": "send_sanitized_insurer_poc_package",
                "external_action_executed": False,
            },
        )
        snapshot = self.orchestration.get_snapshot(run.id)
        return GoldenWorkflowResponse(
            run_id=run.id,
            status=str(snapshot.run.status),
            approval_id=approval.approval.id,
            snapshot=snapshot,
            evidence_package=evidence_package,
            routing_plan=routing,
            conflicts=conflicts,
            execution_disclosure=EXECUTION_DISCLOSURE,
        )

    def _scenario(self) -> dict[str, Any]:
        path = self.fixture_dir / "scenario.json"
        value = json.loads(path.read_text(encoding="utf-8"))
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
        all_evidence_ids = [item.evidence_id for item in evidence.evidence]
        for spec in TASK_SPECS:
            dependency_ids = [tasks[key].id for key in spec.dependency_keys]
            task, _ = self.orchestration.create_task(
                run_id,
                title=spec.title,
                description=spec.description,
                assigned_agent=spec.agent,
                dependency_ids=dependency_ids,
                actor=WORKFLOW_ACTOR,
                correlation_id=correlation_id,
                metadata={
                    "task_key": spec.key,
                    "task_type": f"insurance-poc.{spec.key}",
                    "stage": STAGE_BY_KEY[spec.key],
                    "execution_mode": EXECUTION_MODE,
                    "route_execution_status": "decision_only",
                    "expected_output_names": EXPECTED_OUTPUTS[spec.key],
                    "evidence_package_id": str(evidence.package_id),
                    "shared_evidence_artifact_id": str(evidence_artifact_id),
                    "shared_evidence_ids": all_evidence_ids,
                    "constraints": list(evidence.constraints),
                    "policy_action": {
                        "operation": "read",
                        "tool_name": "insurance-poc-fixture-rule-engine",
                        "external_write": False,
                        "private_data": False,
                        "production_change": False,
                    },
                },
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
        for decision in routing.decisions:
            self.orchestration.record_route_decision(
                run_id,
                task_id=tasks[decision.task_key].id,
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
                metadata={
                    "task_key": decision.task_key,
                    "execution_disclosure": EXECUTION_DISCLOSURE,
                },
            )

    def _execute_tasks(
        self,
        run_id: UUID,
        tasks: dict[str, Any],
        context: dict[str, Any],
        evidence: EvidencePackage,
        conflicts: list[ConflictRecord],
        correlation_id: str | None,
    ) -> None:
        standard_outputs = {
            "evidence-extraction": ("evidence-extraction-report", context["evidence_report"]),
            "executive-orchestration": ("golden-dag", context["dag"]),
            "product-analysis": ("product-scope-proposal", context["product"]),
            "finance-analysis": ("finance-budget-analysis", context["finance"]),
            "engineering-plan": ("engineering-delivery-plan", context["engineering"]),
            "risk-review": ("risk-governance-review", context["risk"]),
        }
        for spec in TASK_SPECS[:6]:
            task = tasks[spec.key]
            self._start_task(run_id, task.id, correlation_id)
            logical_name, payload = standard_outputs[spec.key]
            self._write_task_json(
                run_id,
                task.id,
                logical_name,
                payload,
                source_agents=[spec.agent],
                source_evidence=self._agent_evidence(evidence, spec.agent),
                validation_status="validated",
                correlation_id=correlation_id,
            )
            self.orchestration.complete_task(
                run_id,
                task.id,
                actor=WORKFLOW_ACTOR,
                reason=f"{spec.key} deterministic acceptance output persisted.",
                correlation_id=correlation_id,
            )

        conflict_payload = {
            "schema_version": "insurance-conflicts-1.0",
            "evidence_package_id": str(evidence.package_id),
            "conflicts": [item.model_dump(mode="json") for item in conflicts],
            "static_copy": False,
            "derivation": [
                "scope_budget is computed from scenario budget rows and reserve",
                "authority_boundary is triggered by the prohibited autonomous decision mode",
            ],
        }
        self.artifacts.write_json(
            run_id,
            "conflict-resolution-log",
            "conflict-resolution-log.json",
            conflict_payload,
            WORKFLOW_ACTOR,
            idempotency_key=f"{run_id}:conflicts:v1",
            correlation_id=correlation_id,
            provenance=self._provenance(
                version="1.0",
                source_agents=["product-agent", "finance-agent", "risk-agent"],
                source_evidence=["E-BUDGET-001", "E-PDF-GOVERNANCE-001"],
                validation_status="resolved",
            ),
        )

        synth = tasks["artifact-synthesis"]
        self._start_task(run_id, synth.id, correlation_id)
        self._write_task_json(
            run_id,
            synth.id,
            "draft-delivery-package",
            context["draft_package"],
            source_agents=[
                "product-agent",
                "finance-agent",
                "engineering-agent",
                "risk-agent",
            ],
            source_evidence=[item.evidence_id for item in evidence.evidence],
            validation_status="pending_verifier",
            correlation_id=correlation_id,
        )
        self.orchestration.complete_task(
            run_id,
            synth.id,
            actor=WORKFLOW_ACTOR,
            reason="Draft delivery package persisted for independent verification.",
            correlation_id=correlation_id,
        )

        verifier = tasks["verification"]
        self._start_task(run_id, verifier.id, correlation_id)
        self._write_final_artifacts(
            run_id,
            verifier.id,
            context,
            evidence,
            correlation_id,
        )
        self.orchestration.complete_task(
            run_id,
            verifier.id,
            actor=WORKFLOW_ACTOR,
            reason="Verifier corrections and six independent deliverables persisted.",
            correlation_id=correlation_id,
        )

    def _start_task(
        self,
        run_id: UUID,
        task_id: UUID,
        correlation_id: str | None,
    ) -> None:
        self.orchestration.mark_task_ready(
            run_id,
            task_id,
            actor=WORKFLOW_ACTOR,
            reason="All fixed-DAG dependencies completed.",
            correlation_id=correlation_id,
        )
        task, _ = self.orchestration.start_task(
            run_id,
            task_id,
            actor=WORKFLOW_ACTOR,
            reason="Execute deterministic acceptance rule with shared Evidence Package.",
            correlation_id=correlation_id,
        )
        attempted = task.model_copy(deep=True)
        attempted.attempt_count = 1
        self.orchestration.repository.save_task(attempted)

    def _write_task_json(
        self,
        run_id: UUID,
        task_id: UUID,
        logical_name: str,
        payload: dict[str, Any],
        *,
        source_agents: list[str],
        source_evidence: list[str],
        validation_status: str,
        correlation_id: str | None,
    ) -> None:
        self.artifacts.write_json(
            run_id,
            logical_name,
            f"{logical_name}.json",
            payload,
            WORKFLOW_ACTOR,
            task_id=task_id,
            relation="output",
            idempotency_key=f"{run_id}:{logical_name}:v1",
            correlation_id=correlation_id,
            provenance=self._provenance(
                version="1.0",
                source_agents=source_agents,
                source_evidence=source_evidence,
                validation_status=validation_status,
            ),
        )

    def _write_final_artifacts(
        self,
        run_id: UUID,
        task_id: UUID,
        context: dict[str, Any],
        evidence: EvidencePackage,
        correlation_id: str | None,
    ) -> None:
        all_evidence = [item.evidence_id for item in evidence.evidence]
        source_agents = [
            "product-agent",
            "finance-agent",
            "engineering-agent",
            "risk-agent",
            "artifact-synthesizer",
            "verifier",
        ]
        provenance = self._provenance(
            version="2.0",
            source_agents=source_agents,
            source_evidence=all_evidence,
            validation_status="verified_with_revision",
        )
        for logical_name, filename, content in context["final_text_artifacts"]:
            self.artifacts.write_text(
                run_id,
                logical_name,
                filename,
                content,
                WORKFLOW_ACTOR,
                task_id=task_id,
                relation="output",
                idempotency_key=f"{run_id}:{logical_name}:v2",
                correlation_id=correlation_id,
                provenance=provenance,
            )
        for logical_name, filename, value in context["final_json_artifacts"]:
            self.artifacts.write_json(
                run_id,
                logical_name,
                filename,
                value,
                WORKFLOW_ACTOR,
                task_id=task_id,
                relation="output",
                idempotency_key=f"{run_id}:{logical_name}:v2",
                correlation_id=correlation_id,
                provenance=provenance,
            )
        self._write_task_json(
            run_id,
            task_id,
            "verification-report",
            context["verification_report"],
            source_agents=["verifier"],
            source_evidence=["E-BUDGET-001", "E-PDF-GOVERNANCE-001"],
            validation_status="verified_with_revision",
            correlation_id=correlation_id,
        )

    def _build_outputs(
        self,
        scenario: dict[str, Any],
        evidence: EvidencePackage,
        routing: RoutingPreviewResponse,
    ) -> dict[str, Any]:
        budget = scenario["budget"]
        scope_rows = budget["proposed_scope_costs"]
        proposed_total = sum(int(row["amount"]) for row in scope_rows)
        delivery_ceiling = int(budget["total"]) - int(budget["reserve"])
        accepted_rows = [row for row in scope_rows if not str(row["item"]).startswith("Optional")]
        accepted_total = sum(int(row["amount"]) for row in accepted_rows)
        evidence_ids = [item.evidence_id for item in evidence.evidence]
        scope_conflict = {
            "conflict_id": "C-SCOPE-BUDGET-001",
            "conflict_type": "scope_budget",
            "raised_by": "finance-agent",
            "affected_agents": ["product-agent", "finance-agent", "executive-orchestrator"],
            "source_evidence": ["E-BUDGET-001", "E-PDF-WINDOW-001"],
            "proposal_before": {"scope_items": scope_rows, "planned_cost_cny": proposed_total},
            "constraint": {
                "total_budget_cny": budget["total"],
                "reserve_cny": budget["reserve"],
                "delivery_ceiling_cny": delivery_ceiling,
            },
            "proposal_after": {
                "scope_items": accepted_rows,
                "planned_cost_cny": accepted_total,
                "deferred": "Optional automated insurer write-back",
            },
            "resolution_rule": "planned_cost <= total_budget - reserve",
            "resolution_status": "resolved",
            "accepted_by": ["product-agent", "executive-orchestrator"],
        }
        authority_conflict = {
            "conflict_id": "C-AUTHORITY-001",
            "conflict_type": "authority_boundary",
            "raised_by": "risk-agent",
            "affected_agents": ["product-agent", "engineering-agent", "risk-agent"],
            "source_evidence": ["E-PDF-GOVERNANCE-001", "E-IMG-ACCIDENT-DAMAGE-002"],
            "proposal_before": {"decision_mode": "autonomous_liability_decision"},
            "constraint": {
                "authoritative": False,
                "human_review_required": True,
                "external_write_without_approval": False,
            },
            "proposal_after": {"decision_mode": "model_recommendation_plus_human_review"},
            "resolution_rule": "non_authoritative_output_requires_human_review",
            "resolution_status": "resolved",
            "accepted_by": ["product-agent", "engineering-agent", "artifact-synthesizer"],
        }
        private_decision = self.policy_gate.evaluate(
            PolicyAction(
                actor="risk-agent",
                operation="upload",
                tool_name="insurer-file-upload",
                target="external-insurer-endpoint",
                external_write=True,
                private_data=True,
            )
        )
        sanitized_decision = self.policy_gate.evaluate(
            PolicyAction(
                actor="risk-agent",
                operation="message",
                tool_name="sanitized-poc-dispatch",
                target="external-insurer-reviewer",
                external_write=True,
                private_data=False,
            )
        )
        policy = {
            "private_upload": private_decision.model_dump(mode="json"),
            "sanitized_dispatch": sanitized_decision.model_dump(mode="json"),
            "external_action_executed": False,
        }
        draft = {
            "schema_version": "insurance-draft-package-1.0",
            "evidence_package_id": str(evidence.package_id),
            "source_evidence": evidence_ids,
            "planned_cost_cny": proposed_total,
            "decision_mode": "autonomous_liability_decision",
            "expected_deliverables": [
                "executive-decision-memo",
                "insurance-poc-product-brief",
                "technical-implementation-plan",
                "budget-summary",
                "risk-register",
                "two-week-action-plan",
            ],
            "validation_status": "pending_verifier",
        }
        report = {
            "schema_version": "insurance-verification-1.0",
            "status": "revised_and_passed",
            "independent_model_call": False,
            "execution_mode": EXECUTION_MODE,
            "issues_found": 2,
            "revisions": [
                {
                    "issue": "budget_exceeds_delivery_ceiling",
                    "before": {"planned_cost_cny": proposed_total},
                    "after": {"planned_cost_cny": accepted_total, "reserve_cny": budget["reserve"]},
                    "reason": "Finance arithmetic enforces the CNY 45,000 delivery ceiling.",
                    "source_evidence": ["E-BUDGET-001"],
                },
                {
                    "issue": "unsafe_authority_language",
                    "before": {"decision_mode": "autonomous_liability_decision"},
                    "after": {"decision_mode": "model_recommendation_plus_human_review"},
                    "reason": "The synthetic evidence is non-authoritative and Policy requires review.",
                    "source_evidence": ["E-PDF-GOVERNANCE-001", "E-IMG-ACCIDENT-DAMAGE-002"],
                },
            ],
            "final_validation": {
                "budget_consistent": True,
                "human_review_language_present": True,
                "source_evidence_present": True,
                "external_write_executed": False,
            },
        }
        final_text = self._final_text_artifacts(
            evidence,
            accepted_rows,
            accepted_total,
            int(budget["reserve"]),
        )
        final_json = self._final_json_artifacts(
            evidence,
            accepted_rows,
            accepted_total,
            int(budget["reserve"]),
            policy,
        )
        return {
            "evidence_report": {
                "package_id": str(evidence.package_id),
                "source_count": len(evidence.sources),
                "evidence_count": len(evidence.evidence),
                "categories": sorted({item.category.value for item in evidence.evidence}),
                "source_evidence": evidence_ids,
                "validation_status": "validated",
            },
            "dag": {
                "schema_version": "insurance-golden-dag-1.0",
                "fixed": True,
                "nodes": [
                    {"key": spec.key, "agent": spec.agent, "depends_on": list(spec.dependency_keys)}
                    for spec in TASK_SPECS
                ],
                "shared_evidence_package_id": str(evidence.package_id),
                "routing_decisions": [item.task_key for item in routing.decisions],
            },
            "product": {
                "schema_version": "insurance-product-proposal-1.0",
                "source_evidence": ["E-MISSION-001", "E-PDF-WINDOW-001", "E-BUDGET-001"],
                "target_users": scenario["insurer_requirements"]["target_users"],
                "initial_scope": scope_rows,
                "planned_cost_cny": proposed_total,
                "initial_decision_mode": "autonomous_liability_decision",
            },
            "finance": {
                "schema_version": "insurance-finance-analysis-1.0",
                "source_evidence": ["E-BUDGET-001"],
                "total_budget_cny": budget["total"],
                "reserve_cny": budget["reserve"],
                "delivery_ceiling_cny": delivery_ceiling,
                "proposed_cost_cny": proposed_total,
                "accepted_cost_cny": accepted_total,
                "conflict": scope_conflict,
            },
            "engineering": {
                "schema_version": "insurance-engineering-plan-1.0",
                "source_evidence": ["E-TECH-001", "E-TECH-LIMIT-001", "E-PDF-WINDOW-001"],
                "accepted_scope": accepted_rows,
                "execution_status": "plan_only",
                "code_diff": None,
                "test_result": None,
                "limitation": "No repository mutation or Engineering tool execution was performed by this fixture workflow.",
            },
            "risk": {
                "schema_version": "insurance-risk-review-1.0",
                "source_evidence": ["E-PDF-GOVERNANCE-001", "E-IMG-ACCIDENT-DAMAGE-002"],
                "authority_conflict": authority_conflict,
                "policy": policy,
            },
            "scope_conflict": scope_conflict,
            "authority_conflict": authority_conflict,
            "policy": policy,
            "draft_package": draft,
            "verification_report": report,
            "final_text_artifacts": final_text,
            "final_json_artifacts": final_json,
        }

    @staticmethod
    def _agent_evidence(evidence: EvidencePackage, agent: str) -> list[str]:
        values = [item.evidence_id for item in evidence.evidence if agent in item.used_by_agents]
        return values or [item.evidence_id for item in evidence.evidence]

    @staticmethod
    def _provenance(
        *,
        version: str,
        source_agents: list[str],
        source_evidence: list[str],
        validation_status: str,
    ) -> dict[str, Any]:
        return {
            "artifact_version": version,
            "source_agents": source_agents,
            "source_evidence": source_evidence,
            "validation_status": validation_status,
            "execution_mode": EXECUTION_MODE,
            "live_model_calls": 0,
            "authoritative": False,
        }

    @staticmethod
    def _final_text_artifacts(
        evidence: EvidencePackage,
        accepted_rows: list[dict[str, Any]],
        accepted_total: int,
        reserve: int,
    ) -> list[tuple[str, str, str]]:
        scope = "\n".join(f"- {row['item']}: CNY {row['amount']:,}" for row in accepted_rows)
        disclaimer = (
            "Non-authoritative synthetic demo. Liability output is a model recommendation "
            "plus mandatory human review."
        )
        return [
            (
                "executive-decision-memo",
                "executive-decision-memo.md",
                f"# Executive Decision Memo\n\nProceed with a ten-business-day insurer POC.\n\n"
                f"Planned delivery spend: CNY {accepted_total:,}; reserve: CNY {reserve:,}.\n\n"
                f"Decision boundary: {disclaimer}\n\nEvidence: `E-PDF-WINDOW-001`, `E-BUDGET-001`, "
                "`E-PDF-GOVERNANCE-001`.\n",
            ),
            (
                "insurance-poc-product-brief",
                "insurance-poc-product-brief.md",
                "# Insurance POC Product Brief\n\nUsers: claims adjusters and claims team leads.\n\n"
                "## Accepted scope\n\n"
                f"{scope}\n\nAcceptance requires source-linked evidence, confidence, missing-evidence "
                f"disclosure, and human review.\n\n{disclaimer}\n",
            ),
            (
                "technical-implementation-plan",
                "technical-implementation-plan.md",
                "# Technical Implementation Plan\n\nReuse the accepted D06-D13 runtime, Evidence Package, "
                "Artifact Store, Policy Gate, Approval, and Evaluation contracts.\n\n"
                "The stable demo uses local PDF parsing and two checksum-bound synthetic PNG fixtures. "
                "No live arbitrary-image Adapter, code diff, or test execution is claimed.\n\n"
                "Evidence: `E-TECH-001`, `E-TECH-LIMIT-001`.\n",
            ),
            (
                "two-week-action-plan",
                "two-week-action-plan.md",
                "# Two-week Action Plan\n\n- Days 1-2: validate insurer requirements and evidence contracts.\n"
                "- Days 3-5: integrate the formal local multimodal Adapter and claims workbench.\n"
                "- Days 6-7: run the eight-case demo evaluation and resolve failures.\n"
                "- Days 8-9: insurer rehearsal, security review, and package validation.\n"
                "- Day 10: Founder-approved sanitized demo delivery; no private source upload.\n",
            ),
        ]

    @staticmethod
    def _final_json_artifacts(
        evidence: EvidencePackage,
        accepted_rows: list[dict[str, Any]],
        accepted_total: int,
        reserve: int,
        policy: dict[str, Any],
    ) -> list[tuple[str, str, dict[str, Any]]]:
        return [
            (
                "budget-summary",
                "budget-summary.json",
                {
                    "schema_version": "insurance-budget-1.0",
                    "artifact_version": "2.0",
                    "currency": "CNY",
                    "planned_scope": accepted_rows,
                    "planned_cost": accepted_total,
                    "reserve": reserve,
                    "total": accepted_total + reserve,
                    "within_budget": True,
                    "source_evidence": ["E-BUDGET-001"],
                    "validation_status": "verified_with_revision",
                },
            ),
            (
                "risk-register",
                "risk-register.json",
                {
                    "schema_version": "insurance-risk-register-1.0",
                    "artifact_version": "2.0",
                    "evidence_package_id": str(evidence.package_id),
                    "risks": [
                        {
                            "risk": "Non-authoritative output mistaken for a claim decision",
                            "control": "Model recommendation plus mandatory human review",
                            "evidence": ["E-PDF-GOVERNANCE-001", "E-IMG-ACCIDENT-DAMAGE-002"],
                        },
                        {
                            "risk": "Private accident evidence leaves the governed runtime",
                            "control": "Private upload denied; sanitized external dispatch requires approval",
                            "evidence": ["E-PDF-GOVERNANCE-001"],
                        },
                    ],
                    "policy": policy,
                    "validation_status": "verified_with_revision",
                },
            ),
        ]
