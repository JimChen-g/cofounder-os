"""D13 Founder Mission Control route and API-boundary tests."""

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
        assert 'id="evaluation-view"' in response.text
        assert 'id="evaluation-latest"' in response.text
        assert 'id="evaluation-runs"' in response.text
        assert 'id="evidence-files"' in response.text
        assert 'id="evidence-board"' in response.text
        assert 'id="load-poc-fixture"' in response.text
        assert 'id="routing-board"' in response.text
        assert 'id="simulate-route-fallback"' in response.text
        assert 'id="live-execution-board"' in response.text
        assert 'id="live-execution-grid"' in response.text
        assert 'id="live-execution-verdict"' in response.text
        assert 'id="conflict-section"' in response.text
        assert 'id="conflict-grid"' in response.text
        assert 'id="demo-strategy-grid"' in response.text
        assert 'id="demo-evaluation-disclosure"' in response.text

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
        "/api/evaluation/summary",
        "/api/insurance-poc/evidence",
        "/api/insurance-poc/fixture",
        "/api/insurance-poc/routing",
        "/api/insurance-poc/runs",
        "/api/insurance-poc/evaluation",
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
    assert '<script src="/ui/assets/app.js?v=d15-live-proof" defer></script>' in html
    assert '<link rel="stylesheet" href="/ui/assets/app.css?v=d15-live-proof">' in html


def test_insurance_poc_ui_labels_adaptive_routes_and_verified_live_calls() -> None:
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")

    assert "Build Evidence Package" in html
    assert "Simulate Engineering route outage" in html
    assert "Adaptive explainable router" in html
    assert "not a trained or learned Router" in html
    assert "Simulate Step unavailable" not in html
    assert "Files are normalized locally before any model route" in html
    assert "source.adapter_mode" in script
    assert "state.snapshot = insuranceMission" in script
    assert "hydrateInsuranceRunState" in script
    assert "decision.excluded_models" in script
    assert "decision.privacy_decision" in script
    assert "decision.validation_requirement" in script
    assert "Simulation changes availability only" in script
    assert "Verified live call" in script
    assert "candidate_scores" in script
    assert "execution_metadata" in script
    assert "Restore normal routing" in script
    assert "Route recalculated from submitted constraints" in script
    assert "function renderConflicts()" in script
    assert "conflict.source_evidence" in script
    assert 'const ACTIVE_RUN_KEY = "cofounder-os.active-run-id"' in script
    assert "window.localStorage.setItem(ACTIVE_RUN_KEY" in script
    assert "function tasksInStageOrder()" in script
    assert "function renderInsuranceDemoEvaluation()" in script
    assert "not statistical model quality" in script
    assert "function renderLiveExecutionBoard()" in script
    assert "Only persisted Gateway metadata can mark an Agent LIVE" in script
    assert 'element("span", null, "REQUEST ID")' not in script
    assert '["REQUEST ID", execution.request_id' in script
    assert '["TOKENS", execution.total_tokens' in script
    assert '["LATENCY", formatExecutionLatency' in script
    assert '["REPAIR", repairValue]' in script
    assert '["FALLBACK", fallbackValue]' in script


def test_ui_static_root_contains_only_reviewable_source_assets() -> None:
    assert {path.relative_to(STATIC_ROOT) for path in STATIC_ROOT.iterdir() if path.is_file()} == {
        Path("index.html"),
        Path("app.css"),
        Path("app.js"),
    }


def test_ui_guards_stale_run_responses_and_terminal_failure_copy() -> None:
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "requestEpoch: 0" in script
    assert "requestedRunId !== state.runId" in script
    assert "requestEpoch !== state.requestEpoch" in script
    terminal_check = script.index("result.terminal_failure")
    replay_check = script.index("result.replayed", terminal_check)
    assert terminal_check < replay_check


def test_narrow_layout_keeps_mission_controls_and_five_views() -> None:
    stylesheet = (STATIC_ROOT / "app.css").read_text(encoding="utf-8")

    assert "grid-template-columns: repeat(5, 1fr)" in stylesheet
    assert "#refresh-run," not in stylesheet
    assert ".topbar .button-secondary" not in stylesheet


def test_provider_distribution_uses_evaluated_run_denominator() -> None:
    script = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")

    assert "summary.run_count" in script
    assert "function renderEvaluationProviders(distribution, evaluatedRunCount)" in script
    assert "${count} / ${evaluatedRunCount} evaluated runs" in script
    assert "entries.reduce((sum, [, count])" not in script
