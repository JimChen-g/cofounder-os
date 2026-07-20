"""Base provider abstraction."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.models import (
    ChatChoice,
    ChatMessage,
    ChatResponse,
    Provider,
    Role,
    Usage,
)

if TYPE_CHECKING:
    pass


class ProviderError(Exception):
    """Raised when a provider call fails."""

    def __init__(self, message: str, provider: Provider | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class BaseProvider(ABC):
    """Abstract base class for AI providers."""

    name: Provider

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    @abstractmethod
    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> ChatResponse:
        """Send a chat completion request and return a normalised response."""

    @abstractmethod
    async def health(self) -> tuple[str, float | None]:
        """Check provider health. Returns (status, latency_ms)."""

    def _build_response(
        self,
        *,
        provider: Provider,
        model: str,
        content: str | None = None,
        finish_reason: str = "stop",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        selected_upstream_model: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        function_call: dict[str, Any] | None = None,
        refusal: str | None = None,
    ) -> ChatResponse:
        """Build a normalised ChatResponse from raw provider output.

        Content may be None when the upstream returns a tool-call-only or
        reasoning model response.
        """
        return ChatResponse(
            id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
            provider=provider,
            model=model,
            selected_upstream_model=selected_upstream_model,
            choices=[
                ChatChoice(
                    index=0,
                    message=ChatMessage(
                        role=Role.ASSISTANT,
                        content=content,
                    ),
                    finish_reason=finish_reason,
                )
            ],
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )
