"""D12 Founder Mission Control route and API-boundary tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.ui.routes import STATIC_ROOT


def test_ui_shell_and_assets_are_served_by_existing_app() -> None:
    with TestClient(app) as client:
        response = client.get("/ui")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "Founder Mission Control" in response.text
        assert 'id="mission-form"' in response.text
        assert 'id="approval-list"' in response.text
        assert 'id="artifact-viewer"' in response.text
        assert 'id="audit-list"' in response.text

        stylesheet = client.get("/ui/assets/app.css")
        script = client.get("/ui/assets/app.js")
        assert stylesheet.status_code == 200
        assert stylesheet.headers["content-type"].startswith("text/css")
        assert script.status_code == 200
        assert "use strict" in script.text


def test_ui_shell_has_restrictive_browser_security_headers() -> None:
    with TestClient(app) as client:
        response = client.get("/ui/")

    policy = response.headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_root_advertises_ui_without_changing_existing_contract(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "cofounder-os-gateway",
        "version": "0.1.0",
        "docs": "/docs",
        "mission_control": "/ui",
    }


def test_ui_uses_only_the_accepted_product_api_boundary() -> None:
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    for path in (
        "/api/health",
        "/api/runs",
        "/artifacts",
        "/events",
        "/approvals/",
        "/retry",
    ):
        assert path in script

    assert "/v1/chat/completions" not in script
    assert "127.0.0.1:8000" not in script
    assert "api.stepfun.com" not in script
    assert "mock" not in script.lower()


def test_ui_files_do_not_embed_external_assets_or_inline_code() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert "https://" not in html
    assert "http://" not in html
    assert "<style" not in html
    assert "<script>" not in html
    assert '<script src="/ui/assets/app.js?v=d12" defer></script>' in html


def test_ui_static_root_contains_only_reviewable_source_assets() -> None:
    assert {
        path.relative_to(STATIC_ROOT)
        for path in STATIC_ROOT.iterdir()
        if path.is_file()
    } == {
        Path("index.html"),
        Path("app.css"),
        Path("app.js"),
    }
