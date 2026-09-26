"""Manage only this deployment, never the historical model or unrelated jobs.

Run on Spark: python scripts/manage_t07_t10.py status|start|stop|restart
Set COFOUNDER_RUNTIME_ROOT if not using ~/cofounder-t07-t10.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

root = Path(os.environ.get('COFOUNDER_RUNTIME_ROOT', str(Path.home()/'cofounder-t07-t10')))
action = sys.argv[1]
if action not in {'start', 'stop', 'restart', 'status'}:
    raise SystemExit('Expected start, stop, restart, or status')
if action in {'stop', 'restart'}:
    for name in ('bridge', 'product'):
        path = root/(name+'.pid')
        if path.exists():
            pid = int(path.read_text())
            # Refuse to kill a recycled PID belonging to another command.
            cmdline = Path(f'/proc/{pid}/cmdline')
            if cmdline.exists() and str(root/'venv/bin/python').encode() in cmdline.read_bytes():
                os.kill(pid, signal.SIGTERM)
                for _ in range(60):
                    if not cmdline.exists():
                        break
                    time.sleep(.5)
                if cmdline.exists():
                    raise SystemExit(f'{name} has not stopped; no forced kill performed')
            path.unlink(missing_ok=True)
if action in {'start', 'restart'}:
    config = root/'bridge-config.json'
    if config.stat().st_mode & 0o077:
        raise SystemExit('Runtime config must be mode 0600')
    c = json.loads(config.read_text())
    env = dict(os.environ, PRODUCT_API_TOKEN=c['product_token'],
               PRODUCT_API_BRIDGE_TOKEN=c['bridge_token'], PRODUCT_FOUNDER_ID='founder',
               FEISHU_APP_ID=c['app_id'], FEISHU_OPEN_ID=c['open_id'],
               FEISHU_TENANT_FILE=str(root/'bridge/identity.json'), PRODUCT_DATA_DIR=str(root/'data'),
               AUDIT_DIR=str(root/'audit'), GATEWAY_API_KEY=c['gateway_token'],
               QWEN_API_KEY=(root/'model.key').read_text().strip(), QWEN_MODEL='qwen3.5',
               QWEN_BASE_URL='http://127.0.0.1:8000/v1', STEP_API_KEY=c['step_key'],
               STEP_BASE_URL='https://api.stepfun.com/step_plan/v1', STEP_MODEL='step-3.7-flash',
               COFOUNDER_BRIDGE_CONFIG=str(config))
    for name, args in [('product', ['uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '9000']),
                       ('bridge', ['app.bridge.runner'])]:
        pidfile = root/(name+'.pid')
        if pidfile.exists() and Path('/proc/'+pidfile.read_text().strip()).exists():
            continue
        with (root/(name+'.log')).open('a') as log:
            p = subprocess.Popen([str(root/'venv/bin/python'), '-m', *args], env=env,
                                 cwd=root/'src', stdout=log, stderr=log, start_new_session=True)
        pidfile.write_text(str(p.pid))
if action == 'status':
    for name in ('product', 'bridge'):
        p = root/(name+'.pid')
        print(name, 'running' if p.exists() and Path('/proc/'+p.read_text().strip()).exists() else 'stopped')
    status = root/'bridge/status.json'
    if status.exists():
        print(status.read_text())
