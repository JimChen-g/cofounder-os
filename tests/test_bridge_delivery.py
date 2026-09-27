"""Isolated transport tests; these do not constitute phone approval."""
import json
from uuid import uuid4

import httpx

from app.bridge.delivery import Notifications, command

RID, AID = str(uuid4()), str(uuid4())
D = dict(revision=2, base_sha='a'*40, patch_sha='b'*64, approval_id=AID,
         artifact_id=str(uuid4()), state='pending', expires_at='2099-01-01T00:00:00Z')
CONFIG = dict(product_url='http://localhost', app_id='a', app_secret='s',
              bridge_token='b', tenant='t', open_id='u')


def payload(text):
    return dict(text=text, tenant='t', app='a', message_id='m')


def test_commands_bind_exact_approval_and_version():
    writes = []
    def handle(req):
        if req.method == 'GET':
            return httpx.Response(200, json={'delivery': D})
        writes.append(json.loads(req.content))
        return httpx.Response(200, json={'revision': 2})
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        assert command(payload('评论 looks good'), CONFIG, c, {}) is None
        assert '失效' in command(payload(f'批准 {RID} {uuid4()}'), CONFIG, c, {})
        assert not writes
        assert 'Controller' in command(payload(f'批准 {RID} {AID}'), CONFIG, c, {})
        command(payload(f'批准 {RID} {AID}'), CONFIG, c, {})
    assert writes[0] == writes[1]
    assert writes[0]['revision'] == 2 and writes[0]['patch_sha'] == D['patch_sha']
    assert writes[0]['approval_id'] == AID


def test_expired_cancelled_or_unauthorized_never_claim_success():
    def handle(req):
        return httpx.Response(200, json={'delivery': D}) if req.method == 'GET' else httpx.Response(409, json={'error': 'expired'})
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        assert '未推进' in command(payload(f'批准 {RID} {AID}'), CONFIG, c, {})
        assert '格式' in command(payload('批准 nope'), CONFIG, c, {})


def test_notifications_only_paired_user_and_persisted_dedup(tmp_path):
    sent = []
    def handle(req):
        if req.url.path.endswith('/notifications'):
            return httpx.Response(200, json={'items': [{'run_id': RID, 'delivery': D}]})
        if req.url.path.endswith('/internal'):
            return httpx.Response(200, json={'tenant_access_token': 'fixture'})
        sent.append(json.loads(req.content))
        return httpx.Response(200, json={'code': 0, 'data': {'message_id': 'n'}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        Notifications(tmp_path/'n.db').send_pending(CONFIG, c)
        Notifications(tmp_path/'n.db').send_pending(CONFIG, c)
    assert len(sent) == 1 and sent[0]['receive_id'] == 'u'
    assert AID in sent[0]['content']


def test_export_sends_actual_approved_artifact_and_never_unapproved():
    from app.bridge.delivery import send_export_attachment
    sent = []
    approved = {'approval': {'revision': 2}, 'result': {'code_diff': 'actual-approved-patch'}}
    def handle(req):
        if req.method == 'GET':
            return httpx.Response(200, json=approved)
        sent.append(req)
        if req.url.path.endswith('/files'):
            assert b'actual-approved-patch' in req.content
            return httpx.Response(200, json={'code':0,'data':{'file_key':'f'}})
        assert json.loads(req.content)['msg_type'] == 'file'
        return httpx.Response(200, json={'code':0,'data':{'message_id':'attachment'}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as c:
        assert send_export_attachment(payload(f'导出 {RID}'), CONFIG, c, {}, 'fixture') == 'attachment'
    assert len(sent) == 2
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(409))) as c:
        assert send_export_attachment(payload(f'导出 {RID}'), CONFIG, c, {}, 'fixture') is None
