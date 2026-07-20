"""Tests for environment-variable → Settings mapping."""

from __future__ import annotations

from app.config import Settings


class TestSettingsEnvMapping:
    """Confirm each env var populates the correct Settings field."""

    def test_gateway_api_key(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_API_KEY", "sk-gateway-123")
        s = Settings()
        assert s.gateway_api_key == "sk-gateway-123"

    def test_qwen_base_url(self, monkeypatch):
        monkeypatch.setenv("QWEN_BASE_URL", "http://localhost:1234/v1")
        s = Settings()
        assert s.qwen_base_url == "http://localhost:1234/v1"

    def test_qwen_api_key(self, monkeypatch):
        monkeypatch.setenv("QWEN_API_KEY", "sk-qwen-456")
        s = Settings()
        assert s.qwen_api_key == "sk-qwen-456"

    def test_qwen_model(self, monkeypatch):
        monkeypatch.setenv("QWEN_MODEL", "qwen2.5-72b")
        s = Settings()
        assert s.qwen_model == "qwen2.5-72b"

    def test_step_base_url(self, monkeypatch):
        monkeypatch.setenv("STEP_BASE_URL", "https://custom.stepfun.com/v1")
        s = Settings()
        assert s.step_base_url == "https://custom.stepfun.com/v1"

    def test_step_api_key(self, monkeypatch):
        monkeypatch.setenv("STEP_API_KEY", "sk-step-789")
        s = Settings()
        assert s.step_api_key == "sk-step-789"

    def test_step_model(self, monkeypatch):
        monkeypatch.setenv("STEP_MODEL", "step-3.7-flash")
        s = Settings()
        assert s.step_model == "step-3.7-flash"

    def test_gateway_host(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_HOST", "0.0.0.0")
        s = Settings()
        assert s.gateway_host == "0.0.0.0"

    def test_gateway_port(self, monkeypatch):
        monkeypatch.setenv("GATEWAY_PORT", "8080")
        s = Settings()
        assert s.gateway_port == 8080

    def test_request_timeout_seconds(self, monkeypatch):
        monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "600")
        s = Settings()
        assert s.request_timeout_seconds == 600.0

    def test_audit_dir(self, monkeypatch):
        monkeypatch.setenv("AUDIT_DIR", "/var/log/gateway/audit")
        s = Settings()
        assert s.audit_dir == "/var/log/gateway/audit"


class TestSettingsDefaults:
    """Confirm defaults match the Day 2 acceptance criteria."""

    def test_qwen_base_url_default(self):
        s = Settings()
        assert s.qwen_base_url == "http://127.0.0.1:8000/v1"

    def test_step_base_url_default(self):
        s = Settings()
        assert s.step_base_url == "https://api.stepfun.com/step_plan/v1"

    def test_step_model_default(self):
        s = Settings()
        assert s.step_model == "step-3.7-flash"

    def test_gateway_host_default(self):
        s = Settings()
        assert s.gateway_host == "127.0.0.1"

    def test_gateway_port_default(self):
        s = Settings()
        assert s.gateway_port == 9000

    def test_request_timeout_default(self):
        s = Settings()
        assert s.request_timeout_seconds == 300.0

    def test_audit_dir_default(self):
        s = Settings()
        assert s.audit_dir == "data/audit"
