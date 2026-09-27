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
@pytest.mark.parametrize('schema_name', ['engineering_review_v1', 'engineering_review_v2'])
async def test_fixed_schema_client_router_provider_transmission(monkeypatch, schema_name):
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
    await client.complete(messages, response_schema=schema_name)
    assert observed[0][1]['response_schema'] == schema_name
    sent = observed[1][1]['response_format']
    assert sent == response_format(schema_name)
    assert sent['json_schema']['strict'] is True
    findings = sent['json_schema']['schema']['properties']['findings']
    assert findings['maxItems'] == 3
    assert findings['items']['properties']['evidence']['maxLength'] == 180
    assert set(observed[1][1]) == {'model', 'messages', 'temperature', 'max_tokens',
                                 'response_format'}
    observed.clear()
    await client.complete(messages)
    assert 'response_schema' not in observed[0][1]
    assert 'response_format' not in observed[1][1]


def test_review_v2_requires_all_six_bounded_checks_before_conclusion():
    selected = response_format('engineering_review_v2')['json_schema']
    assert selected['strict'] is True
    schema = selected['schema']
    assert schema['additionalProperties'] is False
    assert schema['required'] == ['checks', 'patch_sha', 'findings', 'conclusion']
    assert list(schema['properties']) == ['checks', 'patch_sha', 'findings', 'conclusion']
    checks = schema['properties']['checks']
    names = ['input_shape', 'material_rules', 'filenames', 'output_contract',
             'side_effects', 'tests']
    assert checks['type'] == 'object'
    assert checks['additionalProperties'] is False
    assert checks['required'] == names
    assert list(checks['properties']) == names
    for check in checks['properties'].values():
        assert check['type'] == 'object'
        assert check['additionalProperties'] is False
        assert check['required'] == ['path', 'line', 'evidence', 'satisfied']
        assert check['properties'] == {
            'path': {'type': 'string', 'enum': ['app/insurance_poc/materials.py',
                                              'tests/test_insurance_poc_materials.py']},
            'line': {'type': 'integer', 'minimum': 1},
            'evidence': {'type': 'string', 'minLength': 1, 'maxLength': 180},
            'satisfied': {'type': 'boolean'},
        }
    legacy = response_format('engineering_review_v1')['json_schema']['schema']
    for name in ('patch_sha', 'findings', 'conclusion'):
        assert schema['properties'][name] == legacy['properties'][name]


def test_review_v1_wire_contract_remains_unchanged():
    selected = response_format('engineering_review_v1')
    assert selected['type'] == 'json_schema'
    assert selected['json_schema']['name'] == 'engineering_review_v1'
    assert selected['json_schema']['strict'] is True
    schema = selected['json_schema']['schema']
    assert schema['additionalProperties'] is False
    assert schema['required'] == ['patch_sha', 'conclusion', 'findings']
    assert list(schema['properties']) == ['patch_sha', 'conclusion', 'findings']
    assert schema['properties']['patch_sha'] == {'type': 'string', 'pattern': '^[0-9a-f]{64}$'}
    assert schema['properties']['conclusion'] == {
        'type': 'string', 'enum': ['passed', 'changes_requested', 'inconclusive']}
    findings = schema['properties']['findings']
    assert findings['type'] == 'array' and findings['maxItems'] == 3
    finding = findings['items']
    assert finding['type'] == 'object' and finding['additionalProperties'] is False
    assert finding['required'] == ['path', 'line', 'trigger', 'impact', 'evidence', 'severity']
    assert finding['properties'] == {
        'path': {'type': 'string', 'enum': ['app/insurance_poc/materials.py',
                                          'tests/test_insurance_poc_materials.py']},
        'line': {'type': 'integer', 'minimum': 1},
        'trigger': {'type': 'string', 'minLength': 1, 'maxLength': 180},
        'impact': {'type': 'string', 'minLength': 1, 'maxLength': 180},
        'evidence': {'type': 'string', 'minLength': 1, 'maxLength': 180},
        'severity': {'type': 'string', 'enum': ['blocking', 'warning', 'info']},
    }


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
