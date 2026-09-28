#!/usr/bin/env python3
"""Loopback-only engineering UI for one explicitly registered Run.

No product API, bridge, model, automatic approvals, or arbitrary reverse proxy.
Credentials stay in this trusted process. POSTs are never retried after dispatch.
"""
from __future__ import annotations

import argparse
import http.client
import http.server
import json
import re
import secrets
import threading
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

READ_SUFFIXES = {'', '/candidate', '/delivery', '/export'}
WRITE_SUFFIXES = {'/feedback', '/approve', '/reject', '/cancel', '/interrupt'}
ASSETS = {'/ui/assets/engineering.css', '/ui/assets/engineering.js'}
PROTECTED_RUN = '17c3e7a1-97cf-4eea-83d0-f79b3568e5da'


def permitted(method: str, path: str, run_id: str) -> bool:
    if '?' in path or '%' in path or '..' in path:
        return False
    if method == 'GET' and path in {'/ui/engineering', '/local/context'} | ASSETS:
        return True
    base = '/api/engineering/runs/' + run_id
    if not path.startswith(base):
        return False
    suffix = path[len(base):]
    return (method == 'GET' and suffix in READ_SUFFIXES) or (
        method == 'POST' and run_id != PROTECTED_RUN and suffix in WRITE_SUFFIXES)


def version_matches(body: dict, run: dict, suffix: str) -> bool:
    delivery = run.get('metadata', {}).get('delivery') or {}
    if suffix == '/interrupt':
        return body.get('base_sha') == run['metadata'].get('base_sha') and body.get('revision') == delivery.get('revision', 0)
    return all(body.get(k) == delivery.get(k) and body.get(k) is not None
               for k in ('revision', 'base_sha', 'patch_sha', 'approval_id'))


class Spark:
    def __init__(self, credentials: Path, known_hosts: Path, host: str, port: int, user: str) -> None:
        self.host, self.port, self.user = host, port, user
        self.credentials = credentials
        self.known_hosts = known_hosts
        self.lock = threading.Lock()
        self.client = None
        self.token = None

    def connection(self):
        import paramiko
        with self.lock:
            if self.client and self.client.get_transport() and self.client.get_transport().is_active():
                return self.client
            if self.client:
                self.client.close()
            text = self.credentials.read_text()
            match = re.search(r'(?:密码|password|口令)\s*[:：=]\s*(.+)', text, re.I)
            if match is None:
                raise RuntimeError('credential_reference_unavailable')
            password = match.group(1).strip().strip('\"\'`')
            client = paramiko.SSHClient()
            client.load_host_keys(str(self.known_hosts))
            client.connect(self.host, port=self.port, username=self.user,
                           password=password, look_for_keys=False, allow_agent=False, timeout=15)
            client.get_transport().set_keepalive(5)
            with client.open_sftp() as sftp:
                with sftp.open('/home/Developer/cofounder-t07-t10/bridge-config.json') as f:
                    self.token = json.load(f)['product_token']
            self.client = client
            return client

    def request(self, method: str, path: str, body: bytes = b''):
        client = self.connection()  # Reconnect only before dispatch; never replay a POST.
        channel = client.get_transport().open_channel('direct-tcpip', ('127.0.0.1', 9000), ('127.0.0.1', 0), timeout=15)
        channel.settimeout(30)
        try:
            headers = (f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:9000\r\n'
                       f'Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n'
                       f'Content-Length: {len(body)}\r\nConnection: close\r\n\r\n')
            channel.sendall(headers.encode() + body)
            response = http.client.HTTPResponse(channel)
            response.begin()
            return response.status, response.getheader('Content-Type', 'application/json'), response.read()
        finally:
            channel.close()


class Proxy(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, spark, run_id: str):
        self.spark = spark
        self.run_id = run_id
        self.session = secrets.token_urlsafe(32)
        self.owner = None
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'


