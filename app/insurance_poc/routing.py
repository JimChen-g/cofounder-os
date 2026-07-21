"""Explainable, deterministic model routing for the insurance POC workflow."""

from __future__ import annotations

from dataclasses import dataclass

from app.insurance_poc.models import (
    EvidencePackage,
    ExplainableRouteDecision,
    PrivacyLevel,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
    SourceModality,
)


QWEN = "cofounder-qwen"
STEP = "cofounder-step"
EVIDENCE_ADAPTER = "insurance-evidence-adapter-chain"
ENGINEERING = "engineering-execution-chain"
HUMAN_REVIEW = "human-review"


@dataclass(frozen=True)
class RouteRule:
    task_key: str
    task_title: str
    preferred_model: str
    fallback_model: str
    candidate_models: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    modalities: tuple[SourceModality, ...]
    privacy_level: PrivacyLevel
    complexity: str
    tool_requirement: str
    latency_budget_ms: float
    cost_budget_usd: float
    validation_requirement: str
    preferred_reason: str
    privacy_decision: str
    static_exclusions: tuple[tuple[str, str], ...] = ()


RULES = (
    RouteRule(
        task_key="evidence-extraction",
        task_title="Evidence Extraction",
        preferred_model=EVIDENCE_ADAPTER,
        fallback_model=HUMAN_REVIEW,
        candidate_models=(EVIDENCE_ADAPTER, QWEN, STEP, HUMAN_REVIEW),
        required_capabilities=("pdf_text_extraction", "image_evidence_normalization"),
        modalities=(SourceModality.DOCUMENT, SourceModality.IMAGE),
        privacy_level=PrivacyLevel.RESTRICTED,
        complexity="medium",
        tool_requirement="local parsers and checksum-bound fixture Adapter",
        latency_budget_ms=2_000,
        cost_budget_usd=0,
        validation_requirement="Evidence Package schema and source-integrity validation",
        preferred_reason=(
            "Raw restricted PDF/image bytes stay local; the fixed Adapter chain can "
            "validate source integrity without a live model call."
        ),
        privacy_decision="raw restricted inputs remain on the DGX Spark product plane",
        static_exclusions=(
            (QWEN, "Current Gateway chat contract does not accept raw image payloads."),
            (STEP, "Restricted raw inputs are not sent to a cloud provider."),
        ),
    ),
    RouteRule(
        task_key="executive-orchestration",
        task_title="Executive Orchestrator",
        preferred_model=STEP,
        fallback_model=QWEN,
        candidate_models=(STEP, QWEN, HUMAN_REVIEW),
        required_capabilities=("long_horizon_planning", "cross_functional_decomposition"),
        modalities=(SourceModality.STRUCTURED_DATA, SourceModality.TEXT),
        privacy_level=PrivacyLevel.INTERNAL,
        complexity="high",
        tool_requirement="bounded DAG materialization",
        latency_budget_ms=12_000,
        cost_budget_usd=0.08,
        validation_requirement="DAG schema, allowed-Agent registry, and dependency validation",
        preferred_reason=(
            "Complex cross-functional planning benefits from Step 3.7 after restricted "
            "evidence is reduced to sanitized Evidence IDs and constraints."
        ),
        privacy_decision="only sanitized Evidence IDs and minimum-necessary facts may leave DGX",
    ),
    RouteRule(
        task_key="product-analysis",
        task_title="Product scope and acceptance",
        preferred_model=STEP,
        fallback_model=QWEN,
        candidate_models=(STEP, QWEN, HUMAN_REVIEW),
        required_capabilities=("multimodal_evidence_synthesis", "product_scope_tradeoffs"),
        modalities=(SourceModality.STRUCTURED_DATA, SourceModality.TEXT),
        privacy_level=PrivacyLevel.INTERNAL,
        complexity="high",
        tool_requirement="structured Product proposal",
        latency_budget_ms=10_000,
        cost_budget_usd=0.06,
        validation_requirement="Product proposal schema and Evidence-ID citation coverage",
        preferred_reason=(
            "The Product task needs cross-source tradeoff reasoning; it receives sanitized "
            "facts, not the restricted source files."
        ),
        privacy_decision="sanitized facts only; restricted binary inputs stay local",
    ),
    RouteRule(
        task_key="finance-analysis",
        task_title="Finance budget review",
        preferred_model=QWEN,
        fallback_model=HUMAN_REVIEW,
        candidate_models=(QWEN, STEP, HUMAN_REVIEW),
        required_capabilities=("budget_arithmetic", "scope_cost_validation"),
        modalities=(SourceModality.STRUCTURED_DATA,),
        privacy_level=PrivacyLevel.RESTRICTED,
        complexity="medium",
        tool_requirement="deterministic budget calculator",
        latency_budget_ms=5_000,
        cost_budget_usd=0,
        validation_requirement="Arithmetic totals, reserve threshold, and Finance schema",
        preferred_reason=(
            "Private budget data and deterministic arithmetic remain local on DGX Spark."
        ),
        privacy_decision="budget rows remain local and are not sent to a cloud provider",
        static_exclusions=((STEP, "No quality gain justifies exporting private budget rows."),),
    ),
    RouteRule(
        task_key="engineering-plan",
        task_title="Engineering implementation plan",
        preferred_model=ENGINEERING,
        fallback_model=HUMAN_REVIEW,
        candidate_models=(ENGINEERING, QWEN, HUMAN_REVIEW),
        required_capabilities=("repository_inspection", "test_execution", "diff_capture"),
        modalities=(SourceModality.STRUCTURED_DATA, SourceModality.TEXT),
        privacy_level=PrivacyLevel.RESTRICTED,
        complexity="high",
        tool_requirement="existing Engineering execution contract",
        latency_budget_ms=30_000,
        cost_budget_usd=0,
        validation_requirement="Real command evidence; never infer a successful diff or test",
        preferred_reason=(
            "Repository actions and tests require the accepted Engineering execution chain, "
            "not a text-only completion."
        ),
        privacy_decision="repository content and command evidence remain on the product plane",
        static_exclusions=((STEP, "A cloud planning model cannot claim repository execution."),),
    ),
    RouteRule(
        task_key="risk-review",
        task_title="Risk and policy review",
        preferred_model=QWEN,
        fallback_model=HUMAN_REVIEW,
        candidate_models=(QWEN, STEP, HUMAN_REVIEW),
        required_capabilities=("privacy_policy_review", "authority_boundary_review"),
        modalities=(SourceModality.STRUCTURED_DATA, SourceModality.TEXT),
        privacy_level=PrivacyLevel.RESTRICTED,
        complexity="high",
        tool_requirement="Deterministic Policy Gate",
        latency_budget_ms=6_000,
        cost_budget_usd=0,
        validation_requirement="Policy rule match and mandatory human-review language",
        preferred_reason=(
            "Risk review uses restricted evidence and local policy rules, so Qwen remains local."
        ),
        privacy_decision="no restricted accident fact is exported for policy review",
        static_exclusions=((STEP, "Restricted risk context remains local."),),
    ),
    RouteRule(
        task_key="artifact-synthesis",
        task_title="Artifact Synthesizer",
        preferred_model=STEP,
        fallback_model=QWEN,
        candidate_models=(STEP, QWEN, HUMAN_REVIEW),
        required_capabilities=("cross_artifact_consistency", "executive_synthesis"),
        modalities=(SourceModality.STRUCTURED_DATA, SourceModality.TEXT),
        privacy_level=PrivacyLevel.INTERNAL,
        complexity="high",
        tool_requirement="filesystem Artifact Store",
        latency_budget_ms=12_000,
        cost_budget_usd=0.08,
        validation_requirement="Six Artifact schemas, checksums, and Evidence-ID provenance",
        preferred_reason=(
            "Step 3.7 synthesizes sanitized structured proposals; the Artifact Store remains "
            "the write authority."
        ),
        privacy_decision="sanitized proposals only; source files remain local",
    ),
    RouteRule(
        task_key="verification",
        task_title="Independent Verifier",
        preferred_model=QWEN,
        fallback_model=HUMAN_REVIEW,
        candidate_models=(QWEN, HUMAN_REVIEW),
        required_capabilities=("independent_consistency_review", "citation_validation"),
        modalities=(SourceModality.STRUCTURED_DATA, SourceModality.TEXT),
        privacy_level=PrivacyLevel.RESTRICTED,
        complexity="high",
        tool_requirement="bounded validation and revision",
        latency_budget_ms=8_000,
        cost_budget_usd=0,
        validation_requirement="Independent review of claims, budget, policy, and citations",
        preferred_reason=(
            "Local Qwen provides an independent review of Step-led synthesis without "
            "exporting the completed restricted package."
        ),
        privacy_decision="complete decision package remains local during verification",
        static_exclusions=((STEP, "Verifier must be independent from Step-led synthesis."),),
    ),
)


