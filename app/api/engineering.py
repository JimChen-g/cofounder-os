"""Bounded engineering entry point with durable at-most-once dispatch."""
from __future__ import annotations

import asyncio
import fcntl
import os
import sqlite3
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.product import _service
from app.config import get_settings

router = APIRouter(prefix='/api/engineering', tags=['engineering'])


class CreateEngineeringRun(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_id: str = Field(min_length=1, max_length=200, pattern=r'^[a-zA-Z0-9_:/.-]+$')
    task: Literal['materials_completeness'] = 'materials_completeness'


class DispatchStore:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / 'engineering-dispatch.sqlite3'
        self.lock = (root / 'engineering-dispatch.lock').open('a')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (request_id TEXT PRIMARY KEY, owner TEXT NOT NULL, task TEXT NOT NULL, run_id TEXT, state TEXT NOT NULL, error TEXT)')
        self.path.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        return db

    def interrupt(self, product: Any, request_id: str | None = None) -> None:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs WHERE state IN ('creating','queued','running') AND (? IS NULL OR request_id=?)", (request_id, request_id)).fetchall()
            for row in rows:
                run_id = row['run_id']
                if not run_id:
                    matches = [run for run in product.orchestration.repository.list_runs() if run.owner == row['owner'] and run.metadata.get('engineering_request_id') == row['request_id']]
                    if len(matches) > 1:
                        raise RuntimeError('duplicate_controller_request_id')
                    if matches:
                        run_id = str(matches[0].id)
                        db.execute('UPDATE jobs SET run_id=? WHERE request_id=?', (run_id, row['request_id']))
                if run_id:
                    snapshot = product.get_run(UUID(run_id), event_limit=0)
                    if snapshot.run.status == 'queued':
                        product.orchestration.cancel_run(run_id, actor='engineering-dispatch', reason='interrupted_process; automatic replay prohibited')
                    elif snapshot.run.status not in ('completed', 'failed', 'cancelled'):
                        product.orchestration.fail_run(run_id, actor='engineering-dispatch', reason='interrupted_process; automatic replay prohibited')
                db.execute("UPDATE jobs SET state='interrupted', error='interrupted_process' WHERE request_id=?", (row['request_id'],))


def _runtime(request: Request) -> Any:
    existing = getattr(request.app.state, 'engineering_runtime', None)
    if existing is not None:
        return existing
    from app.engineering.service import EngineeringService
    product = _service(request)
    root = Path(get_settings().product_data_dir)
    store = DispatchStore(root)
    store.interrupt(product)
    service = EngineeringService(product, Path(os.environ.get('ENGINEERING_REPO', str(Path(__file__).resolve().parents[2]))), Path(os.environ.get('ENGINEERING_WORKSPACE_ROOT', str(root / 'engineering-workspaces'))))
    existing = (store, service, product, set(), asyncio.Lock())
    request.app.state.engineering_runtime = existing
    return existing


async def _execute(store: DispatchStore, service: Any, product: Any, request_id: str, run_id: str) -> None:
    with store.connect() as db:
        changed = db.execute("UPDATE jobs SET state='running' WHERE request_id=? AND state='queued'", (request_id,)).rowcount
    if not changed:
        return
    try:
        await service.execute(run_id)
        state, error = 'finished', None
    except BaseException as exc:
        state, error = 'failed', type(exc).__name__
        try:
            snapshot = product.get_run(UUID(run_id), event_limit=0)
            stop = product.orchestration.cancel_run if snapshot.run.status == 'queued' else product.orchestration.fail_run
            stop(run_id, actor='engineering-dispatch', reason='execution_failed:' + error)
        except Exception:
            pass  # Existing terminal controller failure remains authoritative.
    with store.connect() as db:
        db.execute('UPDATE jobs SET state=?,error=? WHERE request_id=?', (state, error, request_id))


@router.post('/runs')
async def create(request: Request, body: CreateEngineeringRun) -> Any:
    store, service, product, tasks, execution_lock = _runtime(request)
    owner = request.state.principal
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM jobs WHERE request_id=?', (body.request_id,)).fetchone()
        if row:
            if row['owner'] != owner or row['task'] != body.task:
                return JSONResponse({'error': 'idempotency_conflict'}, status_code=409)
            if not row['run_id']:
                return JSONResponse({'error': row['state'], 'detail': 'Creation interrupted; no automatic replay.'}, status_code=409)
            return {'run_id': row['run_id'], 'dispatch_status': row['state'], 'duplicate': True}
        db.execute("INSERT INTO jobs VALUES(?,?,?,NULL,'creating',NULL)", (body.request_id, owner, body.task))
    try:
        snapshot = service.create(owner=owner, request_id=body.request_id)
    except Exception as exc:
        store.interrupt(product, body.request_id)
        with store.connect() as db:
            db.execute("UPDATE jobs SET state='failed',error=? WHERE request_id=?", (type(exc).__name__, body.request_id))
        return JSONResponse({'error': 'creation_failed'}, status_code=409)
    run_id = str(snapshot.run.id)
    with store.connect() as db:
        db.execute("UPDATE jobs SET run_id=?,state='queued' WHERE request_id=?", (run_id, body.request_id))
    async def serialized() -> None:
        async with execution_lock:
            await _execute(store, service, product, body.request_id, run_id)
    task = asyncio.create_task(serialized())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return JSONResponse({'run_id': run_id, 'dispatch_status': 'queued', 'duplicate': False}, status_code=202)


@router.get('/runs/{run_id}')
async def status(request: Request, run_id: UUID) -> Any:
    store, service, product, tasks, execution_lock = _runtime(request)
    with store.connect() as db:
        row = db.execute('SELECT * FROM jobs WHERE run_id=? AND owner=?', (str(run_id), request.state.principal)).fetchone()
    if row is None:
        return JSONResponse({'error': 'not_found'}, status_code=404)
    snapshot = product.get_run(run_id)
    if snapshot.run.owner != request.state.principal:
        return JSONResponse({'error': 'not_found'}, status_code=404)
    return {'run_id': str(run_id), 'dispatch_status': row['state'], 'dispatch_error': row['error'], 'snapshot': snapshot.model_dump(mode='json')}
