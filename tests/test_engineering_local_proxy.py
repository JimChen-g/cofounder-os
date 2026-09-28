"""CPU-only local proxy boundaries; no sockets, remote dispatch, or human approval."""
import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('engineering_local_proxy', Path(__file__).parents[1] / 'scripts/engineering_local_proxy.py')
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)
RUN = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
BASE = '/api/engineering/runs/' + RUN
VERSION = dict(revision=2, base_sha='b' * 40, patch_sha='c' * 64, approval_id='approval')


class FakeSpark:
    def __init__(self):
        self.calls = []
        self.owner = 'founder'
        self.fail_write = False

    def request(self, method, path, body=b''):
        self.calls.append((method, path, body))
        if method == 'POST' and self.fail_write:
            raise TimeoutError('uncertain write')
        if path == BASE:
            data = {'snapshot': {'run': {'owner': self.owner, 'metadata': {'base_sha': VERSION['base_sha'], 'delivery': VERSION}}}}
        else:
            data = {'ok': True}
        return 200, 'application/json', json.dumps(data).encode()


def invoke(method='GET', path=BASE, body=None, headers=None, owner='founder', spark=None):
    handler = proxy.Handler.__new__(proxy.Handler)
    handler.server = SimpleNamespace(origin='http://127.0.0.1:12345', run_id=RUN, session='secret-session', owner=owner, spark=spark or FakeSpark())
    raw = json.dumps(body).encode() if body is not None else b''
    values = {'Host': '127.0.0.1:12345', 'Cookie': 'u01=secret-session', 'Content-Length': str(len(raw))}
    if method == 'POST':
        values.update({'Origin': handler.server.origin, 'Content-Type': 'application/json'})
    values.update(headers or {})
    handler.headers = Message()
    for key, value in values.items():
        if value is not None:
            handler.headers[key] = value
    handler.command, handler.path, handler.rfile = method, path, io.BytesIO(raw)
    handler.reply = lambda status, data, content_type='application/json', session=False: setattr(handler, 'result', (status, data, session))
    handler.forward()
    return handler


@pytest.mark.parametrize('path', ['/api/runs', '/api/engineering/runs/other', BASE + '/candidate?x=1', BASE + '/%2e%2e', BASE + '/../export', BASE + 'suffix', BASE + '/feedback/'])
def test_unregistered_paths_never_dispatch(path):
    h = invoke(path=path)
    assert h.result[0] == 403
    assert not h.server.spark.calls


