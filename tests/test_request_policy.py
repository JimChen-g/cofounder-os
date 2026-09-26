import asyncio

import httpx
import pytest

from app.clients.gateway import GatewayClient
from app.models import ChatMessage, ChatRequest, Provider
from app.policy.request_policy import InvocationBudget, PolicyDenied, active_budget
from app.providers.base import ProviderError
from app.providers.registry import ProviderRegistry, set_registry
from app.request_constraints import RequestPolicy
from app.router.selector import route_chat
from tests.test_router import FakeProvider


def cloud_policy(**updates):
    values = dict(privacy='public', allowed_providers={'local', 'step'},
                  permissions={'model:invoke', 'cloud:invoke'}, cloud_call_budget=2)
    return RequestPolicy(**(values | updates))


def registry(local_fail=True):
    r = ProviderRegistry()
    local = FakeProvider(Provider.QWEN, fail=local_fail)
    cloud = FakeProvider(Provider.STEP)
    r.register(local)
    r.register(cloud)
    return r, local, cloud


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', [RequestPolicy(), cloud_policy(privacy='restricted'),
                                   cloud_policy(allowed_providers={'local'}),
                                   cloud_policy(permissions={'model:invoke'}),
                                   cloud_policy(cloud_call_budget=0)])
async def test_local_failure_never_calls_cloud(policy):
    r, local, cloud = registry()
    with pytest.raises(ProviderError):
        await r.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='private')], policy=policy)
    assert local.call_count == 1
    assert cloud.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', [RequestPolicy(allowed_providers=set()), RequestPolicy(permissions=set()),
                                   RequestPolicy(max_attempts=0), RequestPolicy(max_total_tokens=0),
                                   RequestPolicy(timeout_seconds=0)])
async def test_no_legal_call_explicitly_fails(policy):
    r, local, cloud = registry()
    with pytest.raises(PolicyDenied):
        await r.complete_with_fallback(Provider.STEP, model='', messages=[ChatMessage(role='user', content='x')], policy=policy)
    assert local.call_count == cloud.call_count == 0


@pytest.mark.asyncio
async def test_allowed_fallback_and_spent_attempts():
    r, local, cloud = registry()
    budget = InvocationBudget(cloud_policy())
    token = active_budget.set(budget)
    try:
        _, selected = await r.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='x')], policy=cloud_policy())
        assert selected == Provider.STEP
        with pytest.raises(PolicyDenied, match='budget'):
            await r.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='x')], policy=cloud_policy())
    finally:
        active_budget.reset(token)
    assert local.call_count == cloud.call_count == 1
    assert budget.attempts == 2


@pytest.mark.asyncio
async def test_upstream_ranker_cannot_expand_authority():
    r, local, cloud = registry()
    token = active_budget.set(InvocationBudget(RequestPolicy()))
    try:
        with pytest.raises(ProviderError):
            await r.complete_with_fallback(Provider.STEP, model='', messages=[ChatMessage(role='user', content='secret')], policy=cloud_policy())
    finally:
        active_budget.reset(token)
    assert local.call_count == 1 and cloud.call_count == 0


@pytest.mark.asyncio
async def test_gateway_explicit_local_pins_fallback():
    r, local, cloud = registry()
    set_registry(r)
    try:
        with pytest.raises(ProviderError):
            await route_chat(ChatRequest(model='cofounder-qwen', privacy='public', allowed_providers=['local', 'step'],
                                         policy=cloud_policy().model_dump(mode='json'), messages=[ChatMessage(role='user', content='x')]))
        assert local.call_count == 1 and cloud.call_count == 0
    finally:
        set_registry(None)


@pytest.mark.asyncio
async def test_transport_preserves_constraints_for_repair_and_budget():
    payloads = []
    async def handle(request):
        import json
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'choices':[{'message':{'content':'ok'}}]})
    gateway = GatewayClient('http://test', transport=httpx.MockTransport(handle))
    token = active_budget.set(InvocationBudget(RequestPolicy()))
    try:
        for _ in range(2):
            await gateway.complete([ChatMessage(role='user', content='x')], policy=cloud_policy())
        with pytest.raises(Exception, match='budget'):
            await gateway.complete([ChatMessage(role='user', content='repair')], policy=cloud_policy())
    finally:
        active_budget.reset(token)
    assert len(payloads) == 2
    assert all(p['privacy'] == 'restricted' and p['allowed_providers'] == ['local'] for p in payloads)


@pytest.mark.asyncio
async def test_timeout_does_not_escape_local():
    r, local, cloud = registry()
    async def slow(**kw):
        local.call_count += 1
        await asyncio.sleep(1)
    local.complete = slow
    with pytest.raises(ProviderError):
        await r.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='x')], policy=RequestPolicy(timeout_seconds=.01))
    assert local.call_count == 1 and cloud.call_count == 0


def test_durable_budget_cannot_reset_or_race(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from app.policy.budget_store import BudgetStore
    path = tmp_path/'budgets.db'
    store = BudgetStore(path)
    policy = RequestPolicy(max_attempts=1)
    store.create('run-budget', policy)
    def attempt(_):
        restarted = InvocationBudget(policy, store=BudgetStore(path), key='run-budget')
        try:
            restarted.reserve(Provider.QWEN, 100, cloud_policy())
            return True
        except PolicyDenied:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(4))) == 1
    assert not attempt(0)


@pytest.mark.asyncio
async def test_real_transport_failure_stays_within_policy(monkeypatch):
    from app.providers.openai_compat import OpenAICompatProvider
    async def fail(*args, **kwargs):
        raise httpx.ConnectError('private transport detail')
    monkeypatch.setattr(httpx.AsyncClient, 'post', fail)
    r = ProviderRegistry()
    r.register(OpenAICompatProvider(Provider.QWEN, 'test', 'http://127.0.0.1:1/v1', 'qwen'))
    cloud = FakeProvider(Provider.STEP)
    r.register(cloud)
    with pytest.raises(ProviderError, match='All providers failed'):
        await r.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='secret')])
    assert cloud.call_count == 0
    _, selected = await r.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='public')], policy=cloud_policy())
    assert selected == Provider.STEP and cloud.call_count == 1