class Handler(http.server.BaseHTTPRequestHandler):
    server: Proxy

    def log_message(self, *args):
        pass  # No credentials, payloads, or approval bodies in access logs.

    def reply(self, status: int, data: bytes, content_type='application/json', session=False):
        print(json.dumps({'method': self.command, 'status': status}), flush=True)
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if session:
            self.send_header('Set-Cookie', f'u01={self.server.session}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, reason: str):
        self.reply(status, json.dumps({'error': reason}, ensure_ascii=False).encode())

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        origin = self.server.origin
        if self.headers.get('Host') != urlsplit(origin).netloc:
            self.error(403, '本机地址不匹配')
            return
        if self.headers.get('Origin') not in (None, origin) or self.headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
            self.error(403, '请求来源不允许')
            return
        if not permitted(self.command, self.path, self.server.run_id):
            self.error(403, '此入口只允许已登记的验收 Run 和工程操作')
            return
        landing = self.command == 'GET' and self.path == '/ui/engineering'
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except Exception:
            self.error(403, '本机会话无效')
            return
        value = cookie.get('u01')
        if not landing and (not value or not secrets.compare_digest(value.value, self.server.session)):
            self.error(403, '请先打开本机工程页面')
            return
        if self.command == 'POST' and self.headers.get('Origin') != origin:
            self.error(403, '写操作必须来自本机页面')
            return
        if self.headers.get('Transfer-Encoding'):
            self.error(400, '不支持流式请求')
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 8192:
                raise ValueError()
            raw = self.rfile.read(length)
            if self.command == 'POST':
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    self.error(415, '仅接受 JSON')
                    return
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError()
                status, _, payload = self.server.spark.request('GET', '/api/engineering/runs/' + self.server.run_id)
                if status != 200:
                    self.error(status, '远程身份或 Run 不可用')
                    return
                run = json.loads(payload)['snapshot']['run']
                if self.server.owner is None or run['owner'] != self.server.owner:
                    self.error(403, 'Run 身份不匹配；请重新加载页面')
                    return
                suffix = self.path.rsplit('/', 1)[-1]
                if not version_matches(body, run, '/' + suffix):
                    self.error(409, '版本已失效，请重新加载')
                    return
        except (ValueError, KeyError):
            self.error(400, '无效请求')
            return
        except Exception:
            self.error(502, '远程连接失败；未自动重试，请加载真实状态后再操作')
            return
        if self.path == '/local/context':
            self.reply(200, json.dumps({'run_id': self.server.run_id, 'remote': 'DGX Spark · 既有 9000 工程服务', 'production_commit': '57502708e30e8db9fcb72c508f37b04de590a2dd', 'model': 'Qwen3.5（本阶段核验；本次实际模型见下方执行记录）', 'user_accepted': False}).encode())
            return
        try:
            status, content_type, payload = self.server.spark.request(self.command, self.path, raw)
            if status == 200 and self.path == '/api/engineering/runs/' + self.server.run_id:
                run = json.loads(payload)['snapshot']['run']
                if self.server.owner is not None and self.server.owner != run['owner']:
                    self.error(403, 'Run 身份改变')
                    return
                self.server.owner = run['owner']
            if status == 200 and landing:
                html = payload.decode()
                html = html.replace('<label>访问令牌 <input id="token" type="password" autocomplete="off"></label>', '<input id="token" type="hidden" value="">')
                html = html.replace('<input id="run">', f'<input id="run" value="{self.server.run_id}" readonly>')
                html = html.replace('<h1>工程产物审阅</h1>', '<h1>DGX Spark · 工程产物审阅</h1><p>真实远程服务 · 仅本机访问 · 当前验收 Run。修复需在任务创建后 10 分钟内发起并完成，批准截止时间以当前版本为准；超时请在飞书创建新任务。创建新任务可在飞书发送“创建 材料检查”；此入口只登记下方 Run。</p><p id="connection"></p>')
                html = html.replace('<h2>定位反馈</h2>', '<h2>实际执行、测试和独立审查</h2><pre id="checks"></pre><h2>定位反馈</h2>')
                html = html.replace('令牌仅保留在当前页面内存中。', '服务凭据只在本机代理进程中。最终批准请本人点击；自测不会代为批准。断线或失败请重新加载真实状态。')
                payload = html.encode()
            if status == 200 and self.path == '/ui/assets/engineering.js':
                js = payload.decode().replace("el('diff').textContent=Object.entries(preview.files)", "el('checks').textContent=JSON.stringify({executor:{model:preview.result.executor?.selected_model,provider:preview.result.executor?.selected_provider},tests:preview.result.tests.map(t=>({passed:t.gate?.passed,summary:t.gate?.summary,cases:t.gate?.cases?.length,exit_code:t.exit_code})),reviewer:{model:preview.result.reviewer?.selected_model,provider:preview.result.reviewer?.selected_provider},review:preview.result.review},null,2);el('diff').textContent=Object.entries(preview.files)")
                js = js.replace("current=data.snapshot.run.metadata.delivery;", "current=data.snapshot.run.metadata.delivery;el('feedback').dataset.attempts=String(Math.max(0,...data.snapshot.tasks.map(t=>t.attempt_count)));")
                js = js.replace("el(name).onclick=async()=>{", "el(name).addEventListener('click',async()=>{el('status').textContent='正在请求远程服务…';").replace("el('status').textContent=error.message;}};", "el('status').textContent=error.message;}});")
                js += '''\nconst remoteLoad=load;load=async()=>{await remoteLoad();const pending=current?.state==='pending'&&new Date(current.expires_at)>new Date();for(const id of ['approve','reject','feedback'])el(id).disabled=!pending;el('export').disabled=current?.state!=='approved';el('feedback').disabled=!pending||Number(el('feedback').dataset.attempts)>=2;el('feedback').title=Number(el('feedback').dataset.attempts)>=2?'本Run尝试次数已耗尽，不能再修复':'';};
for(const id of ['approve','reject','feedback','export'])el(id).disabled=true;
fetch('/local/context').then(r=>r.json()).then(c=>{el('connection').textContent=c.remote+' · '+c.model+' · 部署 '+c.production_commit.slice(0,7);return load();}).catch(e=>{el('status').textContent=e.message;});
'''
                payload = js.encode()
            self.reply(status, payload, content_type, session=landing and status == 200)
        except Exception:
            self.error(502, '远程连接失败；操作结果可能未返回，请加载真实状态核对，未自动重试')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--ssh-host', required=True)
    parser.add_argument('--ssh-port', type=int, default=22)
    parser.add_argument('--ssh-user', required=True)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--known-hosts', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    from uuid import UUID
    import os
    if str(UUID(args.run)) != args.run:
        parser.error('canonical Run UUID required')
    spark = Spark(args.credentials, args.known_hosts, args.ssh_host, args.ssh_port, args.ssh_user)
    spark.connection()
    server = Proxy(args.port, spark, args.run)
    args.state.write_text(json.dumps({'pid': os.getpid(), 'url': server.origin + '/ui/engineering', 'run_id': args.run}))
    print(json.dumps({'url': server.origin + '/ui/engineering', 'run_id': args.run}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
