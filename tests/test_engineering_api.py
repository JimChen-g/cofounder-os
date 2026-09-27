import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.engineering import DispatchStore
from app.bridge.runner import business_reply
from app.config import get_settings
from app.main import app
from app.services.product_api import build_product_api_service


@pytest.fixture
def client(monkeypatch, tmp_path):
    for name, value in {'PRODUCT_API_TOKEN':'founder-token', 'PRODUCT_API_BRIDGE_TOKEN':'bridge-token',
                        'FEISHU_APP_ID':'app', 'FEISHU_TENANT_KEY':'tenant', 'FEISHU_OPEN_ID':'paired',
                        'PRODUCT_DATA_DIR':str(tmp_path)}.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    product = build_product_api_service(get_settings())
    calls = []
    class Service:
        def create(self, owner, request_id):
            calls.append(request_id)
            run, _ = product.orchestration.create_run(objective='materials test', actor='test', owner=owner)
            return product.get_run(run.id)
        async def execute(self, run_id):
            await asyncio.sleep(.01)
    store = DispatchStore(tmp_path)
    app.state.engineering_runtime = (store, Service(), product, set(), asyncio.Lock())
    with TestClient(app) as c:
        yield c, calls, store, product
    if hasattr(app.state, "engineering_runtime"):
        del app.state.engineering_runtime
    store.lock.close()
    get_settings.cache_clear()


def headers(user='paired'):
    return {'Authorization':'Bearer bridge-token', 'X-Feishu-App':'app', 'X-Feishu-Tenant':'tenant', 'X-Feishu-User':user}


def test_persisted_dedup_same_controller_and_ownership(client):
    c, calls, store, product = client
    body = {'request_id':'msg-1'}
    first = c.post('/api/engineering/runs', headers=headers(), json=body)
    assert first.status_code == 202
    run_id = first.json()['run_id']
    second = c.post('/api/engineering/runs', headers=headers(), json=body)
    assert second.json()['run_id'] == run_id
    assert second.json()['duplicate'] is True
    assert calls == ['msg-1']
    query = c.get('/api/engineering/runs/' + run_id, headers=headers())
    assert query.json()['snapshot']['run']['id'] == run_id
    assert c.get('/api/engineering/runs/' + run_id, headers=headers('stranger')).status_code == 403
    assert c.get('/api/engineering/runs/' + str(uuid4()), headers=headers()).status_code == 404
    assert c.post('/api/engineering/runs', headers=headers(), json=body | {'owner':'other'}).status_code == 422
    assert c.post('/api/engineering/runs', headers=headers(), json=body | {'task':'approve'}).status_code == 422
    assert c.post('/api/runs/' + run_id + '/retry', headers=headers(), json={}).status_code == 403


def test_restart_does_not_replay_incomplete_intent(client):
    c, calls, store, product = client
    with store.connect() as db:
        db.execute("INSERT INTO jobs VALUES('interrupted','founder','materials_completeness',NULL,'creating',NULL)")
    store.interrupt(product)
    with store.connect() as db:
        assert db.execute("SELECT state FROM jobs WHERE request_id='interrupted'").fetchone()[0] == 'interrupted'
    assert c.post('/api/engineering/runs', headers=headers(), json={'request_id':'interrupted'}).status_code == 409
    assert calls == []


def test_unsupported_bridge_command_never_calls_api():
    class NoNetwork:
        def post(self, *args, **kwargs):
            pytest.fail('Unsupported command caused a request')
    assert '格式' in business_reply({'text':'批准 全部'}, {}, NoNetwork(), {})
    assert '格式无效' in business_reply({'text':'查询 not-a-uuid'}, {}, NoNetwork(), {})


def test_restart_marks_controller_failed(client):
    c, calls, store, product = client
    run, _ = product.orchestration.create_run(objective='interrupted engineering', actor='test', owner='founder')
    with store.connect() as db:
        db.execute("INSERT INTO jobs VALUES('claimed','founder','materials_completeness',?,'running',NULL)", (str(run.id),))
    store.interrupt(product)
    assert product.get_run(run.id).run.status == 'cancelled'
    with store.connect() as db:
        assert db.execute("SELECT state FROM jobs WHERE request_id='claimed'").fetchone()[0] == 'interrupted'


def test_dispatch_lock_excludes_second_worker(client):
    c, calls, store, product = client
    with pytest.raises(BlockingIOError):
        DispatchStore(store.path.parent)


def test_real_engineering_creation_contract(client, tmp_path):
    from pathlib import Path
    from app.engineering.service import EngineeringService
    c, calls, store, product = client
    service = EngineeringService(product, Path(__file__).resolve().parents[1], tmp_path / 'workspaces')
    snapshot = service.create(owner='founder', request_id='real-create-no-model')
    assert snapshot.run.owner == 'founder'
    assert snapshot.run.metadata['engineering'] is True
    assert len(snapshot.run.metadata['base_sha']) == 40
    assert snapshot.tasks[0].metadata['task_type'] == 'engineering.materials'


def test_restart_recovers_unbound_controller_run(client):
    c, calls, store, product = client
    run, _ = product.orchestration.create_run(objective='unbound', actor='test', owner='founder', metadata={'engineering_request_id':'unbound'})
    with store.connect() as db:
        db.execute("INSERT INTO jobs VALUES('unbound','founder','materials_completeness',NULL,'creating',NULL)")
    store.interrupt(product)
    assert product.get_run(run.id).run.status == 'cancelled'
    with store.connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE request_id='unbound'").fetchone()
        assert row['run_id'] == str(run.id)
        assert row['state'] == 'interrupted'


def test_bridge_creation_reply_is_bound_and_deduplicated(tmp_path):
    import json
    import httpx
    from app.bridge.inbox import Inbox
    from app.bridge.runner import process_one
    run_id = str(uuid4())
    creations, replies = [], []
    def handler(request):
        if request.url.path.endswith('/receipt'):
            return httpx.Response(200, json={'service_received_at':'recorded'})
        if request.url.path.endswith('/engineering/runs'):
            creations.append(json.loads(request.content))
            assert request.headers['x-feishu-user'] == 'paired'
            return httpx.Response(202, json={'run_id':run_id, 'dispatch_status':'queued'})
        if request.url.path.endswith('/internal'):
            return httpx.Response(200, json={'tenant_access_token':'test-only'})
        replies.append(json.loads(json.loads(request.content)['content'])['text'])
        return httpx.Response(200, json={'code':0, 'data':{'message_id':'reply'}})
    inbox = Inbox(tmp_path / 'inbox.sqlite3')
    payload = dict(tenant='tenant', app='app', user='paired', message_id='new-message', event_id='new-event', text='创建 材料检查')
    assert inbox.accept(payload)
    assert not inbox.accept(payload | {'event_id':'redelivery'})
    config = dict(product_url='http://127.0.0.1:9000', bridge_token='test', app_id='app', app_secret='test')
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        assert process_one(inbox, config, transport)
        assert not process_one(inbox, config, transport)
    assert len(creations) == 1
    assert creations[0]['task'] == 'materials_completeness'
    assert run_id in replies[0]
    assert inbox.facts()[0]['state'] == 'replied'
