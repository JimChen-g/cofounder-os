"""Public registered-agent surface for CoFounder OS."""

from app.agents.product_agent import (
    ALLOWED_VIRTUAL_MODELS,
    DEFAULT_VIRTUAL_MODEL,
    PRODUCT_AGENT_ID,
    ProductAgent,
    ProductAgentError,
    ProductAgentExecutionError,
    ProductAgentResponseError,
    ProductAgentServiceError,
    ProductAgentValidationError,
    ProductGatewayProtocol,
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
    "ALLOWED_VIRTUAL_MODELS",
    "DEFAULT_AGENTS",
    "DEFAULT_VIRTUAL_MODEL",
    "EXECUTIVE_AGENT_ID",
    "AgentDefinition",
    "AgentRegistry",
    "AgentRegistryError",
    "DuplicateAgentError",
    "UnknownAgentError",
    "PRODUCT_AGENT_ID",
    "ProductAgent",
    "ProductAgentError",
    "ProductAgentExecutionError",
    "ProductAgentResponseError",
    "ProductAgentServiceError",
    "ProductAgentValidationError",
    "ProductGatewayProtocol",
]
