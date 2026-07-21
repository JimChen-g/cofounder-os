"""Resumable, per-Agent execution adapters for the D14 insurance workflow.

Each task reads persisted upstream artifacts and writes its own validated output.
The runtime is deterministic while model services are unavailable, but it is an
actual Workflow Controller adapter: claims, attempts, retries, recovery, policy,
and completion remain owned by the accepted D06-D10 authorities.
"""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any
from uuid import UUID

from app.artifacts import FileArtifactStore
from app.clients import GatewayClient, GatewayClientError
from app.domain import AuditEvent, AuditOutcome, Task
from app.services.artifact_write import ArtifactRegistrationService
from app.services.orchestration import OrchestrationService, RunSnapshot

from .models import EvidencePackage
from .live_agents import (
    EngineeringPlanningAgent,
    LiveAgentCallEvidence,
    LiveAgentValidationFailure,
    RiskReviewAgent,
)


EXECUTION_BACKEND = "deterministic_local_agent"
LIVE_EXECUTION_BACKEND = "gateway_llm_agent"
EXECUTABLE_ROUTE_PROVIDERS = frozenset(
    {
        "dgx-local-adapter",
        "dgx-local-deterministic-agent",
        "qwen-local-dgx",
        "step-cloud",
    }
)
SAFE_DECISION_MODE = "model_recommendation_plus_human_review"

D14_EXPECTED_OUTPUTS: dict[str, frozenset[str]] = {
    "evidence-extraction": frozenset({"evidence-extraction-report"}),
    "executive-orchestration": frozenset({"golden-dag"}),
    "product-analysis": frozenset({"product-scope-proposal"}),
    "finance-analysis": frozenset({"finance-budget-analysis"}),
    "engineering-plan": frozenset({"engineering-delivery-plan"}),
    "risk-review": frozenset({"risk-governance-review"}),
    "private-upload-policy": frozenset(),
    "artifact-synthesis": frozenset({"draft-delivery-package"}),
    "verification": frozenset(
        {
            "executive-decision-memo",
            "insurance-poc-product-brief",
            "technical-implementation-plan",
            "budget-summary",
            "risk-register",
            "two-week-action-plan",
            "verification-report",
            "conflict-resolution-log",
        }
    ),
    "release-approval": frozenset({"governed-release-receipt"}),
}


