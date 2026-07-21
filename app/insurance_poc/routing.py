"""Explainable, constraint-driven routing for the D14 insurance workflow."""

from __future__ import annotations

from dataclasses import dataclass

from .models import (
    EvidenceItem,
    EvidencePackage,
    ExplainableRouteDecision,
    PrivacyLevel,
    RoutingPreviewRequest,
    RoutingPreviewResponse,
    SourceModality,
)


QWEN = "cofounder-qwen"
STEP = "cofounder-step"
HUMAN_REVIEW = "human-review"
EVIDENCE_ADAPTER = "insurance-evidence-adapter-chain"
GENERIC_LOCAL = "generic-deterministic-agent-local"


@dataclass(frozen=True)
class Candidate:
    name: str
    provider: str
    capabilities: frozenset[str]
    modalities: frozenset[SourceModality]
    max_privacy: PrivacyLevel
    latency_ms: float
    cost_usd: float
    max_context: int
    cloud: bool = False


@dataclass(frozen=True)
class RouteRule:
    task_key: str
    task_title: str
    agent: str
    preferred: tuple[str, ...]
    required_capabilities: frozenset[str]
    modalities: frozenset[SourceModality]
    complexity: str
    tool_requirement: str
    latency_budget_ms: float
    cost_budget_usd: float
    validation_requirement: str


ALL_MODALITIES = frozenset(SourceModality)
TEXT_MODALITIES = frozenset({SourceModality.TEXT, SourceModality.STRUCTURED_DATA})

LOCAL_CAPABILITIES = {
    "evidence-extractor": frozenset({"pdf_parse", "image_fixture_adapter", "schema_validate"}),
    "executive-orchestrator": frozenset({"bounded_dag", "shared_context"}),
    "product-agent": frozenset({"product_scope", "evidence_citation"}),
    "finance-agent": frozenset({"budget_arithmetic", "scope_reconciliation"}),
    "engineering-agent": frozenset({"implementation_plan", "execution_disclosure"}),
    "risk-agent": frozenset({"authority_review", "privacy_policy"}),
    "artifact-synthesizer": frozenset({"artifact_consistency", "artifact_write"}),
    "verifier": frozenset({"independent_compare", "bounded_revision"}),
    "release-agent": frozenset({"approval_receipt", "no_external_write"}),
}

CANDIDATES: dict[str, Candidate] = {
    EVIDENCE_ADAPTER: Candidate(
        EVIDENCE_ADAPTER,
        "dgx-local-adapter",
        LOCAL_CAPABILITIES["evidence-extractor"],
        ALL_MODALITIES,
        PrivacyLevel.RESTRICTED,
        450,
        0,
        120_000,
    ),
    **{
        f"{agent}-local": Candidate(
            f"{agent}-local",
            "dgx-local-deterministic-agent",
            capabilities,
            TEXT_MODALITIES,
            PrivacyLevel.RESTRICTED,
            25,
            0,
            120_000,
        )
        for agent, capabilities in LOCAL_CAPABILITIES.items()
        if agent != "evidence-extractor"
    },
    GENERIC_LOCAL: Candidate(
        GENERIC_LOCAL,
        "dgx-local-deterministic-agent",
        frozenset().union(*LOCAL_CAPABILITIES.values()),
        ALL_MODALITIES,
        PrivacyLevel.RESTRICTED,
        35,
        0,
        120_000,
    ),
    QWEN: Candidate(
        QWEN,
        "qwen-local-dgx",
        frozenset().union(*LOCAL_CAPABILITIES.values()),
        TEXT_MODALITIES,
        PrivacyLevel.RESTRICTED,
        2_500,
        0,
        64_000,
    ),
    STEP: Candidate(
        STEP,
        "step-cloud",
        frozenset().union(*LOCAL_CAPABILITIES.values()),
        ALL_MODALITIES,
        PrivacyLevel.INTERNAL,
        6_000,
        0.04,
        128_000,
        cloud=True,
    ),
    HUMAN_REVIEW: Candidate(
        HUMAN_REVIEW,
        "human",
        frozenset().union(*LOCAL_CAPABILITIES.values()),
        ALL_MODALITIES,
        PrivacyLevel.RESTRICTED,
        0,
        0,
        1_000_000,
    ),
}


