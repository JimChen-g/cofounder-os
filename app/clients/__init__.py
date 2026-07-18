"""Public client surface for CoFounder OS."""

from app.clients.gateway import (
    GatewayClient,
    GatewayClientConfigurationError,
    GatewayClientError,
    GatewayCompletion,
    GatewayResponseError,
)

__all__ = [
    "GatewayClient",
    "GatewayClientConfigurationError",
    "GatewayClientError",
    "GatewayCompletion",
    "GatewayResponseError",
]
