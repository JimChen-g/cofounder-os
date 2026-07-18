"""Public registered-agent surface for CoFounder OS."""

from app.agents.registry import (
    DEFAULT_AGENTS,
    EXECUTIVE_AGENT_ID,
    AgentDefinition,
    AgentRegistry,
    AgentRegistryError,
    DuplicateAgentError,
    UnknownAgentError,
)

__all__ = [
    "DEFAULT_AGENTS",
    "EXECUTIVE_AGENT_ID",
    "AgentDefinition",
    "AgentRegistry",
    "AgentRegistryError",
    "DuplicateAgentError",
    "UnknownAgentError",
]
