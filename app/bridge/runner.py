"""Feishu WSS transport with bounded asynchronous receipt/reply handling.

Run only one instance for the paired application. Config is a mode-0600 JSON
file outside the checkout, containing app_id, app_secret, tenant, open_id,
product_url, bridge_token and state_dir. Only supported engineering creation/query.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.bridge.inbox import Inbox, now


def validate_event(data: Any, config: dict[str, Any]) -> dict[str, str] | None:
    event = data.event
    message = event.message
    sender = event.sender
    if (data.header.app_id != config['app_id'] or (config.get('tenant') and data.header.tenant_key != config['tenant'])
            or not data.header.tenant_key or sender.sender_type != 'user' or sender.sender_id.open_id != config['open_id']
            or message.chat_type != 'p2p' or message.message_type != 'text'):
        return None
    text = json.loads(message.content).get('text')
    if not isinstance(text, str) or not message.message_id or len(text) > 8000:
        return None
    return {'app': config['app_id'], 'tenant': data.header.tenant_key, 'user': config['open_id'],
            'message_id': message.message_id, 'event_id': data.header.event_id, 'text': text}


def business_reply(payload: dict[str, str], config: dict[str, Any], client: httpx.Client, headers: dict[str, str]) -> str:
    from app.bridge.delivery import command
    delivery_reply = command(payload, config, client, headers)
    if delivery_reply is not None:
        return delivery_reply
    text = payload['text'].strip()
    if text == '创建 材料检查':
        request_id = hashlib.sha256(('/'.join(payload[k] for k in ('tenant', 'app', 'message_id'))).encode()).hexdigest()
        response = client.post(config['product_url'] + '/api/engineering/runs', headers=headers,
                               json={'request_id': request_id, 'task': 'materials_completeness'})
        response.raise_for_status()
        result = response.json()
        return '材料检查任务已受理。Run ID: ' + str(result['run_id']) + '。查询状态请发送：查询 ' + str(result['run_id'])
    if text.startswith('查询 '):
        try:
            run_id = str(uuid.UUID(text[3:].strip()))
        except ValueError:
            return 'Run ID 格式无效。请发送：查询 <Run UUID>'
        response = client.get(config['product_url'] + '/api/engineering/runs/' + run_id, headers=headers)
        if response.status_code == 404:
            return '未找到本人可查询的任务。'
        response.raise_for_status()
        result = response.json()
        return 'Run ID: ' + run_id + '。Controller 状态：' + str(result['snapshot']['run']['status']) + '；执行状态：' + str(result['dispatch_status']) + '。检查完成不代表交付已批准。'
    return '仅支持：创建 材料检查；查询 <Run UUID>。详情/导出 <Run UUID>；批准/驳回/取消 <Run UUID> <Approval UUID>。'


def process_one(inbox: Inbox, config: dict[str, Any], client: httpx.Client) -> bool:
    row = inbox.claim()
    if row is None:
        return False
    key = row['message_key']
    payload = json.loads(row['payload'])
    reply_started = False
    try:
        headers = {'Authorization': 'Bearer ' + config['bridge_token'],
                   'X-Feishu-App': payload['app'], 'X-Feishu-Tenant': payload['tenant'],
                   'X-Feishu-User': payload['user']}
        receipt = client.post(config['product_url'] + '/api/bridge/receipt', headers=headers,
                              json={'message_id': payload['message_id'], 'body_sha256': row['payload_hash']})
        receipt.raise_for_status()
        facts = receipt.json()
        inbox.record(key, service_received_at=facts['service_received_at'])
        reply = business_reply(payload, config, client, headers)
        auth = client.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                           json={'app_id': config['app_id'], 'app_secret': config['app_secret']})
        auth.raise_for_status()
        token = auth.json()['tenant_access_token']
        from app.bridge.delivery import send_export_attachment
        attachment_id = send_export_attachment(payload, config, client, headers, token)
        if attachment_id:
            inbox.record(key, export_message_id=attachment_id)
        body = {'msg_type': 'text', 'content': json.dumps({'text': reply}, ensure_ascii=False),
                'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, key))}
        # Stable UUID on bounded transport retry; never replay the business consumer.
        reply_started = True
        for attempt in range(2):
            try:
                response = client.post('https://open.feishu.cn/open-apis/im/v1/messages/' + payload['message_id'] + '/reply',
                                       headers={'Authorization': 'Bearer ' + token}, json=body)
                response.raise_for_status()
                result = response.json()
                if result.get('code') != 0:
                    raise RuntimeError('platform_rejected')
                inbox.record(key, state='replied', platform_accepted_at=now(), replied_at=now(),
                             reply_message_id=result['data']['message_id'])
                return True
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 1:
                    raise
        return True
    except Exception as exc:
        inbox.record(key, state='failed', error=type(exc).__name__)
        if not reply_started:
            # A single best-effort warning; never repeat the business request or
            # send a contradictory warning after an uncertain normal reply.
            try:
                auth = client.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
                                   json={'app_id': config['app_id'], 'app_secret': config['app_secret']})
                auth.raise_for_status()
                warning = '请求处理未确认完成，未自动重放操作。请发送“详情 <Run UUID>”核对真实状态；创建任务未返回 ID 时请先在本机查看任务清单，勿重复提交。'
                response = client.post('https://open.feishu.cn/open-apis/im/v1/messages/' + payload['message_id'] + '/reply',
                    headers={'Authorization': 'Bearer ' + auth.json()['tenant_access_token']},
                    json={'msg_type': 'text', 'content': json.dumps({'text': warning}, ensure_ascii=False),
                          'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, key + '/failure-warning'))})
                response.raise_for_status()
                result = response.json()
                if result.get('code') == 0:
                    inbox.record(key, platform_accepted_at=now(), replied_at=now(), reply_message_id=result['data']['message_id'])
            except Exception:
                pass  # Keep original failure evidence; an uncertain warning is not retried.
        return True


def run(config: dict[str, Any]) -> None:
    # All raw SDK logs disabled: websocket URLs include credential material.
    import lark_oapi as lark  # type: ignore[import-untyped]
    from lark_oapi.ws.client import loop  # type: ignore[import-untyped]
    logging.disable(logging.CRITICAL)
    url = urlparse(config['product_url'])
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost')):
        raise ValueError('Product API requires TLS or loopback')
    root = Path(config['state_dir'])
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (root / 'bridge.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    identity = root / 'identity.json'
    if identity.exists():
        config['tenant'] = json.loads(identity.read_text())['tenant']
    inbox = Inbox(root / 'inbox.sqlite3')
    stopped = threading.Event()
    status: dict[str, Any] = {'started_at': now(), 'pid': os.getpid(), 'connection': 'starting',
                              'reconnects': 0, 'received': 0, 'duplicates': 0}
    status_lock = threading.Lock()

    def record(**values: Any) -> None:
        with status_lock:
            status.update(values)
            temporary = root / 'status.tmp'
            temporary.write_text(json.dumps(status, ensure_ascii=False))
            temporary.replace(root / 'status.json')

    def receive(data: Any) -> None:
        start = time.monotonic()
        payload = validate_event(data, config)
        if payload is None:
            return
        if not config.get('tenant'):
            config['tenant'] = payload['tenant']
            identity = root / 'identity.json'
            identity.write_text(json.dumps({'tenant': payload['tenant']}))
            identity.chmod(0o600)
        inserted = inbox.accept(payload)  # persistence errors propagate: SDK sends non-200
        record(received=status['received'] + int(inserted),
               duplicates=status['duplicates'] + int(not inserted),
               last_callback_ms=(time.monotonic()-start)*1000,
               last_message_fingerprint=hashlib.sha256(payload['message_id'].encode()).hexdigest()[:16])

    from app.bridge.delivery import Notifications
    notifications = Notifications(root / 'notifications.sqlite3')

    def worker() -> None:
        next_notification = 0.0
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            while not stopped.is_set():
                try:
                    process_one(inbox, config, client)
                    if time.monotonic() >= next_notification:
                        next_notification = time.monotonic() + 10
                        notifications.send_pending(config, client)
                except Exception as exc:
                    record(worker_error=type(exc).__name__)
                stopped.wait(0.2)

    handler = lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(receive).build()
    client = lark.ws.Client(config['app_id'], config['app_secret'], event_handler=handler, auto_reconnect=True)
    client.on_reconnecting = lambda: record(connection='reconnecting')
    client.on_reconnected = lambda: record(connection='connected', reconnects=status['reconnects']+1)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    # SDK connect/write paths are tested at the pinned version. Initial connect
    # is explicit so readiness is evidence of completed handshake.
    async def main() -> None:
        import asyncio
        await asyncio.wait_for(client._connect(), 30)
        record(connection='connected', connected_at=now())
        ping = asyncio.create_task(client._ping_loop())
        while not stopped.is_set():
            await asyncio.sleep(0.25)
        ping.cancel()
        await client._disconnect()

    def stop(*_: Any) -> None:
        stopped.set()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # Explicit test hook disconnects only this session; SDK handles reconnection.
    def reconnect(*_: Any) -> None:
        if client._conn is not None:
            loop.create_task(client._conn.close())
    signal.signal(signal.SIGUSR1, reconnect)
    record()
    try:
        loop.run_until_complete(main())
    finally:
        stopped.set()
        thread.join(timeout=25)
        record(connection='stopped', stopped_at=now())
        lock.close()


if __name__ == '__main__':
    config_path = Path(os.environ['COFOUNDER_BRIDGE_CONFIG'])
    if config_path.stat().st_mode & 0o077:
        raise SystemExit('Bridge config must be mode 0600')
    run(json.loads(config_path.read_text()))
