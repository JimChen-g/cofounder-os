import json

import httpx
import pytest
from pydantic import ValidationError

from app.clients.gateway import GatewayClient
from app.models import ChatMessage, ChatRequest, Provider
from app.providers.openai_compat import OpenAICompatProvider
from app.providers.registry import ProviderRegistry
from app.response_schemas import response_format
from app.router.selector import route_chat


@pytest.mark.asyncio
async def test_fixed_schema_client_router_provider_transmission(monkeypatch):
    registry = ProviderRegistry()
    registry.register(OpenAICompatProvider(Provider.QWEN, 'test-only', 'http://upstream/v1', 'qwen'))
    monkeypatch.setattr('app.router.selector.get_registry', lambda: registry)
    observed = []
    actual_client = httpx.AsyncClient
    async def handler(request):
        body = json.loads(request.content)
        observed.append((request.url.host, body))
        if request.url.host == 'gateway':
            result = await route_chat(ChatRequest.model_validate(body))
            return httpx.Response(200, json=result.model_dump(mode='json'))
        return httpx.Response(200, json={'model':'qwen', 'choices':[{'message':{'content':'{}'}, 'finish_reason':'stop'}], 'usage':{}})
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: actual_client(**(kw | {'transport':transport})))
    client = GatewayClient('http://gateway')
    messages = [ChatMessage(role='user', content='review')]
    await client.complete(messages, response_schema='engineering_review_v1')
    assert observed[0][1]['response_schema'] == 'engineering_review_v1'
    sent = observed[1][1]['response_format']
    assert sent == response_format('engineering_review_v1')
    assert sent['json_schema']['strict'] is True
    findings = sent['json_schema']['schema']['properties']['findings']
    assert findings['maxItems'] == 3
    assert findings['items']['properties']['evidence']['maxLength'] == 180
    observed.clear()
    await client.complete(messages)
    assert 'response_schema' not in observed[0][1]
    assert 'response_format' not in observed[1][1]


def test_unknown_schema_rejected_before_routing():
    with pytest.raises(ValidationError):
        ChatRequest(messages=[{'role':'user', 'content':'review'}], response_schema='arbitrary')
    with pytest.raises(ValueError):
        response_format('arbitrary')


@pytest.mark.asyncio
async def test_legacy_provider_receives_no_extra_keyword():
    class LegacyProvider:
        name = Provider.QWEN
        async def complete(self, *, model, messages, temperature, max_tokens):
            return 'legacy-response'
    registry = ProviderRegistry()
    registry.register(LegacyProvider())
    response, provider = await registry.complete_with_fallback(Provider.QWEN, model='', messages=[ChatMessage(role='user', content='test')])
    assert response == 'legacy-response'
    assert provider == Provider.QWEN
