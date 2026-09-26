from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import httpx
import pytest

from app.bridge.inbox import Inbox
from app.bridge.runner import process_one, validate_event


def payload():
    return dict(tenant='t', app='a', user='u', message_id='m', event_id='e', text='ping')


def test_persistent_dedup_conflict_and_one_consumer(tmp_path):
    inbox = Inbox(tmp_path/'inbox.db')
    assert inbox.accept(payload())
    assert not inbox.accept(payload() | {'event_id':'redelivery'})
    inbox = Inbox(tmp_path/'inbox.db')
    assert not inbox.accept(payload())
    with pytest.raises(ValueError):
        inbox.accept(payload() | {'text':'tamper'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: inbox.claim(), range(4)))
    assert sum(x is not None for x in claimed) == 1
    restarted = Inbox(tmp_path/'inbox.db')
    assert restarted.claim() is None
    assert restarted.facts()[0]['state'] == 'uncertain'
    assert restarted.facts()[0]['attempts'] == 1


def test_identity_requires_all_verified_fields():
    config = dict(app_id='a', tenant='t', open_id='u')
    data = NS(header=NS(app_id='a', tenant_key='t', event_id='e'), event=NS(
        sender=NS(sender_type='user', sender_id=NS(open_id='u')),
        message=NS(chat_type='p2p', message_type='text', message_id='m', content='{"text":"ping"}')))
    assert validate_event(data, config) == payload()
    for key in config:
        assert validate_event(data, config | {key:'other'}) is None
    data.event.message.chat_type = 'group'
    assert validate_event(data, config) is None


def test_async_worker_facts_and_no_replay(tmp_path):
    inbox = Inbox(tmp_path/'inbox.db')
    inbox.accept(payload())
    calls = []
    def handle(req):
        calls.append(req.url.path)
        if req.url.path.endswith('/receipt'):
            return httpx.Response(200, json={'service_received_at':'actual-service-time'})
        if req.url.path.endswith('/internal'):
            return httpx.Response(200, json={'tenant_access_token':'test-token'})
        return httpx.Response(200, json={'code':0,'data':{'message_id':'reply'}})
    config = dict(product_url='http://127.0.0.1:9000', bridge_token='token', app_id='a', app_secret='secret')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert process_one(inbox, config, client)
        assert not process_one(inbox, config, client)
    facts = inbox.facts()[0]
    assert facts['state'] == 'replied' and facts['attempts'] == 1
    assert facts['service_received_at'] == 'actual-service-time'
    assert facts['agent_consumed_at'] is None and facts['read_state'] == 'unknown'
    assert len(calls) == 3