class ExplainableInsuranceRouter:
    """Create auditable decisions; do not execute the selected models."""

    def route(self, request: RoutingPreviewRequest) -> RoutingPreviewResponse:
        unavailable = set(request.unavailable_models)
        context_length = self._estimated_context_length(request.evidence_package)
        decisions = [self._decision(rule, unavailable, context_length) for rule in RULES]
        return RoutingPreviewResponse(
            package_id=request.evidence_package.package_id,
            decisions=decisions,
            live_model_calls=0,
            simulation_disclosure=(
                "Routing decisions are deterministic policy output. Availability constraints "
                "exercise fallback selection only; they do not claim a model call occurred."
            ),
        )

    @staticmethod
    def _decision(
        rule: RouteRule,
        unavailable: set[str],
        context_length: int,
    ) -> ExplainableRouteDecision:
        selected = rule.preferred_model
        fallback_used = False
        excluded = dict(rule.static_exclusions)
        reason = rule.preferred_reason
        if selected in unavailable:
            excluded[selected] = "Simulated unavailable for deterministic fallback acceptance."
            selected = rule.fallback_model
            fallback_used = True
            reason = (
                f"{rule.preferred_model} is unavailable under the simulated acceptance "
                f"condition; selected {selected} through the declared fallback policy."
            )
        if selected in unavailable:
            excluded[selected] = "Fallback is also unavailable."
            selected = HUMAN_REVIEW
            fallback_used = True
            reason = "All executable candidates are unavailable; human review is required."

        provider, latency, cost = ExplainableInsuranceRouter._estimate(selected)
        return ExplainableRouteDecision(
            task_key=rule.task_key,
            task_title=rule.task_title,
            requested_model=rule.preferred_model,
            selected_model=selected,
            provider=provider,
            reason=reason,
            candidate_models=list(rule.candidate_models),
            excluded_models=excluded,
            required_capabilities=list(rule.required_capabilities),
            input_modalities=list(rule.modalities),
            privacy_level=rule.privacy_level,
            complexity=rule.complexity,  # type: ignore[arg-type]
            context_length=context_length,
            tool_requirement=rule.tool_requirement,
            latency_budget_ms=rule.latency_budget_ms,
            cost_budget_usd=rule.cost_budget_usd,
            estimated_latency_ms=latency,
            estimated_cost_usd=cost,
            privacy_decision=rule.privacy_decision,
            fallback_model=rule.fallback_model,
            fallback_used=fallback_used,
            validation_required=True,
            validation_requirement=rule.validation_requirement,
        )

    @staticmethod
    def _estimated_context_length(package: EvidencePackage) -> int:
        character_count = len(package.mission) + sum(len(item.content) for item in package.evidence)
        return max(1, character_count // 4)

    @staticmethod
    def _estimate(model: str) -> tuple[str, float, float]:
        if model == STEP:
            return "step", 6_000, 0.04
        if model == QWEN:
            return "qwen-local-dgx", 2_500, 0
        if model == ENGINEERING:
            return "engineering-toolchain", 12_000, 0
        if model == EVIDENCE_ADAPTER:
            return "local-adapter-chain", 450, 0
        return "human", 0, 0
