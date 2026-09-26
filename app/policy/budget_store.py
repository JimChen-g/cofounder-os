"""Atomic durable reservations across retries, workers, and process restarts."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from app.models import Provider
from app.provider_errors import ProviderError
from app.request_constraints import RequestPolicy


class BudgetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS budgets (
                id TEXT PRIMARY KEY, policy TEXT NOT NULL, started REAL NOT NULL,
                attempts INTEGER NOT NULL, tokens INTEGER NOT NULL, cloud INTEGER NOT NULL)''')

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=2)
        db.execute('PRAGMA synchronous=FULL')
        return db

    def create(self, key: str, policy: RequestPolicy) -> None:
        with self.connect() as db:
            db.execute('INSERT INTO budgets VALUES(?,?,?,0,0,0)', (key, policy.model_dump_json(), time.time()))

    def policy(self, key: str) -> RequestPolicy:
        with self.connect() as db:
            row = db.execute('SELECT policy FROM budgets WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ProviderError('budget_record_missing')
        return RequestPolicy.model_validate_json(row[0])

    def reserve(self, key: str, provider: Provider, tokens: int, requested: RequestPolicy) -> float:
        from app.policy.request_policy import PolicyDenied
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT policy,started,attempts,tokens,cloud FROM budgets WHERE id=?', (key,)).fetchone()
            if row is None:
                raise PolicyDenied('budget_record_missing')
            policy = RequestPolicy.model_validate_json(row[0]).intersect(requested)
            remaining = policy.timeout_seconds - (time.time()-row[1])
            if not policy.allows(provider):
                raise PolicyDenied('provider_not_allowed')
            if (remaining <= 0 or row[2] >= policy.max_attempts
                    or row[3]+tokens > policy.max_total_tokens
                    or (provider == Provider.STEP and row[4] >= policy.cloud_call_budget)):
                raise PolicyDenied('request_budget_exhausted')
            db.execute('UPDATE budgets SET attempts=attempts+1,tokens=tokens+?,cloud=cloud+? WHERE id=?',
                       (tokens, int(provider == Provider.STEP), key))
        return float(remaining)