def _rule(
    key: str,
    title: str,
    agent: str,
    capabilities: frozenset[str],
    *,
    modalities: frozenset[SourceModality] = TEXT_MODALITIES,
    complexity: str = "high",
    tool: str = "persisted structured Agent output",
    latency: float = 8_000,
    cost: float = 0.05,
    validation: str = "Schema, provenance, and upstream checksum validation",
) -> RouteRule:
    local = EVIDENCE_ADAPTER if agent == "evidence-extractor" else f"{agent}-local"
    return RouteRule(
        key,
        title,
        agent,
        (local, GENERIC_LOCAL, QWEN, STEP, HUMAN_REVIEW),
        capabilities,
        modalities,
        complexity,
        tool,
        latency,
        cost,
        validation,
    )


RULES = (
    _rule(
        "evidence-extraction",
        "Evidence Extraction",
        "evidence-extractor",
        LOCAL_CAPABILITIES["evidence-extractor"],
        modalities=frozenset({SourceModality.DOCUMENT, SourceModality.IMAGE}),
        complexity="medium",
        tool="bounded local PDF parser and checksum-bound image Adapter",
        latency=2_000,
        cost=0,
    ),
    _rule(
        "executive-orchestration",
        "Executive Orchestrator",
        "executive-orchestrator",
        LOCAL_CAPABILITIES["executive-orchestrator"],
        tool="fixed DAG materialization",
    ),
    _rule(
        "product-analysis",
        "Product scope and acceptance",
        "product-agent",
        LOCAL_CAPABILITIES["product-agent"],
    ),
    _rule(
        "finance-analysis",
        "Finance scope/budget reconciliation",
        "finance-agent",
        LOCAL_CAPABILITIES["finance-agent"],
        complexity="medium",
        tool="deterministic budget calculator over Product output",
        latency=5_000,
        cost=0,
    ),
    _rule(
        "engineering-plan",
        "Engineering implementation plan",
        "engineering-agent",
        LOCAL_CAPABILITIES["engineering-agent"],
        tool="planning-only execution contract with no fabricated test result",
        cost=0,
    ),
    _rule(
        "risk-review",
        "Risk and authority review",
        "risk-agent",
        LOCAL_CAPABILITIES["risk-agent"],
        tool="deterministic policy and authority rules",
        cost=0,
    ),
    _rule(
        "private-upload-policy",
        "Private upload Policy Gate",
        "risk-agent",
        LOCAL_CAPABILITIES["risk-agent"],
        complexity="low",
        tool="Deterministic Policy Gate",
        latency=500,
        cost=0,
        validation="A persisted policy.denied event is required",
    ),
    _rule(
        "artifact-synthesis",
        "Artifact Synthesizer",
        "artifact-synthesizer",
        LOCAL_CAPABILITIES["artifact-synthesizer"],
        tool="filesystem Artifact Store",
    ),
    _rule(
        "verification",
        "Independent Verifier",
        "verifier",
        LOCAL_CAPABILITIES["verifier"],
        tool="independent persisted-output comparison",
        cost=0,
    ),
    _rule(
        "release-approval",
        "Founder-governed sanitized release",
        "release-agent",
        LOCAL_CAPABILITIES["release-agent"],
        complexity="low",
        tool="Policy Gate plus human Approval",
        latency=500,
        cost=0,
        validation="Founder capability, approval digest, TTL, and local receipt",
    ),
)