@pytest.mark.parametrize('headers', [{'Host': 'evil.example'}, {'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}, {'Cookie': None}, {'Cookie': 'u01=wrong'}])
def test_origin_host_and_session_fail_closed(headers):
    h = invoke(headers=headers)
    assert h.result[0] == 403
    assert not h.server.spark.calls


def test_landing_alone_can_bootstrap_session():
    assert invoke(path='/ui/engineering', headers={'Cookie': None}).result[0] == 200
    h = invoke(path='/local/context', headers={'Cookie': None})
    assert h.result[0] == 403 and not h.server.spark.calls


@pytest.mark.parametrize('headers,status', [({'Origin': None}, 403), ({'Content-Type': 'text/plain'}, 415), ({'Transfer-Encoding': 'chunked'}, 400), ({'Content-Length': '-1'}, 400), ({'Content-Length': '8193'}, 400), ({'Content-Length': 'invalid'}, 400)])
def test_write_transport_boundary(headers, status):
    h = invoke('POST', BASE + '/approve', VERSION, headers)
    assert h.result[0] == status
    assert not h.server.spark.calls


def test_current_identity_and_version_required_before_write():
    for owner, body, status in [(None, VERSION, 403), ('other', VERSION, 403), ('founder', VERSION | {'revision': 1}, 409), ('founder', {}, 409)]:
        h = invoke('POST', BASE + '/approve', body, owner=owner)
        assert h.result[0] == status
        assert [c[0] for c in h.server.spark.calls] == ['GET']


def test_exact_write_is_dispatched_once_and_uncertain_write_not_replayed():
    spark = FakeSpark()
    spark.fail_write = True
    h = invoke('POST', BASE + '/approve', VERSION, spark=spark)
    assert h.result[0] == 502
    assert [c[0] for c in spark.calls] == ['GET', 'POST']
    assert json.loads(spark.calls[1][2]) == VERSION


def test_read_pins_owner_then_rejects_owner_change():
    h = invoke(owner=None)
    assert h.result[0] == 200 and h.server.owner == 'founder'
    h = invoke(owner='other')
    assert h.result[0] == 403


def test_protected_historical_run_is_read_only():
    path = '/api/engineering/runs/' + proxy.PROTECTED_RUN
    for suffix in proxy.WRITE_SUFFIXES:
        assert not proxy.permitted('POST', path + suffix, proxy.PROTECTED_RUN)
    assert proxy.permitted('GET', path + '/export', proxy.PROTECTED_RUN)


def test_interrupt_requires_current_base_and_revision():
    run = {'metadata': {'base_sha': VERSION['base_sha'], 'delivery': VERSION}}
    assert proxy.version_matches({'base_sha': VERSION['base_sha'], 'revision': 2}, run, '/interrupt')
    assert not proxy.version_matches({'base_sha': 'wrong', 'revision': 2}, run, '/interrupt')
    assert not proxy.version_matches({'base_sha': VERSION['base_sha'], 'revision': 1}, run, '/interrupt')


def test_reply_sets_noncacheable_csp_and_http_only_cookie():
    h = proxy.Handler.__new__(proxy.Handler)
    h.server = SimpleNamespace(session='secret-session')
    h.command = 'GET'
    captured = {}
    h.send_response = lambda status: captured.update(status=status)
    h.send_header = lambda name, value: captured.update({name: value})
    h.end_headers = lambda: None
    h.wfile = io.BytesIO()
    h.reply(200, b'ok', session=True)
    assert captured['Cache-Control'] == 'no-store'
    assert "frame-ancestors 'none'" in captured['Content-Security-Policy']
    assert 'HttpOnly' in captured['Set-Cookie'] and 'SameSite=Strict' in captured['Set-Cookie']


def test_actual_asset_transform_hides_token_summarizes_and_gates_attempts():
    class AssetSpark(FakeSpark):
        token = 'sensitive-server-token'

        def request(self, method, path, body=b''):
            assert method == 'GET'
            source = 'engineering.html' if path == '/ui/engineering' else 'engineering.js'
            payload = (Path(__file__).parents[1] / 'app/ui/static' / source).read_bytes()
            return 200, 'text/html' if source.endswith('html') else 'application/javascript', payload

    spark = AssetSpark()
    html = invoke(path='/ui/engineering', spark=spark).result[1].decode()
    js = invoke(path='/ui/assets/engineering.js', spark=spark).result[1].decode()
    assert spark.token not in html + js
    assert '<input id="token" type="hidden" value="">' in html
    assert 'type="password"' not in html
    assert f'value="{RUN}" readonly' in html and 'id="checks"' in html
    assert 'tests:preview.result.tests.map' in js and 'executor:{model:' in js
    assert 'dataset.attempts=String(Math.max(0,...data.snapshot.tasks.map(t=>t.attempt_count)))' in js
    assert "el('feedback').disabled=!pending||Number(el('feedback').dataset.attempts)>=2" in js
    assert '本Run尝试次数已耗尽，不能再修复' in js
    assert "addEventListener('click'" in js


def test_context_uses_live_deployment_not_hardcoded_commit():
    class HealthSpark(FakeSpark):
        def request(self, method, path, body=b''):
            assert (method, path) == ('GET', '/health')
            return 200, 'application/json', b'{"deployment_commit":"new-live-commit"}'
    h = invoke(path='/local/context', spark=HealthSpark())
    assert json.loads(h.result[1])['production_commit'] == 'new-live-commit'


def test_connection_uses_key_known_hosts_and_private_local_token(tmp_path, monkeypatch):
    import sys
    calls = {}
    class Client:
        def load_host_keys(self, path): calls['hosts'] = path
        def set_missing_host_key_policy(self, policy): calls['reject_unknown'] = True
        def connect(self, host, **kwargs): calls.update(kwargs)
        def get_transport(self): return SimpleNamespace(set_keepalive=lambda _: None)
    monkeypatch.setitem(sys.modules, 'paramiko', SimpleNamespace(SSHClient=Client, RejectPolicy=object))
    token = tmp_path/'token'
    token.write_text('scoped-token')
    token.chmod(0o600)
    spark = proxy.Spark(tmp_path/'ssh-key', tmp_path/'hosts', 'host', 22, 'user', token_file=token)
    spark.connection()
    assert calls['key_filename'] == str(tmp_path/'ssh-key')
    assert calls['reject_unknown'] and 'password' not in calls
    assert spark.token == 'scoped-token'
    token.chmod(0o644)
    spark.client = None
    with pytest.raises(RuntimeError, match='private'):
        spark.connection()
