"""Tests for /health endpoint."""

from __future__ import annotations

from app.models import Provider
from app.providers.base import BaseProvider
from app.providers.registry import ProviderRegistry, set_registry


class FakeQwen(BaseProvider):
    """Fake Qwen provider."""

    name = Provider.QWEN

    async def complete(self, **kwargs):
        from app.models import ChatMessage, ChatResponse, Usage

        return ChatResponse(
            id="fake-qwen",
            provider=Provider.QWEN,
            model="cofounder-qwen",
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
        return "healthy", 55.0


class FakeStep(BaseProvider):
    """Fake Step provider."""

    name = Provider.STEP

    async def complete(self, **kwargs):
        from app.models import ChatMessage, ChatResponse, Usage

        return ChatResponse(
            id="fake-step",
            provider=Provider.STEP,
            model="cofounder-step",
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
        return "unavailable", None


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        registry = ProviderRegistry()
        registry.register(FakeQwen())
        registry.register(FakeStep())
        set_registry(registry)

        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_schema(self, client):
        registry = ProviderRegistry()
        registry.register(FakeQwen())
        registry.register(FakeStep())
        set_registry(registry)

        resp = client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "version" in data
        assert "providers" in data

    def test_health_providers_list(self, client):
        registry = ProviderRegistry()
        registry.register(FakeQwen())
        registry.register(FakeStep())
        set_registry(registry)

        resp = client.get("/health")
        data = resp.json()
        assert isinstance(data["providers"], list)
        provider_names = {p["provider"] for p in data["providers"]}
        assert "cofounder-qwen" in provider_names
        assert "cofounder-step" in provider_names
        assert "openai" not in provider_names

    def test_health_degraded_when_provider_unavailable(self, client):
        registry = ProviderRegistry()
        registry.register(FakeQwen())
        registry.register(FakeStep())
        set_registry(registry)

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"

    def test_health_healthy_when_all_up(self, client):
        registry = ProviderRegistry()
        registry.register(FakeQwen())
        set_registry(registry)

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_root_endpoint(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "cofounder-os-gateway"
        assert "version" in data
