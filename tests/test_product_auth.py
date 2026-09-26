from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.main import app


@pytest.fixture
def auth_client(monkeypatch, tmp_path):
    for name, value in {'PRODUCT_API_TOKEN':'founder-token', 'PRODUCT_API_BRIDGE_TOKEN':'bridge-token',
                        'FEISHU_APP_ID':'app', 'FEISHU_TENANT_KEY':'tenant', 'FEISHU_OPEN_ID':'paired',
                        'PRODUCT_DATA_DIR':str(tmp_path), 'GATEWAY_API_KEY':'gateway-token'}.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def bridge_headers(user='paired'):
    return {'Authorization':'Bearer bridge-token', 'X-Feishu-App':'app', 'X-Feishu-Tenant':'tenant', 'X-Feishu-User':user}


def test_all_deployed_api_surfaces_require_auth(auth_client):
    for path in ['/api/health', '/api/runs', '/api/evaluations', '/api/insurance-poc/fixtures', '/v1/models', '/v1/chat/completions']:
        assert auth_client.get(path).status_code == 401


def test_bridge_bound_identity_and_no_business_authority(auth_client):
    assert auth_client.get('/api/health', headers=bridge_headers('other')).status_code == 403
    assert auth_client.post('/api/runs', headers=bridge_headers(), json={'owner':'founder'}).status_code == 403
    assert auth_client.post('/api/runs/any/approvals/any', headers=bridge_headers(), json={}).status_code == 403
    assert auth_client.get('/api/health', headers={'Authorization':'Bearer gateway-token'}).status_code == 401


def test_receipt_is_durable_idempotent_not_task_completion(auth_client):
    payload = {'message_id':'message', 'body_sha256':'a'*64}
    first = auth_client.post('/api/bridge/receipt', headers=bridge_headers(), json=payload)
    assert first.status_code == 200
    second = auth_client.post('/api/bridge/receipt', headers=bridge_headers(), json=payload)
    assert second.json() == first.json()
    assert first.json()['task_created'] is False
    changed = auth_client.post('/api/bridge/receipt', headers=bridge_headers(), json=payload | {'body_sha256':'b'*64})
    assert changed.status_code == 409


def test_founder_cannot_forge_another_owner(auth_client):
    response = auth_client.post('/api/runs', headers={'Authorization':'Bearer founder-token'}, json={'owner':'someone-else'})
    assert response.status_code == 403


def test_missing_config_fails_closed(auth_client, monkeypatch):
    monkeypatch.delenv('PRODUCT_API_TOKEN')
    monkeypatch.delenv('PRODUCT_API_BRIDGE_TOKEN')
    get_settings.cache_clear()
    assert auth_client.get('/api/health', headers={'Authorization':'Bearer founder-token'}).status_code == 401


def test_cross_run_owner_is_hidden(auth_client):
    from app.services.product_api import build_product_api_service
    service = build_product_api_service(get_settings())
    run, _ = service.orchestration.create_run(objective='test only', actor='test', owner='other')
    old = getattr(app.state, 'product_api_service', None)
    app.state.product_api_service = service
    try:
        for suffix in ('', '/events', '/artifacts', '/retry'):
            response = auth_client.get(f'/api/runs/{run.id}{suffix}', headers={'Authorization':'Bearer founder-token'})
            assert response.status_code == 404
    finally:
        if old is None:
            del app.state.product_api_service
        else:
            app.state.product_api_service = old
