"""Tests for the audit logger."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


class TestAuditLogger:
    def test_log_writes_daily_jsonl(self, tmp_path):
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))

        logger.log({"event": "test", "value": 42})

        # File should be named YYYY-MM-DD.jsonl for current UTC date
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        assert log_file.exists()
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "test"
        assert record["value"] == 42
        assert "ts" in record
        assert "ts_iso" in record

    def test_log_request_event(self, tmp_path):
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))

        logger.log_request(
            request_id="req-abc",
            provider="cofounder-qwen",
            model="qwen-turbo",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=123.4,
            status="success",
            user_agent="test-client",
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        record = json.loads(lines[0])
        assert record["event"] == "chat_request"
        assert record["request_id"] == "req-abc"
        assert record["total_tokens"] == 15
        assert record["latency_ms"] == pytest.approx(123.4)

    def test_log_request_includes_canonical_fields(self, tmp_path):
        """log_request must accept and store all canonical Day 2 fields."""
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))

        logger.log_request(
            request_id="req-canonical",
            provider="cofounder-qwen",
            model="cofounder-qwen",
            prompt_tokens=10,
            completion_tokens=5,
            latency_ms=50.0,
            status="success",
            user_agent="test",
            timestamp="2026-07-16T10:30:00Z",
            endpoint="/v1/chat/completions",
            requested_virtual_model="cofounder-qwen",
            selected_provider="qwen",
            selected_upstream_model="qwen-turbo",
            routing_reason="forced_local",
            message_count=3,
            tool_count=1,
            prompt_sha256="a" * 64,
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        record = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert record["timestamp"] == "2026-07-16T10:30:00Z"
        assert record["endpoint"] == "/v1/chat/completions"
        assert record["requested_virtual_model"] == "cofounder-qwen"
        assert record["selected_provider"] == "qwen"
        assert record["selected_upstream_model"] == "qwen-turbo"
        assert record["routing_reason"] == "forced_local"
        assert record["message_count"] == 3
        assert record["tool_count"] == 1
        assert record["prompt_sha256"] == "a" * 64

    def test_log_request_canonical_fields_default_to_none(self, tmp_path):
        """When canonical fields are omitted they should be None in the record."""
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))

        logger.log_request(
            request_id="req-legacy-only",
            provider="cofounder-qwen",
            model="cofounder-qwen",
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        record = json.loads(
            (tmp_path / f"{today}.jsonl").read_text(encoding="utf-8").strip()
        )
        assert record["timestamp"] is not None
        assert record["endpoint"] is None
        assert record["requested_virtual_model"] is None
        assert record["selected_provider"] is None
        assert record["selected_upstream_model"] is None
        assert record["routing_reason"] is None
        assert record["message_count"] is None
        assert record["tool_count"] is None
        assert record["prompt_sha256"] is None

    def test_multiple_events_appended_to_same_day(self, tmp_path):
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))

        for i in range(5):
            logger.log({"event": "ping", "i": i})

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 5
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["i"] == i

    def test_get_audit_logger_singleton(self):
        from app.audit.logger import get_audit_logger

        logger1 = get_audit_logger()
        logger2 = get_audit_logger()
        assert logger1 is logger2

    def test_read_recent_returns_records(self, tmp_path):
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))
        for i in range(3):
            logger.log({"event": "ping", "i": i})

        records = logger.read_recent(max_records=10)
        assert len(records) == 3
        assert records[-1]["i"] == 2

    def test_compute_prompt_sha256_deterministic(self):
        """Same messages must produce the same SHA-256."""
        from app.audit.logger import _compute_prompt_sha256
        from app.models import ChatMessage, Role

        msgs = [
            ChatMessage(role=Role.USER, content="Hello"),
            ChatMessage(role=Role.ASSISTANT, content="World"),
        ]
        hash1 = _compute_prompt_sha256(msgs)
        hash2 = _compute_prompt_sha256(msgs)
        assert hash1 == hash2
        assert len(hash1) == 64
        assert all(c in "0123456789abcdef" for c in hash1)

    def test_compute_prompt_sha256_changes_with_content(self):
        """Different message content must produce different SHA-256."""
        from app.audit.logger import _compute_prompt_sha256
        from app.models import ChatMessage, Role

        msgs_a = [ChatMessage(role=Role.USER, content="Hello")]
        msgs_b = [ChatMessage(role=Role.USER, content="World")]
        assert _compute_prompt_sha256(msgs_a) != _compute_prompt_sha256(msgs_b)

    def test_compute_prompt_sha256_with_null_content(self):
        """SHA-256 handles None content correctly."""
        from app.audit.logger import _compute_prompt_sha256
        from app.models import ChatMessage, Role

        msgs = [ChatMessage(role=Role.ASSISTANT, content=None)]
        digest = _compute_prompt_sha256(msgs)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_compute_prompt_sha256_order_independent(self):
        """Key order in serialization shouldn't matter — same logical messages."""
        from app.audit.logger import _compute_prompt_sha256
        from app.models import ChatMessage, Role

        msgs1 = [
            ChatMessage(role=Role.SYSTEM, content="Be helpful"),
            ChatMessage(role=Role.USER, content="Hi"),
        ]
        msgs2 = list(reversed(msgs1))
        # Different order → different hash (order IS meaningful for prompts)
        assert _compute_prompt_sha256(msgs1) != _compute_prompt_sha256(msgs2)

    def test_audit_log_contains_no_prompt_content(self, tmp_path):
        """Audit records must never contain message content or secrets."""
        from app.audit.logger import AuditLogger

        logger = AuditLogger(log_dir=str(tmp_path))

        secret_content = "SECRET_API_KEY_12345"
        logger.log_request(
            request_id="req-secret",
            provider="cofounder-qwen",
            model="qwen-turbo",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=500.0,
            status="success",
            user_agent="test",
        )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = tmp_path / f"{today}.jsonl"
        raw = log_file.read_text(encoding="utf-8")
        assert secret_content not in raw
        assert "Bearer" not in raw
        assert "api_key" not in raw.lower()
        assert "Authorization" not in raw
