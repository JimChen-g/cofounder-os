"""Tests for the minimal public Gateway client."""

import asyncio

import httpx
import pytest

from app.clients import (
    GatewayClient,
    GatewayResponseError,
)
from app.models import ChatMessage


def test_gateway_client_normalizes_completion_metadata():
    async def handler(request):
        assert request.url.path == "/v1/chat/completions"
        payload = __import__("json").loads(request.content)
        assert payload["model"] == "cofounder-auto"
        assert payload["stream"] is False

        return httpx.Response(
            200,
            headers={"X-Request-ID": "req-123"},
            json={
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"objective":"test"}',
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
                "cofounder_os": {
                    "selected_provider": "qwen",
                    "selected_upstream_model": "Qwen3",
                    "routing_reason": "Local planning.",
                    "fallback_used": False,
                },
            },
        )

    client = GatewayClient(
        "http://gateway.test",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        client.complete(
            [
                ChatMessage(
                    role="user",
                    content="Create a plan.",
                )
            ]
        )
    )

    assert result.content == '{"objective":"test"}'
    assert result.selected_provider == "qwen"
    assert result.selected_model == "Qwen3"
    assert result.routing_reason == "Local planning."
    assert result.request_id == "req-123"
    assert result.finish_reason is None
    assert result.usage["total_tokens"] == 15


@pytest.mark.parametrize('upstream_reason,expected', [
    ('length', 'length'), ('stop', 'stop'), (None, None), (42, None), ({'reason': 'length'}, None),
])
def test_gateway_client_preserves_string_finish_reason_without_repair(upstream_reason, expected):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            'choices': [{'message': {'role': 'assistant', 'content': '{"checks":'},
                         'finish_reason': upstream_reason}],
            'usage': {'prompt_tokens': 120, 'completion_tokens': 50, 'total_tokens': 170},
            'cofounder_os': {'selected_provider': 'qwen', 'fallback_used': False},
        })

    client = GatewayClient('http://gateway.test', transport=httpx.MockTransport(handler))
    result = asyncio.run(client.complete([ChatMessage(role='user', content='Review the patch.')]))
    assert result.finish_reason == expected
    assert result.content == '{"checks":'
    assert result.usage == {'prompt_tokens': 120, 'completion_tokens': 50, 'total_tokens': 170}
    assert result.selected_provider == 'qwen'
    assert result.raw_metadata == {'selected_provider': 'qwen', 'fallback_used': False}
    assert len(calls) == 1


def test_gateway_client_rejects_missing_message_content():
    async def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                        }
                    }
                ]
            },
        )

    client = GatewayClient(
        "http://gateway.test",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(GatewayResponseError):
        asyncio.run(
            client.complete(
                [
                    ChatMessage(
                        role="user",
                        content="Create a plan.",
                    )
                ]
            )
        )
