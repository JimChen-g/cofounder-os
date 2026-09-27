import json

import httpx
import pytest

from app.clients.gateway import GatewayClient
from app.evaluation.sampling import SampleTask, derive_label, sample_task, score_output
from app.request_constraints import RequestPolicy


@pytest.mark.parametrize("output,expected,passed", [
    ('{"missing":["consent"],"complete":false}', ("consent",), True),
    ('{"missing":[],"complete":true}', (), True),
    ('{"missing":["consent","consent"],"complete":false}', ("consent",), False),
    ('{"missing":[],"complete":true}', ("consent",), False),
    ('{"missing":["consent"],"complete":true}', ("consent",), False),
    ('{"missing":[],"complete":1}', (), False),
    ('not json', (), False),
])
def test_semantic_boundaries(output, expected, passed):
    assert score_output(output, expected)["semantic_pass"] is passed


@pytest.mark.asyncio
async def test_two_channel_evidence(tmp_path):
    payloads = []
    def transport(request):
        body = json.loads(request.content)
        payloads.append(body)
        channel = body["allowed_providers"][0]
        output = '{"missing":["consent"],"complete":false}' if channel == "local" else '{}'
        return httpx.Response(200, json={"choices": [{"message": {"content": output}}],
            "usage": {"total_tokens": 40}, "cofounder_os": {"selected_provider": channel,
            "selected_model": "test-fixture"}})
    policy = RequestPolicy(privacy="public", allowed_providers={"local", "step"},
        permissions={"model:invoke", "cloud:invoke"}, cloud_call_budget=1, max_attempts=2)
    client = GatewayClient("http://test", transport=httpx.MockTransport(transport), policy=policy)
    task = SampleTask("case-1", "completeness", "fixture", "group-1",
                      "Required: consent. Provided: none.", ("consent",), policy)
    result = await sample_task(task, client, tmp_path)
    assert len(payloads) == 2
    assert result["label"] == "local" == derive_label(result["candidates"])
    assert result["attempts_reserved"] == 2
    assert "expected_missing" not in result["features_before_call"]
    assert [p["allowed_providers"] for p in payloads] == [["local"], ["step"]]
    assert json.loads((tmp_path / "case-1.json").read_text()) == result
    observation = json.loads((tmp_path / "case-1-local.record.json").read_text())
    assert observation["family_id"] == "group-1"
    assert observation["split"] == "smoke"
    assert observation["measurement"]["cost"] is None
    assert observation["measurement"]["score_kind"] == "deterministic"
    with pytest.raises(FileExistsError):
        await sample_task(task, client, tmp_path)


@pytest.mark.asyncio
async def test_restricted_never_calls_cloud_and_rejects_wrong_provider(tmp_path):
    calls = []
    def transport(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "content": '{"missing":[],"complete":true}'}}],
            "cofounder_os": {"selected_provider": "step"}})
    client = GatewayClient("http://test", transport=httpx.MockTransport(transport))
    task = SampleTask("private", "completeness", "fixture", "group-1", "hello", (), RequestPolicy())
    result = await sample_task(task, client, tmp_path)
    assert len(calls) == 1
    assert result["label"] == "neither"
    assert result["candidates"][0]["error"] == "provider_identity_mismatch"


@pytest.mark.asyncio
async def test_transport_failure_retains_safe_error_and_neither_label(tmp_path):
    def transport(request):
        raise httpx.ReadTimeout("secret-endpoint-token", request=request)
    client = GatewayClient("http://test", transport=httpx.MockTransport(transport))
    task = SampleTask("error", "completeness", "fixture", "group-1", "hello", (), RequestPolicy())
    result = await sample_task(task, client, tmp_path)
    assert result["label"] == "neither"
    assert result["candidates"][0]["error"] == "timeout"
    assert result["attempts_reserved"] == 1
    assert "secret-endpoint-token" not in (tmp_path / "error.json").read_text()
