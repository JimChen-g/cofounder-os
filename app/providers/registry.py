"""Provider registry — manages available providers with optional fallback."""

from __future__ import annotations

import asyncio
from typing import Sequence, TypedDict

from app.policy.request_policy import InvocationBudget, PolicyDenied, active_budget
from app.request_constraints import RequestPolicy

from app.models import ChatMessage, ChatResponse, Provider
from app.providers.base import BaseProvider, ProviderError


class ProviderHealthResult(TypedDict):
    """Normalized provider health entry returned by the registry."""

    provider: str
    status: str
    latency_ms: float | None


class ProviderRegistry:
    """Registry of configured AI providers."""

    def __init__(self) -> None:
        self._providers: dict[Provider, BaseProvider] = {}

    def register(self, provider: BaseProvider) -> None:
        """Register a provider instance."""
        self._providers[provider.name] = provider

    def get(self, provider: Provider) -> BaseProvider | None:
        """Return a provider by enum, or None if not registered."""
        return self._providers.get(provider)

    def all(self) -> Sequence[BaseProvider]:
        """Return all registered providers."""
        return list(self._providers.values())

    def clear(self) -> None:
        """Remove all registered providers (useful for tests)."""
        self._providers.clear()

    async def complete_with_fallback(
        self,
        preferred: Provider,
        *,
        model: str,
        messages: list[ChatMessage],
        temperature: float = 0.7,
        max_tokens: int = 1024,
        policy: RequestPolicy | None = None,
    ) -> tuple[ChatResponse, Provider]:
        """Filter before every attempt; failures retain their budget reservation."""
        policy = policy or RequestPolicy()
        budget = active_budget.get() or InvocationBudget(policy)
        policy = budget.policy.intersect(policy)
        order = [preferred] + [p.name for p in self.all() if p.name != preferred]
        candidates = [p for p in order if self.get(p) is not None and policy.allows(p)]
        if not candidates:
            raise PolicyDenied("no_legal_provider")
        tried: list[Provider] = []
        # UTF-8 byte count + framing is a conservative text token bound.
        tokens = sum(len((m.content or "").encode()) + 16 for m in messages) + max_tokens
        for name in candidates:
            provider = self.get(name)
            assert provider is not None
            remaining = budget.reserve(name, tokens, policy)
            tried.append(name)
            try:
                response = await asyncio.wait_for(provider.complete(
                    model=model if name == preferred else "",
                    messages=messages, temperature=temperature, max_tokens=max_tokens,
                ), timeout=remaining)
                return response, name
            except (ProviderError, asyncio.TimeoutError):
                continue
        raise ProviderError(f"All providers failed after trying: {[t.value for t in tried]}")

    async def health_status(self) -> list[ProviderHealthResult]:
        """Return health info for all registered providers."""
        results: list[ProviderHealthResult] = []
        for provider in self.all():
            status, latency = await provider.health()
            results.append(
                {
                    "provider": provider.name.value,
                    "status": status,
                    "latency_ms": latency,
                }
            )
        return results


# Module-level default registry — tests can replace this via set_registry()
_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the default provider registry, creating it if needed."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def set_registry(registry: ProviderRegistry | None) -> None:
    """Replace the global registry (primarily for tests)."""
    global _registry
    _registry = registry
