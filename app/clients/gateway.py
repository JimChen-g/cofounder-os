"""Client for the public CoFounder OS Gateway interface."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.models import ChatMessage


class GatewayClientError(RuntimeError):
    """Base error for Gateway client operations."""


class GatewayClientConfigurationError(GatewayClientError):
    """Raised when a Gateway client cannot be configured."""


class GatewayResponseError(GatewayClientError):
    """Raised when the Gateway returns an unusable response."""


class GatewayCompletion(BaseModel):
    """Normalized completion result used by internal application code."""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1)
    requested_model: str = Field(min_length=1)
    selected_provider: str | None = None
    selected_model: str | None = None
    routing_reason: str | None = None
    fallback_used: bool = False
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class GatewayClient:
    """Async OpenAI-compatible client for the stable Gateway boundary."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized:
            raise GatewayClientConfigurationError(
                "Gateway base_url must not be empty"
            )
        if timeout_seconds <= 0:
            raise GatewayClientConfigurationError(
                "timeout_seconds must be positive"
            )

        self.base_url = normalized
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "GatewayClient":
        """Create a client from explicit runtime environment variables."""

        base_url = os.environ.get("COFOUNDER_GATEWAY_URL", "").strip()
        if not base_url:
            raise GatewayClientConfigurationError(
                "COFOUNDER_GATEWAY_URL is required; use "
                "http://127.0.0.1:19000 on Mac or "
                "http://127.0.0.1:9000 on DGX Spark"
            )

        return cls(
            base_url,
            api_key=os.environ.get("COFOUNDER_GATEWAY_API_KEY"),
        )

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str = "cofounder-auto",
        temperature: float = 0.1,
        max_tokens: int = 1800,
    ) -> GatewayCompletion:
        """Send one non-streaming Chat Completions request."""

        if not messages:
            raise GatewayClientError("At least one message is required")
        if not model.strip():
            raise GatewayClientError("model must not be empty")
        if max_tokens <= 0:
            raise GatewayClientError("max_tokens must be positive")

        payload = {
            "model": model,
            "messages": [
                message.model_dump(exclude_none=True)
                for message in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise GatewayClientError(
                f"Gateway request failed: {exc}"
            ) from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayResponseError(
                "Gateway response is not valid JSON"
            ) from exc

        if not isinstance(body, dict):
            raise GatewayResponseError(
                "Gateway response must be a JSON object"
            )

        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise GatewayResponseError(
                "Gateway response has no choices"
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise GatewayResponseError(
                "Gateway choice must be a JSON object"
            )

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise GatewayResponseError(
                "Gateway choice has no message object"
            )

        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise GatewayResponseError(
                "Gateway response message has no text content"
            )

        metadata = body.get("cofounder_os")
        if not isinstance(metadata, dict):
            metadata = {}

        usage = body.get("usage")
        if not isinstance(usage, dict):
            usage = {}

        selected_provider = (
            metadata.get("selected_provider")
            or metadata.get("provider")
        )
        selected_model = (
            metadata.get("selected_upstream_model")
            or metadata.get("selected_model")
        )
        routing_reason = (
            metadata.get("routing_reason")
            or metadata.get("reason")
        )

        fallback_value = metadata.get("fallback_used", False)
        fallback_used = (
            fallback_value
            if isinstance(fallback_value, bool)
            else False
        )

        request_id = (
            response.headers.get("X-Request-ID")
            or body.get("id")
        )
        if request_id is not None:
            request_id = str(request_id)

        return GatewayCompletion(
            content=content.strip(),
            requested_model=model,
            selected_provider=(
                str(selected_provider)
                if selected_provider is not None
                else None
            ),
            selected_model=(
                str(selected_model)
                if selected_model is not None
                else None
            ),
            routing_reason=(
                str(routing_reason)
                if routing_reason is not None
                else None
            ),
            fallback_used=fallback_used,
            request_id=request_id,
            usage=usage,
            raw_metadata=metadata,
        )
