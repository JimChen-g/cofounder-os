"""vLLM context preflight: exact evidence, bounded output, no blind inference."""
import json

import httpx
import pytest

from app.models import ChatMessage, Provider, Role
from app.providers.base import ProviderError
from app.providers.openai_compat import OpenAICompatProvider


@pytest.mark.parametrize('count,requested,expected', [(5355, 3000, 2773), (5510, 3000, 2618), (1000, 5500, 5500), (7000, 100, 100)])
async def test_context_preflight_preserves_full_messages(monkeypatch, count, requested, expected):
    messages = [ChatMessage(role=Role.USER, content='complete source, tests and evidence')]
    requests = []
    def handle(request):
        body = json.loads(request.content)
        requests.append((request.url.path, body))
        assert request.headers['Authorization'] == 'Bearer synthetic-key'
        if request.url.path == '/tokenize':
            assert body == {'model': 'qwen', 'messages': [{'role': 'user', 'content': messages[0].content}], 'add_generation_prompt': True}
            return httpx.Response(200, json={'count': count})
        assert body['max_tokens'] == expected
        assert body['messages'] == requests[0][1]['messages']
        assert body['response_format'] == {'type': 'json_object'}
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}, 'finish_reason': 'stop'}]})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    provider = OpenAICompatProvider(Provider.QWEN, 'synthetic-key', 'http://localhost:8000/v1', 'qwen', context_window=8192)
    await provider.complete(model='qwen', messages=messages, max_tokens=requested, response_format={'type': 'json_object'})
    assert [path for path, _ in requests] == ['/tokenize', '/v1/chat/completions']


@pytest.mark.parametrize('status,body', [(200, {'count': 6200}), (500, {}), (200, {}), (200, {'count': True}), (200, {'count': -1}), (200, [])])
async def test_invalid_or_insufficient_preflight_never_invokes_model(monkeypatch, status, body):
    paths = []
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(status, json=body)
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    provider = OpenAICompatProvider(Provider.QWEN, 'synthetic-key', 'http://localhost:8000/v1', 'qwen', context_window=8192)
    with pytest.raises(ProviderError):
        await provider.complete(model='qwen', messages=[ChatMessage(role=Role.USER, content='source')], max_tokens=3000)
    assert paths == ['/tokenize']


async def test_unconfigured_provider_does_not_require_vllm(monkeypatch):
    paths = []
    def handle(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    provider = OpenAICompatProvider(Provider.STEP, 'synthetic-key', 'https://example.test/v1', 'step')
    await provider.complete(model='step', messages=[ChatMessage(role=Role.USER, content='source')])
    assert paths == ['/v1/chat/completions']