class InsurancePOCTaskRuntime:
    """Execute D14 tasks from persisted inputs instead of prebuilding results."""

    def __init__(
        self,
        *,
        orchestration: OrchestrationService,
        artifact_store: FileArtifactStore,
        gateway: GatewayClient | None = None,
    ) -> None:
        self.orchestration = orchestration
        self.artifact_store = artifact_store
        self.writer = ArtifactRegistrationService(artifact_store, orchestration)
        self.gateway = gateway
        self.engineering_llm = EngineeringPlanningAgent(gateway) if gateway else None
        self.risk_llm = RiskReviewAgent(gateway) if gateway else None
        self._handlers: dict[str, Callable[[Task, RunSnapshot, str | None], None]] = {
            "evidence-extraction": self._evidence,
            "executive-orchestration": self._executive,
            "product-analysis": self._product,
            "finance-analysis": self._finance,
            "engineering-plan": self._engineering,
            "risk-review": self._risk,
            "artifact-synthesis": self._synthesis,
            "verification": self._verification,
            "release-approval": self._release,
        }

    def supports(self, task: Task) -> bool:
        task_type = task.metadata.get("task_type")
        return isinstance(task_type, str) and task_type.startswith("insurance-poc.")

    def expected_outputs(self, task: Task) -> frozenset[str] | None:
        key = task.metadata.get("task_key")
        return D14_EXPECTED_OUTPUTS.get(str(key))

    async def dispatch(
        self,
        task: Task,
        snapshot: RunSnapshot,
        correlation_id: str | None,
    ) -> None:
        current = next(item for item in snapshot.tasks if item.id == task.id)
        key = str(current.metadata.get("task_key"))
        matching_routes = [
            route for route in snapshot.route_decisions if route.task_id == current.id
        ]
        if len(matching_routes) != 1:
            raise RuntimeError(f"D14 task {key} requires exactly one persisted route")
        route = matching_routes[0]
        if route.provider not in EXECUTABLE_ROUTE_PROVIDERS:
            raise RuntimeError(
                f"D14 task {key} cannot auto-execute route provider "
                f"{route.provider}"
            )
        failure_key = snapshot.run.metadata.get("failure_injection_task")
        if failure_key == key and current.attempt_count == 1:
            raise RuntimeError(f"Injected recoverable D14 failure for {key}")
        started = time.perf_counter()
        backend = EXECUTION_BACKEND
        fallback_used: bool | None = None
        execution_metadata: dict[str, Any] = {
            "planned_model": route.selected_model,
            "planned_provider": route.provider,
        }
        live_route = route.provider in {"qwen-local-dgx", "step-cloud"}
        try:
            if live_route and key == "engineering-plan":
                call = await self._engineering_live(
                    current,
                    snapshot,
                    route.selected_model,
                    correlation_id,
                )
                backend = LIVE_EXECUTION_BACKEND
                execution_metadata.update(call.model_dump(mode="json"))
                fallback_used = call.fallback_used
            elif live_route and key == "risk-review":
                call = await self._risk_live(
                    current,
                    snapshot,
                    route.selected_model,
                    correlation_id,
                )
                backend = LIVE_EXECUTION_BACKEND
                execution_metadata.update(call.model_dump(mode="json"))
                fallback_used = call.fallback_used
            else:
                if live_route:
                    raise RuntimeError(
                        f"D15 live routing is implemented only for Engineering and Risk, not {key}"
                    )
                self._handler(key)(current, snapshot, correlation_id)
        except (GatewayClientError, LiveAgentValidationFailure, ValueError) as exc:
            if not live_route or key not in {"engineering-plan", "risk-review"}:
                raise
            self._handler(key)(current, snapshot, correlation_id)
            fallback_used = True
            failed_call = (
                exc.call_evidence
                if isinstance(exc, LiveAgentValidationFailure)
                else None
            )
            if failed_call is not None:
                execution_metadata.update(failed_call.model_dump(mode="json"))
            execution_metadata.update(
                {
                    "live_call_succeeded": False,
                    "fallback_model": f"{current.assigned_agent}-local",
                    "fallback_reason": str(exc),
                }
            )
        latency_ms = (time.perf_counter() - started) * 1_000
        self.orchestration.mark_route_executed(
            current.run_id,
            current.id,
            actor=current.assigned_agent or "insurance-poc-runtime",
            execution_backend=backend,
            correlation_id=correlation_id,
            latency_ms=latency_ms,
            fallback_used=fallback_used,
            execution_metadata=execution_metadata,
        )
        self._append_execution_event(
            current,
            key,
            correlation_id,
            backend=backend,
            execution_metadata=execution_metadata,
        )

    def _handler(self, key: str) -> Callable[[Task, RunSnapshot, str | None], None]:
        try:
            return self._handlers[key]
        except KeyError as exc:
            raise RuntimeError(f"No D14 runtime handler for {key}") from exc

    def _evidence(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        payload = {
            "schema_version": "insurance-evidence-execution-2.0",
            "package_id": str(evidence.package_id),
            "source_count": len(evidence.sources),
            "evidence_count": len(evidence.evidence),
            "categories": sorted({item.category.value for item in evidence.evidence}),
            "source_evidence": [item.evidence_id for item in evidence.evidence],
            "adapter_modes": sorted({item.adapter_mode for item in evidence.evidence}),
            "execution_backend": EXECUTION_BACKEND,
            "validation_status": "validated",
        }
        self._write_json(task, "evidence-extraction-report", payload, evidence, correlation_id)

    def _executive(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        tasks = sorted(
            snapshot.tasks,
            key=lambda item: (int(item.metadata.get("stage", 0)), item.created_at, str(item.id)),
        )
        payload = {
            "schema_version": "insurance-golden-dag-2.0",
            "fixed": True,
            "nodes": [
                {
                    "key": item.metadata.get("task_key"),
                    "agent": item.assigned_agent,
                    "depends_on_task_ids": [str(value) for value in item.dependency_ids],
                    "stage": item.metadata.get("stage"),
                }
                for item in tasks
            ],
            "shared_evidence_package_id": str(evidence.package_id),
            "source_evidence": [item.evidence_id for item in evidence.evidence],
            "execution_backend": EXECUTION_BACKEND,
        }
        self._write_json(task, "golden-dag", payload, evidence, correlation_id)

    def _product(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        scenario = self._scenario(snapshot)
        budget = self._dict(scenario["budget"], "budget")
        scope_rows = self._dict_list(budget["proposed_scope_costs"], "proposed_scope_costs")
        payload = {
            "schema_version": "insurance-product-proposal-2.0",
            "mission": evidence.mission,
            "target_users": self._dict(scenario["insurer_requirements"], "insurer_requirements")[
                "target_users"
            ],
            "scope_items": scope_rows,
            "planned_cost_cny": sum(int(row["amount"]) for row in scope_rows),
            "requested_decision_mode": scenario.get(
                "requested_decision_mode",
                "autonomous_liability_decision",
            ),
            "acceptance": self._dict(
                scenario["insurer_requirements"], "insurer_requirements"
            )["required_capabilities"],
            "source_evidence": self._used_by(evidence, "product-agent"),
            "execution_backend": EXECUTION_BACKEND,
        }
        self._write_json(task, "product-scope-proposal", payload, evidence, correlation_id)

    def _finance(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        scenario = self._scenario(snapshot)
        budget = self._dict(scenario["budget"], "budget")
        rows = self._dict_list(budget["proposed_scope_costs"], "proposed_scope_costs")
        total_budget = int(budget["total"])
        reserve = int(budget["reserve"])
        delivery_ceiling = total_budget - reserve
        proposed_total = sum(int(row["amount"]) for row in rows)
        accepted = [row for row in rows if not bool(row.get("optional", False))]
        while sum(int(row["amount"]) for row in accepted) > delivery_ceiling and accepted:
            accepted.pop()
        accepted_total = sum(int(row["amount"]) for row in accepted)
        deferred = [row for row in rows if row not in accepted]
        conflict = {
            "conflict_id": "C-SCOPE-BUDGET-001",
            "conflict_type": "scope_budget",
            "raised_by": "finance-agent",
            "affected_agents": ["product-agent", "finance-agent", "executive-orchestrator"],
            "source_evidence": ["E-BUDGET-001", "E-PDF-WINDOW-001"],
            "proposal_before": {"scope_items": rows, "planned_cost_cny": proposed_total},
            "constraint": {
                "total_budget_cny": total_budget,
                "reserve_cny": reserve,
                "delivery_ceiling_cny": delivery_ceiling,
            },
            "proposal_after": {
                "scope_items": accepted,
                "planned_cost_cny": accepted_total,
                "deferred": deferred,
            },
            "resolution_rule": "planned_cost <= total_budget - reserve",
            "resolution_status": "resolved" if proposed_total > delivery_ceiling else "not_required",
            "accepted_by": ["finance-agent"],
        }
        payload = {
            "schema_version": "insurance-finance-analysis-2.0",
            "total_budget_cny": total_budget,
            "reserve_cny": reserve,
            "delivery_ceiling_cny": delivery_ceiling,
            "proposed_cost_cny": proposed_total,
            "accepted_cost_cny": accepted_total,
            "accepted_scope": accepted,
            "deferred_scope": deferred,
            "conflict": conflict,
            "source_evidence": self._used_by(evidence, "finance-agent"),
            "execution_backend": EXECUTION_BACKEND,
        }
        self._write_json(task, "finance-budget-analysis", payload, evidence, correlation_id)

    async def _engineering_live(
        self,
        task: Task,
        snapshot: RunSnapshot,
        virtual_model: str,
        correlation_id: str | None,
    ) -> LiveAgentCallEvidence:
        if self.engineering_llm is None:
            raise GatewayClientError("Engineering live Agent has no configured Gateway")
        evidence = self._evidence_package(snapshot)
        product = self._dependency_json(snapshot, task, "product-scope-proposal")
        finance = self._dependency_json(snapshot, task, "finance-budget-analysis")
        scenario = self._scenario(snapshot)
        project_status = self._dict(scenario["project_status"], "project_status")
        result, call = await self.engineering_llm.execute(
            virtual_model=virtual_model,
            evidence=evidence,
            product=product,
            finance=finance,
            project_status=project_status,
        )
        payload = {
            **result.model_dump(mode="json"),
            "accepted_scope": finance["accepted_scope"],
            "execution_backend": LIVE_EXECUTION_BACKEND,
            "model_call": call.model_dump(mode="json"),
        }
        self._write_json(
            task,
            "engineering-delivery-plan",
            payload,
            evidence,
            correlation_id,
            live_call=call,
        )
        return call

    def _engineering(
        self,
        task: Task,
        snapshot: RunSnapshot,
        correlation_id: str | None,
    ) -> None:
        evidence = self._evidence_package(snapshot)
        finance = self._dependency_json(snapshot, task, "finance-budget-analysis")
        scenario = self._scenario(snapshot)
        project_status = self._dict(scenario["project_status"], "project_status")
        payload = {
            "schema_version": "insurance-engineering-plan-2.0",
            "accepted_scope": finance["accepted_scope"],
            "reused_capabilities": project_status["available"],
            "limitations": project_status["missing_or_limited"],
            "execution_status": "plan_only",
            "code_diff": None,
            "test_result": None,
            "two_week_sequence": [
                "evidence and Adapter integration",
                "claims workbench and audit package",
                "evaluation, security review, and rehearsal",
            ],
            "source_evidence": self._used_by(evidence, "engineering-agent"),
            "execution_backend": EXECUTION_BACKEND,
        }
        self._write_json(task, "engineering-delivery-plan", payload, evidence, correlation_id)

    async def _risk_live(
        self,
        task: Task,
        snapshot: RunSnapshot,
        virtual_model: str,
        correlation_id: str | None,
    ) -> LiveAgentCallEvidence:
        if self.risk_llm is None:
            raise GatewayClientError("Risk live Agent has no configured Gateway")
        evidence = self._evidence_package(snapshot)
        product = self._dependency_json(snapshot, task, "product-scope-proposal")
        finance = self._dependency_json(snapshot, task, "finance-budget-analysis")
        result, call = await self.risk_llm.execute(
            virtual_model=virtual_model,
            evidence=evidence,
            product=product,
            finance=finance,
        )
        requested_mode = str(product["requested_decision_mode"])
        human_review_required = any(
            "human" in item.content.lower() and "review" in item.content.lower()
            for item in evidence.evidence
            if item.category.value == "compliance/constraint"
        )
        accepted_mode = (
            SAFE_DECISION_MODE
            if human_review_required or not evidence.authoritative
            else result.recommended_decision_mode
        )
        conflict = self._authority_conflict(
            evidence,
            requested_mode=requested_mode,
            accepted_mode=accepted_mode,
            human_review_required=human_review_required,
        )
        payload = {
            "schema_version": "insurance-risk-review-3.0",
            "accepted_decision_mode": accepted_mode,
            "authority_conflict": conflict,
            "llm_review": result.model_dump(mode="json"),
            "policy_override_applied": (
                accepted_mode != result.recommended_decision_mode
                or result.private_upload_allowed
                or not result.required_human_approval
            ),
            "proposed_private_action": {
                "operation": "upload",
                "tool_name": "insurer-file-upload",
                "target": "external-insurer-endpoint",
                "external_write": True,
                "private_data": True,
            },
            "proposed_sanitized_action": {
                "operation": "message",
                "tool_name": "sanitized-poc-dispatch",
                "target": "external-insurer-reviewer",
                "external_write": True,
                "private_data": False,
            },
            "source_evidence": self._used_by(evidence, "risk-agent"),
            "execution_backend": LIVE_EXECUTION_BACKEND,
            "model_call": call.model_dump(mode="json"),
        }
        self._write_json(
            task,
            "risk-governance-review",
            payload,
            evidence,
            correlation_id,
            live_call=call,
        )
        return call

    def _risk(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        product = self._dependency_json(snapshot, task, "product-scope-proposal")
        requested_mode = str(product["requested_decision_mode"])
        human_review_required = any(
            "human" in item.content.lower() and "review" in item.content.lower()
            for item in evidence.evidence
            if item.category.value == "compliance/constraint"
        )
        accepted_mode = (
            SAFE_DECISION_MODE
            if human_review_required or not evidence.authoritative
            else requested_mode
        )
        conflict = self._authority_conflict(
            evidence,
            requested_mode=requested_mode,
            accepted_mode=accepted_mode,
            human_review_required=human_review_required,
        )
        payload = {
            "schema_version": "insurance-risk-review-2.0",
            "accepted_decision_mode": accepted_mode,
            "authority_conflict": conflict,
            "proposed_private_action": {
                "operation": "upload",
                "tool_name": "insurer-file-upload",
                "target": "external-insurer-endpoint",
                "external_write": True,
                "private_data": True,
            },
            "proposed_sanitized_action": {
                "operation": "message",
                "tool_name": "sanitized-poc-dispatch",
                "target": "external-insurer-reviewer",
                "external_write": True,
                "private_data": False,
            },
            "source_evidence": self._used_by(evidence, "risk-agent"),
            "execution_backend": EXECUTION_BACKEND,
        }
        self._write_json(task, "risk-governance-review", payload, evidence, correlation_id)

    def _authority_conflict(
        self,
        evidence: EvidencePackage,
        *,
        requested_mode: str,
        accepted_mode: str,
        human_review_required: bool,
    ) -> dict[str, Any]:
        return {
            "conflict_id": "C-AUTHORITY-001",
            "conflict_type": "authority_boundary",
            "raised_by": "risk-agent",
            "affected_agents": ["product-agent", "engineering-agent", "risk-agent"],
            "source_evidence": self._used_by(evidence, "risk-agent"),
            "proposal_before": {"decision_mode": requested_mode},
            "constraint": {
                "authoritative": evidence.authoritative,
                "human_review_required": human_review_required,
                "external_write_without_approval": False,
            },
            "proposal_after": {"decision_mode": accepted_mode},
            "resolution_rule": "non_authoritative_output_requires_human_review",
            "resolution_status": "resolved" if accepted_mode != requested_mode else "not_required",
            "accepted_by": ["risk-agent", "deterministic-policy-gate"],
        }

    def _synthesis(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        product = self._dependency_json(snapshot, task, "product-scope-proposal")
        finance = self._dependency_json(snapshot, task, "finance-budget-analysis")
        engineering = self._dependency_json(snapshot, task, "engineering-delivery-plan")
        risk = self._dependency_json(snapshot, task, "risk-governance-review")
        payload = {
            "schema_version": "insurance-draft-package-2.0",
            "evidence_package_id": str(evidence.package_id),
            "planned_cost_cny": product["planned_cost_cny"],
            "decision_mode": product["requested_decision_mode"],
            "product_proposal": product,
            "finance_review": finance,
            "engineering_plan": engineering,
            "risk_review": risk,
            "validation_status": "pending_independent_verifier",
            "source_evidence": [item.evidence_id for item in evidence.evidence],
            "execution_backend": EXECUTION_BACKEND,
        }
        self._write_json(task, "draft-delivery-package", payload, evidence, correlation_id)

    def _verification(
        self,
        task: Task,
        snapshot: RunSnapshot,
        correlation_id: str | None,
    ) -> None:
        evidence = self._evidence_package(snapshot)
        draft = self._dependency_json(snapshot, task, "draft-delivery-package")
        finance = self._dict(draft["finance_review"], "finance_review")
        risk = self._dict(draft["risk_review"], "risk_review")
        engineering = self._dict(draft["engineering_plan"], "engineering_plan")
        revisions: list[dict[str, Any]] = []
        final_cost = int(draft["planned_cost_cny"])
        accepted_cost = int(finance["accepted_cost_cny"])
        if final_cost != accepted_cost:
            revisions.append(
                {
                    "issue": "budget_exceeds_delivery_ceiling",
                    "before": {"planned_cost_cny": final_cost},
                    "after": {
                        "planned_cost_cny": accepted_cost,
                        "reserve_cny": finance["reserve_cny"],
                    },
                    "reason": "Persisted Finance output controls the delivery ceiling.",
                    "source_evidence": ["E-BUDGET-001"],
                }
            )
            final_cost = accepted_cost
        final_mode = str(draft["decision_mode"])
        accepted_mode = str(risk["accepted_decision_mode"])
        if final_mode != accepted_mode:
            revisions.append(
                {
                    "issue": "unsafe_authority_language",
                    "before": {"decision_mode": final_mode},
                    "after": {"decision_mode": accepted_mode},
                    "reason": "Persisted Risk output requires non-authoritative human review.",
                    "source_evidence": risk["source_evidence"],
                }
            )
            final_mode = accepted_mode
        report = {
            "schema_version": "insurance-verification-2.0",
            "status": "revised_and_passed" if revisions else "passed_without_revision",
            "independent_model_call": False,
            "execution_backend": EXECUTION_BACKEND,
            "issues_found": len(revisions),
            "revisions": revisions,
            "final_validation": {
                "budget_consistent": final_cost <= int(finance["delivery_ceiling_cny"]),
                "human_review_language_present": final_mode == SAFE_DECISION_MODE,
                "source_evidence_present": bool(draft["source_evidence"]),
                "external_write_executed": False,
            },
        }
        conflicts = [finance["conflict"], risk["authority_conflict"]]
        self._write_json(
            task,
            "conflict-resolution-log",
            {
                "schema_version": "insurance-conflicts-2.0",
                "conflicts": conflicts,
                "static_copy": False,
                "derived_from_artifacts": [
                    "product-scope-proposal",
                    "finance-budget-analysis",
                    "risk-governance-review",
                ],
            },
            evidence,
            correlation_id,
        )
        self._write_final_artifacts(
            task,
            evidence,
            finance,
            engineering,
            risk,
            report,
            final_cost,
            final_mode,
            correlation_id,
        )
        self._write_json(task, "verification-report", report, evidence, correlation_id)

    def _release(self, task: Task, snapshot: RunSnapshot, correlation_id: str | None) -> None:
        evidence = self._evidence_package(snapshot)
        verification = self._dependency_json(snapshot, task, "verification-report")
        payload = {
            "schema_version": "insurance-release-receipt-1.0",
            "approval_verified_by_controller": True,
            "verification_status": verification["status"],
            "release_scope": "sanitized_demo_package_only",
            "external_action_executed": False,
            "execution_backend": EXECUTION_BACKEND,
            "source_evidence": ["E-PDF-GOVERNANCE-001"],
        }
        self._write_json(task, "governed-release-receipt", payload, evidence, correlation_id)

    def _write_final_artifacts(
        self,
        task: Task,
        evidence: EvidencePackage,
        finance: dict[str, Any],
        engineering: dict[str, Any],
        risk: dict[str, Any],
        report: dict[str, Any],
        final_cost: int,
        final_mode: str,
        correlation_id: str | None,
    ) -> None:
        accepted_scope = self._dict_list(finance["accepted_scope"], "accepted_scope")
        scope = "\n".join(
            f"- {row['item']}: CNY {int(row['amount']):,}" for row in accepted_scope
        )
        disclaimer = (
            "Non-authoritative synthetic demo. Liability output is a model recommendation "
            "plus mandatory human review."
        )
        text_artifacts = [
            (
                "executive-decision-memo",
                "executive-decision-memo.md",
                "# Executive Decision Memo\n\nProceed with a ten-business-day insurer POC.\n\n"
                f"Planned delivery spend: CNY {final_cost:,}; reserve: "
                f"CNY {int(finance['reserve_cny']):,}.\n\nDecision mode: {final_mode}.\n\n"
                f"{disclaimer}\n",
            ),
            (
                "insurance-poc-product-brief",
                "insurance-poc-product-brief.md",
                "# Insurance POC Product Brief\n\n## Accepted scope\n\n"
                f"{scope}\n\nAcceptance requires traceable evidence and human review.\n\n"
                f"{disclaimer}\n",
            ),
            (
                "technical-implementation-plan",
                "technical-implementation-plan.md",
                "# Technical Implementation Plan\n\n"
                + "\n".join(f"- {item}" for item in engineering["two_week_sequence"])
                + "\n\nNo code diff or test success is claimed by this planning-only task.\n",
            ),
            (
                "two-week-action-plan",
                "two-week-action-plan.md",
                "# Two-week Action Plan\n\n- Days 1-2: evidence contracts.\n"
                "- Days 3-5: Adapter and workbench integration.\n"
                "- Days 6-9: evaluation, security review, and rehearsal.\n"
                "- Day 10: Founder-approved sanitized delivery.\n",
            ),
        ]
        for logical_name, filename, content in text_artifacts:
            self._write_text(
                task,
                logical_name,
                filename,
                content,
                evidence,
                correlation_id,
            )
        self._write_json(
            task,
            "budget-summary",
            {
                "schema_version": "insurance-budget-2.0",
                "planned_scope": accepted_scope,
                "planned_cost": final_cost,
                "reserve": finance["reserve_cny"],
                "total": final_cost + int(finance["reserve_cny"]),
                "within_budget": final_cost <= int(finance["delivery_ceiling_cny"]),
                "validation_status": report["status"],
            },
            evidence,
            correlation_id,
        )
        self._write_json(
            task,
            "risk-register",
            {
                "schema_version": "insurance-risk-register-2.0",
                "risks": [
                    {
                        "risk": "Non-authoritative output mistaken for a claim decision",
                        "control": risk["accepted_decision_mode"],
                    },
                    {
                        "risk": "Private accident evidence leaves the governed runtime",
                        "control": "Private upload denied; sanitized release requires approval",
                    },
                ],
                "validation_status": report["status"],
            },
            evidence,
            correlation_id,
        )

    def _write_json(
        self,
        task: Task,
        logical_name: str,
        payload: dict[str, Any],
        evidence: EvidencePackage,
        correlation_id: str | None,
        live_call: LiveAgentCallEvidence | None = None,
    ) -> None:
        self.writer.write_json(
            task.run_id,
            logical_name,
            f"{logical_name}.json",
            payload,
            task.assigned_agent or "insurance-poc-runtime",
            task_id=task.id,
            relation="output",
            idempotency_key=f"{task.run_id}:{task.id}:{logical_name}:v2",
            correlation_id=correlation_id,
            provenance=self._provenance(task, evidence, live_call=live_call),
        )

    def _write_text(
        self,
        task: Task,
        logical_name: str,
        filename: str,
        content: str,
        evidence: EvidencePackage,
        correlation_id: str | None,
    ) -> None:
        self.writer.write_text(
            task.run_id,
            logical_name,
            filename,
            content,
            task.assigned_agent or "insurance-poc-runtime",
            task_id=task.id,
            relation="output",
            idempotency_key=f"{task.run_id}:{task.id}:{logical_name}:v2",
            correlation_id=correlation_id,
            provenance=self._provenance(task, evidence),
        )

    @staticmethod
    def _provenance(
        task: Task,
        evidence: EvidencePackage,
        *,
        live_call: LiveAgentCallEvidence | None = None,
    ) -> dict[str, Any]:
        task_key = task.metadata.get("task_key")
        source_agents = [task.assigned_agent or "insurance-poc-runtime"]
        validation_status = "agent_output_validated"
        if task_key == "verification":
            source_agents = [
                "product-agent",
                "finance-agent",
                "engineering-agent",
                "risk-agent",
                "artifact-synthesizer",
                "verifier",
            ]
            validation_status = "verified_with_revision"
        value: dict[str, Any] = {
            "artifact_version": "2.0",
            "source_agents": source_agents,
            "source_evidence": [item.evidence_id for item in evidence.evidence],
            "validation_status": validation_status,
            "execution_backend": (
                LIVE_EXECUTION_BACKEND if live_call is not None else EXECUTION_BACKEND
            ),
            "live_model_calls": live_call.call_count if live_call is not None else 0,
            "authoritative": False,
        }
        if live_call is not None:
            value["model_call"] = live_call.model_dump(mode="json")
        return value

    def _dependency_json(
        self,
        snapshot: RunSnapshot,
        task: Task,
        logical_name: str,
    ) -> dict[str, Any]:
        by_task_id = {candidate.id: candidate for candidate in snapshot.tasks}
        ancestor_ids: set[UUID] = set()
        pending = list(task.dependency_ids)
        while pending:
            dependency_id = pending.pop()
            if dependency_id in ancestor_ids:
                continue
            ancestor_ids.add(dependency_id)
            dependency = by_task_id.get(dependency_id)
            if dependency is None:
                raise RuntimeError(f"Missing dependency task {dependency_id}")
            pending.extend(dependency.dependency_ids)
        matches = [
            artifact
            for artifact in snapshot.artifacts
            if artifact.name == logical_name and artifact.task_id in ancestor_ids
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"{task.metadata.get('task_key')} requires one {logical_name} dependency output"
            )
        artifact = matches[0]
        stored = self.artifact_store.verify(
            artifact.run_id,
            str(artifact.metadata["logical_name"]),
            str(artifact.metadata["filename"]),
            artifact.task_id,
        )
        if stored.checksum_sha256 != artifact.checksum_sha256:
            raise RuntimeError(f"Dependency checksum mismatch for {logical_name}")
        value = self.artifact_store.read_json(
            artifact.run_id,
            str(artifact.metadata["logical_name"]),
            str(artifact.metadata["filename"]),
            artifact.task_id,
        )
        return self._dict(value, logical_name)

    @staticmethod
    def _evidence_package(snapshot: RunSnapshot) -> EvidencePackage:
        return EvidencePackage.model_validate(snapshot.run.metadata["evidence_package"])

    @staticmethod
    def _scenario(snapshot: RunSnapshot) -> dict[str, Any]:
        return InsurancePOCTaskRuntime._dict(
            snapshot.run.metadata["scenario_context"],
            "scenario_context",
        )

    @staticmethod
    def _used_by(evidence: EvidencePackage, agent: str) -> list[str]:
        values = [item.evidence_id for item in evidence.evidence if agent in item.used_by_agents]
        return values or [item.evidence_id for item in evidence.evidence]

    @staticmethod
    def _dict(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise RuntimeError(f"{name} must be an object")
        return value

    @staticmethod
    def _dict_list(value: Any, name: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise RuntimeError(f"{name} must be a list of objects")
        return value

    def _append_execution_event(
        self,
        task: Task,
        task_key: str,
        correlation_id: str | None,
        *,
        backend: str,
        execution_metadata: dict[str, Any],
    ) -> None:
        self.orchestration.repository.append_event(
            AuditEvent(
                run_id=task.run_id,
                task_id=task.id,
                event_type="agent.executed",
                actor=task.assigned_agent or "insurance-poc-runtime",
                action="produce_structured_output",
                target_type="task",
                target_id=str(task.id),
                outcome=AuditOutcome.SUCCESS,
                correlation_id=correlation_id,
                details={
                    "task_key": task_key,
                    "execution_backend": backend,
                    "live_model_calls": int(execution_metadata.get("call_count", 0)),
                    "selected_provider": execution_metadata.get("selected_provider"),
                    "selected_upstream_model": execution_metadata.get(
                        "selected_upstream_model"
                    ),
                    "request_id": execution_metadata.get("request_id"),
                    "fallback_reason": execution_metadata.get("fallback_reason"),
                },
            )
        )
