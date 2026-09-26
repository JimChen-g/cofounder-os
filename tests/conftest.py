"""Shared pytest fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# Ensure test mode settings
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("STEP_API_KEY", "test-step-key")
os.environ.setdefault("GATEWAY_AUDIT_TOKEN", "test-audit-token")
os.environ.setdefault("GATEWAY_API_KEY", "test-client-key")
os.environ.setdefault("PRODUCT_API_TOKEN", "test-client-key")


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Return a FastAPI TestClient for the app."""
    from app.main import app

    with TestClient(app, headers={"Authorization": "Bearer test-client-key"}) as test_client:
        yield test_client
