"""Public registered-agent surface for CoFounder OS."""

from app.agents.product import (
    PRODUCT_AGENT_ID,
    AgentMismatchError,
    GatewayExecutionProtocol,
    ProductAgent,
    ProductAgentError,
    ProductAgentExecutionResult,
    TaskNotReadyError,
)
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
    "AgentMismatchError",
    "AgentRegistry",
    "AgentRegistryError",
    "DuplicateAgentError",
    "GatewayExecutionProtocol",
    "PRODUCT_AGENT_ID",
    "ProductAgent",
    "ProductAgentError",
    "ProductAgentExecutionResult",
    "TaskNotReadyError",
    "UnknownAgentError",
]
