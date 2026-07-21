"""Explicit registry of agents available to the Executive Orchestrator."""

from __future__ import annotations

from collections.abc import Iterable
from typing import List

from pydantic import BaseModel, ConfigDict, Field


EXECUTIVE_AGENT_ID = "executive-orchestrator"


class AgentRegistryError(ValueError):
    """Base error for agent-registry validation."""


class DuplicateAgentError(AgentRegistryError):
    """Raised when an agent identifier is registered more than once."""


class UnknownAgentError(AgentRegistryError):
    """Raised when a plan references an unregistered agent."""


class AgentDefinition(BaseModel):
    """Stable identity and capability description for one registered agent."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    display_name: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    capabilities: tuple[str, ...] = Field(min_length=1)
    can_plan: bool = False
    can_execute: bool = True
    planner_visible: bool = True


DEFAULT_AGENTS: tuple[AgentDefinition, ...] = (
    AgentDefinition(
        agent_id=EXECUTIVE_AGENT_ID,
        display_name="Executive Orchestrator",
        purpose=(
            "Decompose objectives, assign registered agents, coordinate "
            "dependencies, and synthesize decision-ready conclusions."
        ),
        capabilities=(
            "task decomposition",
            "dependency planning",
            "executive synthesis",
            "approval coordination",
        ),
        can_plan=True,
        can_execute=True,
    ),
    AgentDefinition(
        agent_id="research-agent",
        display_name="Research Agent",
        purpose=(
            "Gather, compare, and summarize evidence relevant to a decision."
        ),
        capabilities=(
            "evidence gathering",
            "source comparison",
            "market research",
            "fact synthesis",
        ),
        can_execute=False,
    ),
    AgentDefinition(
        agent_id="product-agent",
        display_name="Product Agent",
        purpose=(
            "Analyze users, product requirements, positioning, and delivery "
            "trade-offs."
        ),
        capabilities=(
            "user analysis",
            "product strategy",
            "requirements",
            "roadmap trade-offs",
        ),
    ),
    AgentDefinition(
        agent_id="finance-agent",
        display_name="Finance Agent",
        purpose=(
            "Evaluate revenue, cost, unit economics, scenarios, and financial "
            "risk."
        ),
        capabilities=(
            "financial modeling",
            "unit economics",
            "scenario analysis",
            "budget assessment",
        ),
    ),
    AgentDefinition(
        agent_id="legal-agent",
        display_name="Legal and Risk Agent",
        purpose=(
            "Identify legal, compliance, contractual, and policy risks."
        ),
        capabilities=(
            "legal issue spotting",
            "compliance review",
            "contract risk",
            "policy analysis",
        ),
        can_execute=False,
    ),
    AgentDefinition(
        agent_id="operations-agent",
        display_name="Operations Agent",
        purpose=(
            "Assess execution feasibility, dependencies, resources, and "
            "operational controls."
        ),
        capabilities=(
            "execution planning",
            "resource analysis",
            "process design",
            "operational risk",
        ),
        can_execute=False,
    ),
    AgentDefinition(
        agent_id="evidence-extractor",
        display_name="Evidence Extraction Agent",
        purpose="Validate bounded multimodal inputs and normalize source-linked evidence.",
        capabilities=("document parsing", "image adapter execution", "evidence normalization"),
        planner_visible=False,
    ),
    AgentDefinition(
        agent_id="engineering-agent",
        display_name="Engineering Agent",
        purpose="Produce executable technical plans and preserve tool-result honesty boundaries.",
        capabilities=("implementation planning", "repository execution", "test evidence"),
        planner_visible=False,
    ),
    AgentDefinition(
        agent_id="risk-agent",
        display_name="Risk Agent",
        purpose="Apply privacy, authority, policy, and human-review controls.",
        capabilities=("privacy review", "policy analysis", "authority boundary enforcement"),
        planner_visible=False,
    ),
    AgentDefinition(
        agent_id="artifact-synthesizer",
        display_name="Artifact Synthesizer",
        purpose="Combine persisted specialist outputs into a traceable delivery draft.",
        capabilities=("artifact synthesis", "lineage preservation", "delivery packaging"),
        planner_visible=False,
    ),
    AgentDefinition(
        agent_id="verifier",
        display_name="Independent Verifier",
        purpose="Independently validate persisted proposals and produce bounded corrections.",
        capabilities=("consistency verification", "citation validation", "bounded correction"),
        planner_visible=False,
    ),
    AgentDefinition(
        agent_id="release-agent",
        display_name="Governed Release Agent",
        purpose="Record an approved sanitized release decision without performing an external write.",
        capabilities=("approval verification", "release receipt", "external-write boundary"),
        planner_visible=False,
    ),
)


class AgentRegistry:
    """Read-only registry used to validate all plan assignments."""

    def __init__(
        self,
        agents: Iterable[AgentDefinition] = DEFAULT_AGENTS,
    ) -> None:
        definitions = tuple(agents)
        by_id: dict[str, AgentDefinition] = {}

        for definition in definitions:
            if definition.agent_id in by_id:
                raise DuplicateAgentError(
                    f"Duplicate agent id: {definition.agent_id}"
                )
            by_id[definition.agent_id] = definition

        if EXECUTIVE_AGENT_ID not in by_id:
            raise AgentRegistryError(
                f"Required agent is missing: {EXECUTIVE_AGENT_ID}"
            )

        if not by_id[EXECUTIVE_AGENT_ID].can_plan:
            raise AgentRegistryError(
                "Executive Orchestrator must have can_plan=true"
            )

        self._definitions = definitions
        self._by_id = by_id

    def get(self, agent_id: str) -> AgentDefinition:
        try:
            return self._by_id[agent_id]
        except KeyError as exc:
            raise UnknownAgentError(
                f"Unregistered agent: {agent_id}"
            ) from exc

    def require_executable(self, agent_id: str) -> AgentDefinition:
        definition = self.get(agent_id)
        if not definition.can_execute:
            raise AgentRegistryError(
                f"Agent cannot execute tasks: {agent_id}"
            )
        return definition

    def list(self) -> tuple[AgentDefinition, ...]:
        return self._definitions

    def ids(self) -> tuple[str, ...]:
        return tuple(definition.agent_id for definition in self._definitions)

    def prompt_catalog(self) -> List[dict[str, object]]:
        """Return the exact catalog supplied to the planning model."""

        return [
            {
                "agent_id": definition.agent_id,
                "display_name": definition.display_name,
                "purpose": definition.purpose,
                "capabilities": list(definition.capabilities),
            }
            for definition in self._definitions
            if definition.can_execute and definition.planner_visible
        ]
