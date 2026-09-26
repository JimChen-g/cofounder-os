"""Single-founder authentication at the deployed ASGI boundary."""
from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings


def same(a: str | None, b: str | None) -> bool:
    return bool(a and b and secrets.compare_digest(a.encode(), b.encode()))


async def authenticate(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    settings = get_settings()
    path = request.url.path
    if path.startswith('/v1/'):
        if not same(request.headers.get('authorization'), 'Bearer ' + (settings.gateway_api_key or '')) or not settings.gateway_api_key:
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
    if not path.startswith('/api/'):
        return await call_next(request)
    token = request.headers.get('authorization')
    is_founder = bool(settings.product_api_token and same(token, 'Bearer ' + settings.product_api_token))
    is_bridge = bool(settings.product_api_bridge_token and same(token, 'Bearer ' + settings.product_api_bridge_token))
    if not (is_founder or is_bridge):
        return JSONResponse({'error': 'unauthorized'}, status_code=401)
    if is_bridge:
        tenant = settings.feishu_tenant_key
        if tenant is None and settings.feishu_tenant_file:
            import json
            from pathlib import Path
            try:
                tenant = json.loads(Path(settings.feishu_tenant_file).read_text()).get('tenant')
            except (OSError, ValueError):
                tenant = None
        # The service token alone has no authority to create/approve/query Runs.
        if not (same(request.headers.get('x-feishu-app'), settings.feishu_app_id)
                and same(request.headers.get('x-feishu-tenant'), tenant)
                and same(request.headers.get('x-feishu-user'), settings.feishu_open_id)):
            return JSONResponse({'error': 'identity_not_paired'}, status_code=403)
        if path not in ('/api/bridge/receipt', '/api/health'):
            return JSONResponse({'error': 'bridge_business_actions_not_enabled'}, status_code=403)
    request.state.principal = settings.product_founder_id
    run = None
    if path.startswith('/api/runs/'):
        from app.api.product import _service
        try:
            run_id = UUID(path.split('/')[3])
            run = _service(request).get_run(run_id, event_limit=0).run
        except (ValueError, LookupError):
            return JSONResponse({'error': 'not_found'}, status_code=404)
        except Exception:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        if run.owner != settings.product_founder_id:
            return JSONResponse({'error': 'not_found'}, status_code=404)
    if request.method == 'POST' and path.startswith('/api/runs'):
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({'error': 'invalid_request'}, status_code=422)
        if any(body.get(field) not in (None, settings.product_founder_id) for field in ('owner', 'decided_by')):
            return JSONResponse({'error': 'principal_mismatch'}, status_code=403)
    if request.method != 'POST' or path == '/api/bridge/receipt':
        return await call_next(request)
    from app.policy.request_policy import InvocationBudget, active_budget
    from app.request_constraints import RequestPolicy
    from app.policy.budget_store import BudgetStore
    from pathlib import Path
    from uuid import uuid4
    from pydantic import ValidationError
    policy = RequestPolicy()
    if path == '/api/runs' and request.method == 'POST':
        try:
            policy = RequestPolicy.model_validate(body.get('constraints', {}))
        except ValidationError:
            return JSONResponse({'error': 'invalid_constraints'}, status_code=422)
    store = BudgetStore(Path(settings.product_data_dir) / 'budgets.sqlite3')
    key = run.metadata.get('request_budget_id') if run is not None else None
    if isinstance(key, str):
        try:
            policy = store.policy(key)
        except Exception:
            return JSONResponse({'error': 'budget_record_missing'}, status_code=409)
    else:
        key = str(uuid4())
        store.create(key, policy)
    scope = active_budget.set(InvocationBudget(policy, store=store, key=key))
    try:
        return await call_next(request)
    finally:
        active_budget.reset(scope)
