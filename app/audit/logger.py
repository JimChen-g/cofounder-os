"""Audit logging for gateway requests."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings


def _compute_prompt_sha256(messages: list[Any]) -> str | None:
    """Compute a deterministic SHA-256 hex digest of request messages.

    Uses canonical JSON serialization with sorted keys and compact separators.
    Only includes message structure (role, content) — no secrets or raw content.
    """
    try:
        canonical = json.dumps(
            [m.model_dump(mode="json") for m in messages],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        return None


class AuditLogger:
    """Append-only JSONL audit logger — one file per UTC day."""

    def __init__(self, log_dir: str | None = None) -> None:
        settings = get_settings()
        self._dir = Path(log_dir or settings.audit_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _daily_path(self) -> Path:
        """Return the JSONL path for the current UTC date."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._dir / f"{today}.jsonl"

    def log(self, event: dict[str, Any]) -> None:
        """Append a single event as a JSON line to today's UTC file."""
        record = {
            "ts": time.time(),
            "ts_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            **event,
        }
        path = self._daily_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def log_request(
        self,
        *,
        request_id: str,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        status: str = "success",
        error: str | None = None,
        user_agent: str | None = None,
        # ── Canonical Day 2 fields ────────────────────────────────────────
        timestamp: str | None = None,
        endpoint: str | None = None,
        requested_virtual_model: str | None = None,
        selected_provider: str | None = None,
        selected_upstream_model: str | None = None,
        routing_reason: str | None = None,
        message_count: int | None = None,
        tool_count: int | None = None,
        prompt_sha256: str | None = None,
    ) -> None:
        """Log a chat request audit record (no prompt content, no secrets)."""
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.log(
            {
                "event": "chat_request",
                "request_id": request_id,
                "provider": provider,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "latency_ms": round(latency_ms, 2),
                "status": status,
                "error": error,
                "user_agent": user_agent,
                # ── Canonical Day 2 fields ────────────────────────────────────
                "timestamp": timestamp,
                "endpoint": endpoint,
                "requested_virtual_model": requested_virtual_model,
                "selected_provider": selected_provider,
                "selected_upstream_model": selected_upstream_model,
                "routing_reason": routing_reason,
                "message_count": message_count,
                "tool_count": tool_count,
                "prompt_sha256": prompt_sha256,
            }
        )

    def read_recent(self, max_records: int = 100) -> list[dict[str, Any]]:
        """Read recent records from today's audit file."""
        path = self._daily_path()
        if not path.exists():
            return []
        records = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records[-max_records:]


# Module-level default logger
_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Return the default audit logger."""
    global _logger
    if _logger is None:
        _logger = AuditLogger()
    return _logger
