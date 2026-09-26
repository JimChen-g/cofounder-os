"""Durable single-instance inbox; independent message facts, no Run state."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Inbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS inbox (
                message_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL, stored_at TEXT NOT NULL,
                service_received_at TEXT, agent_consumed_at TEXT, replied_at TEXT,
                platform_accepted_at TEXT, reply_message_id TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
                read_state TEXT NOT NULL DEFAULT 'unknown')''')
            # An interrupted consumer is never silently re-executed.
            db.execute("UPDATE inbox SET state='uncertain', error='interrupted_after_claim' WHERE state='processing'")
        path.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=0.5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        return db

    def accept(self, payload: dict[str, Any]) -> bool:
        key = '/'.join(payload[k] for k in ('tenant', 'app', 'message_id'))
        canonical = json.dumps({k: payload[k] for k in ('tenant', 'app', 'message_id', 'user', 'text')}, sort_keys=True)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with self.connect() as db:
            previous = db.execute('SELECT payload_hash FROM inbox WHERE message_key=?', (key,)).fetchone()
            if previous:
                if previous['payload_hash'] != digest:
                    raise ValueError('message_id_payload_conflict')
                return False
            db.execute('INSERT INTO inbox(message_key,payload_hash,payload,state,stored_at) VALUES(?,?,?,?,?)',
                       (key, digest, json.dumps(payload), 'stored', now()))
        return True

    def claim(self) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM inbox WHERE state='stored' ORDER BY stored_at LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE inbox SET state='processing', attempts=attempts+1 WHERE message_key=?", (row['message_key'],))
            return dict(row)

    def record(self, key: str, **facts: Any) -> None:
        allowed = {'state', 'service_received_at', 'agent_consumed_at', 'replied_at', 'platform_accepted_at', 'reply_message_id', 'error'}
        if not facts.keys() <= allowed:
            raise ValueError('unknown_fact')
        with self.connect() as db:
            db.execute('UPDATE inbox SET ' + ','.join(k+'=?' for k in facts) + ' WHERE message_key=?', (*facts.values(), key))

    def facts(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT message_key,state,stored_at,service_received_at,agent_consumed_at,replied_at,platform_accepted_at,attempts,error,read_state FROM inbox')]
