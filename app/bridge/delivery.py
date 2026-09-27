"""Text delivery commands. Version authority remains in the Workflow Controller."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import httpx


def details(run_id: str, d: dict[str, Any]) -> str:
    return (f"Run: {run_id}\n版本: {d['revision']}\n状态: {d['state']}\n"
            f"Patch: {d['patch_sha']}\n产物: {d['artifact_id']}\n"
            f"有效至: {d['expires_at']}\n"
            f"批准 {run_id} {d['approval_id']}\n驳回 {run_id} {d['approval_id']}\n"
            "请先查看详情并核对产物。评论不等于批准。")


def command(payload: dict[str, str], config: dict[str, Any], client: httpx.Client,
            headers: dict[str, str]) -> str | None:
    parts = payload['text'].strip().split()
    if not parts or parts[0] not in {'详情', '批准', '驳回', '取消', '导出'}:
        return None
    verb = parts[0]
    if len(parts) != (3 if verb in {'批准', '驳回', '取消'} else 2):
        return '格式：详情/导出 <Run UUID>；批准/驳回/取消 <Run UUID> <Approval UUID>'
    try:
        rid = str(uuid.UUID(parts[1]))
        approval = str(uuid.UUID(parts[2])) if len(parts) == 3 else None
    except ValueError:
        return 'ID 格式无效。请复制详情中的完整命令。'
    base = config['product_url'] + '/api/engineering/runs/' + rid
    if verb == '导出':
        response = client.get(base + '/export', headers=headers)
        if response.status_code in {403, 404, 409}:
            return '无法导出：仅本人已批准的当前版本可导出。'
        response.raise_for_status()
        result = response.json()
        return f"已导出 Run: {rid}\n版本: {result['approval']['revision']}\nPatch: {result['result']['patch_sha']}\n候选提交: {result['result']['candidate_commit']}\n可在任务页面下载完整产物。"
    response = client.get(base + '/delivery', headers=headers)
    if response.status_code in {403, 404}:
        return '未找到本人可操作的任务。'
    response.raise_for_status()
    d = response.json().get('delivery')
    if not d:
        return '当前任务尚无可审批产物。'
    if verb == '详情':
        candidate = client.get(base + '/candidate', headers=headers)
        if candidate.status_code != 200:
            return details(rid, d) + '\n产物详情暂不可用，请勿在未查看产物时批准。'
        result = candidate.json()['result']
        diff = str(result['code_diff'])
        return (details(rid, d) + '\n独立审查: ' + str(result['review']['conclusion'])
                + '\n测试退出码: ' + str([t['exit_code'] for t in result['tests']])
                + '\nDiff:\n' + diff[:6000]
                + ('\n[内容截断，请在 Web 查看完整候选]' if len(diff) > 6000 else ''))
    if approval != d['approval_id']:
        return '审批 ID 已失效：请查询当前版本详情。'
    action = {'批准': 'approve', '驳回': 'reject', '取消': 'cancel'}[verb]
    body = {k: d[k] for k in ('revision', 'base_sha', 'patch_sha', 'approval_id')}
    body['request_id'] = 'feishu:' + hashlib.sha256('/'.join(payload[k] for k in ('tenant', 'app', 'message_id')).encode()).hexdigest()
    response = client.post(base + '/' + action, headers=headers, json=body)
    if response.status_code in {403, 404, 409}:
        return '操作未推进：审批过期、版本已变更、任务已终止或无权限。请重新查询详情。'
    response.raise_for_status()
    result = response.json()
    return f"{verb}已由 Controller 记录。Run: {rid}；版本: {result['revision']}。" + (f"\n导出 {rid}" if verb == '批准' else '')


class Notifications:
    """Persistent at-most-once notification claims; ambiguous sends are not replayed."""
    def __init__(self, path: Path):
        self.path = path
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS notifications (approval TEXT PRIMARY KEY, state TEXT, message_id TEXT)')
        path.chmod(0o600)

    def send_pending(self, config: dict[str, Any], client: httpx.Client) -> None:
        if not config.get('tenant'):
            return
        headers = {'Authorization': 'Bearer ' + config['bridge_token'], 'X-Feishu-App': config['app_id'],
                   'X-Feishu-Tenant': config['tenant'], 'X-Feishu-User': config['open_id']}
        response = client.get(config['product_url'] + '/api/engineering/notifications', headers=headers)
        response.raise_for_status()
        for row in response.json()['items']:
            d, rid = row['delivery'], row['run_id']
            with sqlite3.connect(self.path) as db:
                claimed = db.execute('INSERT OR IGNORE INTO notifications VALUES(?,?,NULL)', (d['approval_id'], 'claimed')).rowcount
            if not claimed:
                continue
            auth = client.post('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal', json={'app_id': config['app_id'], 'app_secret': config['app_secret']})
            auth.raise_for_status()
            response = client.post('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',
                headers={'Authorization': 'Bearer ' + auth.json()['tenant_access_token']},
                json={'receive_id': config['open_id'], 'msg_type': 'text',
                      'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, d['approval_id'])),
                      'content': json.dumps({'text': '当前候选待本人审批。\n' + details(rid, d)}, ensure_ascii=False)})
            response.raise_for_status()
            result = response.json()
            if result.get('code') != 0:
                raise RuntimeError('notification_platform_rejected')
            with sqlite3.connect(self.path) as db:
                db.execute('UPDATE notifications SET state=?,message_id=? WHERE approval=?', ('platform_accepted', result['data']['message_id'], d['approval_id']))
