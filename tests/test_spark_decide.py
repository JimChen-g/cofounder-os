"""Decision invariants tested with explicitly synthetic providers."""
import pytest

from app.decision.service import DecisionRequest, decide
from app.models import Provider
from app.providers.openai_compat import OpenAICompatProvider
from app.providers.registry import ProviderRegistry
from app.providers.base import ProviderError
from app.request_constraints import RequestPolicy


class FakeProvider(OpenAICompatProvider):
    def __init__(self, name=Provider.QWEN, output="local", fail=False):
        super().__init__(name, "fixture", "http://fixture.invalid", "fixture-v1")
        self.output = output
        self.fail = fail
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise ProviderError("fixture failure")
        return self._build_response(provider=self.name, model="fixture-v1", content=self.output)


@pytest.mark.asyncio
@pytest.mark.parametrize("output,action,reason", [
    ("local", "local", None),
    ("step", "refuse", "invalid_or_truncated_label"),
    ("local\nI chose local", "refuse", "invalid_or_truncated_label"),
    ("", "refuse", "invalid_or_truncated_label"),
    ("refuse", "refuse", "model_refusal"),
])
async def test_labels(output, action, reason):
    registry = ProviderRegistry()
    registry.register(FakeProvider(output=output))
    result = await decide(DecisionRequest(task="private task", candidates=["local", "refuse"]),
                          registry, "fixture-v1")
    assert (result.action, result.refusal_reason) == (action, reason)
    assert result.scores is None and result.score_kind == "unavailable"
    assert len(result.request_sha256) == 64


@pytest.mark.asyncio
async def test_no_cloud_fallback_and_hard_filter():
    registry = ProviderRegistry()
    local, cloud = FakeProvider(fail=True), FakeProvider(Provider.STEP)
    registry.register(local)
    registry.register(cloud)
    result = await decide(DecisionRequest(task="secret", candidates=["local", "step"]),
                          registry, "fixture")
    assert result.legal_candidates == ["local"]
    assert result.refusal_reason == "local_provider_failed"
    assert (local.calls, cloud.calls) == (1, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,reason", [
    (RequestPolicy(max_attempts=0), "request_policy_or_budget_denied"),
    (RequestPolicy(allowed_providers=frozenset()), "local_decider_not_allowed"),
    (RequestPolicy(max_total_tokens=0), "request_policy_or_budget_denied"),
])
async def test_no_authority_or_budget(policy, reason):
    registry = ProviderRegistry()
    provider = FakeProvider()
    registry.register(provider)
    result = await decide(DecisionRequest(task="test", candidates=["human"], policy=policy),
                          registry, "fixture")
    assert result.refusal_reason == reason
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_authorized_step_selection_is_not_cloud_invocation():
    registry = ProviderRegistry()
    provider = FakeProvider(output="step")
    registry.register(provider)
    policy = RequestPolicy(privacy="public", allowed_providers=frozenset({"local", "step"}),
                           permissions=frozenset({"model:invoke", "cloud:invoke"}), cloud_call_budget=1)
    result = await decide(DecisionRequest(task="public", candidates=["step"], policy=policy),
                          registry, "fixture")
    assert result.action == "step" and provider.calls == 1