class ExplainableInsuranceRouter:
    """Filter and rank candidates from actual evidence and execution constraints."""

    def route(self, request: RoutingPreviewRequest) -> RoutingPreviewResponse:
        context_length = self._estimated_context_length(request.evidence_package)
        decisions = [self._decision(rule, request, context_length) for rule in RULES]
        return RoutingPreviewResponse(
            package_id=request.evidence_package.package_id,
            decisions=decisions,
            live_model_calls=0,
            simulation_disclosure=(
                "These are persisted routing decisions, not model-call claims. Local Agent "
                "adapters are selected when live-provider health is not explicitly confirmed."
            ),
        )

    def _decision(
        self,
        rule: RouteRule,
        request: RoutingPreviewRequest,
        context_length: int,
    ) -> ExplainableRouteDecision:
        evidence = self._relevant_evidence(request.evidence_package, rule.agent)
        privacy = self._privacy(evidence)
        cloud_eligible = bool(evidence) and all(item.cloud_eligible for item in evidence)
        # Specialist Agents receive the normalized Evidence Package, not raw files.
        # Only the extraction task is routed against document/image modalities.
        modalities = rule.modalities
        latency_budget = request.latency_budget_ms or rule.latency_budget_ms
        cost_budget = (
            request.cost_budget_usd
            if request.cost_budget_usd is not None
            else rule.cost_budget_usd
        )
        unavailable = set(request.unavailable_models)
        excluded: dict[str, str] = {}
        viable: list[Candidate] = []
        for name in rule.preferred:
            candidate = CANDIDATES[name]
            reason = self._exclusion_reason(
                candidate,
                request,
                unavailable,
                rule,
                modalities,
                privacy,
                cloud_eligible,
                context_length,
                latency_budget,
                cost_budget,
            )
            if reason:
                excluded[name] = reason
            else:
                viable.append(candidate)
        if not viable:
            raise ValueError(f"No viable route for {rule.task_key}")
        selected = viable[0]
        for candidate in viable[1:]:
            excluded[candidate.name] = (
                f"Eligible but lower-ranked than {selected.name} for this bounded task."
            )
        preferred_name = rule.preferred[0]
        fallback_used = selected.name != preferred_name
        fallback = next(
            (candidate.name for candidate in viable[1:]),
            HUMAN_REVIEW,
        )
        reason = (
            f"Selected {selected.name}: it satisfies {len(rule.required_capabilities)} "
            f"required capabilities, {privacy.value} privacy, {context_length} estimated "
            f"tokens, and the {latency_budget:.0f} ms / ${cost_budget:.2f} budgets."
        )
        return ExplainableRouteDecision(
            task_key=rule.task_key,
            task_title=rule.task_title,
            requested_model=preferred_name,
            selected_model=selected.name,
            provider=selected.provider,
            reason=reason,
            candidate_models=list(rule.preferred),
            excluded_models=excluded,
            required_capabilities=sorted(rule.required_capabilities),
            input_modalities=sorted(modalities, key=lambda item: item.value),
            privacy_level=privacy,
            complexity=rule.complexity,  # type: ignore[arg-type]
            context_length=context_length,
            tool_requirement=rule.tool_requirement,
            latency_budget_ms=latency_budget,
            cost_budget_usd=cost_budget,
            estimated_latency_ms=selected.latency_ms,
            estimated_cost_usd=selected.cost_usd,
            privacy_decision=(
                "Cloud use is allowed only for evidence explicitly marked cloud_eligible."
                if cloud_eligible
                else "Evidence remains on the local DGX product plane."
            ),
            fallback_model=fallback,
            fallback_used=fallback_used,
            validation_required=True,
            validation_requirement=rule.validation_requirement,
        )

    @staticmethod
    def _exclusion_reason(
        candidate: Candidate,
        request: RoutingPreviewRequest,
        unavailable: set[str],
        rule: RouteRule,
        modalities: frozenset[SourceModality],
        privacy: PrivacyLevel,
        cloud_eligible: bool,
        context_length: int,
        latency_budget: float,
        cost_budget: float,
    ) -> str | None:
        if candidate.name in unavailable:
            return "The candidate is declared unavailable for this request."
        if candidate.name in {QWEN, STEP} and not request.provider_health.get(
            candidate.name,
            False,
        ):
            return "Live provider health was not explicitly confirmed."
        if not rule.required_capabilities <= candidate.capabilities:
            return "Required task capabilities are not all supported."
        if not modalities <= candidate.modalities:
            return "Input modalities exceed the candidate contract."
        if privacy == PrivacyLevel.RESTRICTED and candidate.max_privacy != PrivacyLevel.RESTRICTED:
            return "Restricted evidence cannot use this candidate."
        if candidate.cloud and not cloud_eligible:
            return "One or more relevant evidence items are not cloud eligible."
        if context_length > candidate.max_context:
            return "Estimated context exceeds the candidate limit."
        if candidate.latency_ms > latency_budget:
            return "Estimated latency exceeds the request budget."
        if candidate.cost_usd > cost_budget:
            return "Estimated cost exceeds the request budget."
        return None

    @staticmethod
    def _relevant_evidence(package: EvidencePackage, agent: str) -> list[EvidenceItem]:
        relevant = [item for item in package.evidence if agent in item.used_by_agents]
        return relevant or list(package.evidence)

    @staticmethod
    def _privacy(evidence: list[EvidenceItem]) -> PrivacyLevel:
        levels = {item.privacy_level for item in evidence}
        if PrivacyLevel.RESTRICTED in levels:
            return PrivacyLevel.RESTRICTED
        if PrivacyLevel.INTERNAL in levels:
            return PrivacyLevel.INTERNAL
        return PrivacyLevel.PUBLIC

    @staticmethod
    def _estimated_context_length(package: EvidencePackage) -> int:
        character_count = len(package.mission) + sum(len(item.content) for item in package.evidence)
        return max(1, character_count // 4)
