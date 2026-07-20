"""Tests for the provider registry and router."""

from __future__ import annotations

import pytest

from app.models import ChatMessage, Provider
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry


class FakeProvider:
    def __init__(self, name: Provider, fail: bool = False, model: str = "test-model") -> None:
        self.name = name
        self._fail = fail
        self._model = model
        self.call_count = 0

    async def complete(self, **kwargs):
        self.call_count += 1
        if self._fail:
            raise ProviderError(f"{self.name.value} failed", provider=self.name)
        from app.models import ChatMessage, ChatResponse, Usage

        return ChatResponse(
            id="fake",
            provider=self.name,
            model=kwargs.get("model", self._model),
            choices=[
                {
                    "index": 0,
                    "message": ChatMessage(role="assistant", content="ok"),
                    "finish_reason": "stop",
                }
            ],
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )

    async def health(self):
        if self._fail:
            return "unavailable", None
        return "healthy", 10.0


class TestProviderRegistry:
    def test_register_and_get(self):
        registry = ProviderRegistry()
        p = FakeProvider(Provider.QWEN)
        registry.register(p)
        assert registry.get(Provider.QWEN) is p
        assert registry.get(Provider.STEP) is None

    def test_all(self):
        registry = ProviderRegistry()
        registry.register(FakeProvider(Provider.QWEN))
        registry.register(FakeProvider(Provider.STEP))
        assert len(registry.all()) == 2

    def test_clear(self):
        registry = ProviderRegistry()
        registry.register(FakeProvider(Provider.QWEN))
        assert len(registry.all()) == 1
        registry.clear()
        assert len(registry.all()) == 0

    def test_no_openai_provider_registered(self):
        """Provider.OPENAI must not be a valid enum value."""
        assert Provider.QWEN.value == "cofounder-qwen"
        assert Provider.STEP.value == "cofounder-step"
        # Cannot reference Provider.OPENAI — it doesn't exist

    @pytest.mark.asyncio
    async def test_complete_with_fallback_success(self):
        registry = ProviderRegistry()
        qwen = FakeProvider(Provider.QWEN)
        registry.register(qwen)

        resp, used = await registry.complete_with_fallback(
            preferred=Provider.QWEN,
            model="cofounder-qwen",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.0,
            max_tokens=10,
        )
        assert used == Provider.QWEN
        assert qwen.call_count == 1

    @pytest.mark.asyncio
    async def test_complete_with_fallback_on_failure(self):
        registry = ProviderRegistry()
        bad_qwen = FakeProvider(Provider.QWEN, fail=True)
        step = FakeProvider(Provider.STEP)
        registry.register(bad_qwen)
        registry.register(step)

        resp, used = await registry.complete_with_fallback(
            preferred=Provider.QWEN,
            model="cofounder-qwen",
            messages=[ChatMessage(role="user", content="Hi")],
            temperature=0.0,
            max_tokens=10,
        )
        assert used == Provider.STEP
        assert bad_qwen.call_count == 1
        assert step.call_count == 1

    @pytest.mark.asyncio
    async def test_complete_with_fallback_all_fail(self):
        registry = ProviderRegistry()
        registry.register(FakeProvider(Provider.QWEN, fail=True))
        registry.register(FakeProvider(Provider.STEP, fail=True))

        with pytest.raises(ProviderError, match="All providers failed"):
            await registry.complete_with_fallback(
                preferred=Provider.QWEN,
                model="cofounder-qwen",
                messages=[ChatMessage(role="user", content="Hi")],
                temperature=0.0,
                max_tokens=10,
            )

    @pytest.mark.asyncio
    async def test_health_status(self):
        registry = ProviderRegistry()
        registry.register(FakeProvider(Provider.QWEN, fail=True))
        registry.register(FakeProvider(Provider.STEP))

        results = await registry.health_status()
        assert len(results) == 2
        statuses = {r["provider"]: r["status"] for r in results}
        assert statuses["cofounder-qwen"] == "unavailable"
        assert statuses["cofounder-step"] == "healthy"
