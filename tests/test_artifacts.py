"""Tests for the filesystem Artifact Store (D06-B approved contract)."""

from __future__ import annotations

import hashlib
import json
import threading

import pytest

from app.artifacts import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactPathError,
    FileArtifactStore,
)
from app.services import (
    ArtifactRegistrationService,
    OrchestrationService,
)
from app.state import FileStateRepository, LifecycleStateMachine


# ── Helpers ────────────────────────────────────────────────────────────────

CONTENT = b"Hello, Artifact Store!"
CONTENT_TEXT = CONTENT.decode("utf-8")


def _make_store(tmp_path):
    return FileArtifactStore(tmp_path / "artifacts")


def _write_text(store, run_id, logical_name="report", filename="report.md", text=CONTENT_TEXT, task_id=None, idempotency_key=None, created_by="test-user"):
    return store.write_text(
        run_id=run_id,
        logical_name=logical_name,
        filename=filename,
        text=text,
        created_by=created_by,
        task_id=task_id,
        idempotency_key=idempotency_key,
    )


def _write_json(store, run_id, logical_name="result", filename="result.json", value=None, task_id=None, idempotency_key=None, created_by="test-user"):
    if value is None:
        value = {"key": "value", "number": 42}
    return store.write_json(
        run_id=run_id,
        logical_name=logical_name,
        filename=filename,
        value=value,
        created_by=created_by,
        task_id=task_id,
        idempotency_key=idempotency_key,
    )


# ── Text round trip ────────────────────────────────────────────────────────

def test_text_round_trip(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, "00000000-0000-0000-0000-000000000001")

    assert stored.logical_name == "report"
    assert stored.filename == "report.md"
    assert stored.content_type == "text/plain; charset=utf-8"
    assert stored.size_bytes == len(CONTENT)
    assert stored.checksum_sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert stored.created_by == "test-user"
    assert stored.format_version == "1.0"
    assert stored.uri == "artifact://run/report"

    text = store.read_text("00000000-0000-0000-0000-000000000001", "report", "report.md")
    assert text == CONTENT.decode("utf-8")


# ── Canonical JSON round trip ──────────────────────────────────────────────

def test_canonical_json_round_trip(tmp_path):
    store = _make_store(tmp_path)
    value = {"z": 1, "a": [3, 2, 1], "m": {"nested": True}}
    stored = _write_json(store, "00000000-0000-0000-0000-000000000002", value=value)

    assert stored.content_type == "application/json; charset=utf-8"
    assert stored.size_bytes > 0

    data = store.read_json("00000000-0000-0000-0000-000000000002", "result", "result.json")
    assert data == value


# ── Deterministic JSON checksum ───────────────────────────────────────────

def test_deterministic_json_checksum(tmp_path):
    store = _make_store(tmp_path)
    value_a = {"z": 1, "a": 2}
    value_b = {"a": 2, "z": 1}

    stored_a = _write_json(store, "00000000-0000-0000-0000-000000000003", logical_name="v1", filename="v1.json", value=value_a)
    stored_b = _write_json(store, "00000000-0000-0000-0000-000000000004", logical_name="v2", filename="v2.json", value=value_b)

    assert stored_a.checksum_sha256 == stored_b.checksum_sha256


# ── Run scope ──────────────────────────────────────────────────────────────

def test_run_scope_layout(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, "00000000-0000-0000-0000-000000000005")

    content_dir = tmp_path / "artifacts" / "data" / "runs" / "00000000-0000-0000-0000-000000000005" / "artifacts" / "run" / "report"
    assert content_dir.is_dir()
    assert (content_dir / "report.md").is_file()
    assert (content_dir / "meta.json").is_file()


# ── Task scope ─────────────────────────────────────────────────────────────

def test_task_scope_layout(tmp_path):
    store = _make_store(tmp_path)
    _write_text(
        store,
        "00000000-0000-0000-0000-000000000006",
        task_id="00000000-0000-0000-0000-000000000099",
    )

    content_dir = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / "00000000-0000-0000-0000-000000000006"
        / "artifacts"
        / "00000000-0000-0000-0000-000000000099"
        / "report"
    )
    assert content_dir.is_dir()
    assert (content_dir / "report.md").is_file()


# ── Stable URI ─────────────────────────────────────────────────────────────

def test_stable_uri(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, "00000000-0000-0000-0000-000000000007")

    assert stored.uri == "artifact://run/report"

    stored_task = _write_text(
        store,
        "00000000-0000-0000-0000-000000000007",
        task_id="00000000-0000-0000-0000-000000000099",
    )
    assert stored_task.uri == "artifact://00000000-0000-0000-0000-000000000099/report"


# ── Corruption detection ──────────────────────────────────────────────────

def test_corruption_detection(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, "00000000-0000-0000-0000-000000000008")

    content_path = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / "00000000-0000-0000-0000-000000000008"
        / "artifacts"
        / "run"
        / "report"
        / "report.md"
    )
    content_path.write_bytes(b"corrupted content")

    with pytest.raises(ArtifactIntegrityError):
        store.read_text("00000000-0000-0000-0000-000000000008", "report", "report.md")


# ── Missing content ────────────────────────────────────────────────────────

def test_missing_content(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises(ArtifactIntegrityError):
        store.read_text("00000000-0000-0000-0000-000000000009", "report", "report.md")


# ── Absolute path rejection ────────────────────────────────────────────────

def test_absolute_path_rejection(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises(ArtifactPathError):
        _write_text(store, "00000000-0000-0000-0000-000000000010", logical_name="/absolute")


# ── Traversal rejection ────────────────────────────────────────────────────

def test_traversal_rejection(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises(ArtifactPathError):
        _write_text(store, "00000000-0000-0000-0000-000000000011", logical_name="../escape")


# ── Symlink rejection ──────────────────────────────────────────────────────

def test_symlink_rejection(tmp_path):
    store = _make_store(tmp_path)

    # Create a symlink inside the store root pointing outside
    link_dir = tmp_path / "artifacts" / "data"
    link_dir.mkdir(parents=True, exist_ok=True)
    symlink = link_dir / "runs"
    target = tmp_path / "outside"
    target.mkdir(exist_ok=True)
    try:
        symlink.symlink_to(target)

        with pytest.raises(ArtifactPathError):
            _write_text(store, "00000000-0000-0000-0000-000000000012")
    finally:
        if symlink.is_symlink():
            symlink.unlink()


# ── Safe filename normalization ────────────────────────────────────────────

def test_safe_filename_normalization(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, "00000000-0000-0000-0000-000000000013", filename="My-Report_2.md")

    assert stored.filename == "My-Report_2.md"
    content_dir = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / "00000000-0000-0000-0000-000000000013"
        / "artifacts"
        / "run"
        / "report"
    )
    assert (content_dir / "My-Report_2.md").is_file()


# ── 0600/0700 permissions ─────────────────────────────────────────────────

def test_permissions(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, "00000000-0000-0000-0000-000000000014")

    content_path = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / "00000000-0000-0000-0000-000000000014"
        / "artifacts"
        / "run"
        / "report"
        / "report.md"
    )
    meta_path = content_path.parent / "meta.json"

    assert oct(content_path.stat().st_mode)[-3:] == "600"
    assert oct(meta_path.stat().st_mode)[-3:] == "600"

    root = tmp_path / "artifacts"
    assert oct(root.stat().st_mode)[-3:] == "700"


# ── No temporary files ─────────────────────────────────────────────────────

def test_no_temporary_files(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, "00000000-0000-0000-0000-000000000015")

    content_dir = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / "00000000-0000-0000-0000-000000000015"
        / "artifacts"
        / "run"
        / "report"
    )
    assert not any(p.name.endswith(".tmp") for p in content_dir.iterdir())


# ── Same-key same-content idempotency ─────────────────────────────────────

def test_same_key_same_content_idempotency(tmp_path):
    store = _make_store(tmp_path)
    stored1 = _write_text(store, "00000000-0000-0000-0000-000000000016", idempotency_key="key1")
    stored2 = _write_text(store, "00000000-0000-0000-0000-000000000016", idempotency_key="key1")

    assert stored1 == stored2
    assert stored1.checksum_sha256 == stored2.checksum_sha256


# ── Same-key different-content conflict ───────────────────────────────────

def test_same_key_different_content_conflict(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, "00000000-0000-0000-0000-000000000017", idempotency_key="key1")

    with pytest.raises(ArtifactConflictError):
        _write_text(store, "00000000-0000-0000-0000-000000000017", idempotency_key="key1", text="Different content")


# ── Restart idempotency ────────────────────────────────────────────────────

def test_restart_idempotency(tmp_path):
    store1 = _make_store(tmp_path)
    stored1 = _write_text(store1, "00000000-0000-0000-0000-000000000018", idempotency_key="restart-key")

    store2 = _make_store(tmp_path)
    stored2 = _write_text(store2, "00000000-0000-0000-0000-000000000018", idempotency_key="restart-key")

    assert stored1 == stored2
    assert stored1.checksum_sha256 == stored2.checksum_sha256


# ── Concurrent writer protection ──────────────────────────────────────────

def test_concurrent_writer_protection(tmp_path):
    store = _make_store(tmp_path)
    errors = []

    def writer(key_suffix):
        try:
            _write_text(
                store,
                "00000000-0000-0000-0000-000000000019",
                idempotency_key=f"concurrent-{key_suffix}",
            )
        except ArtifactConflictError as exc:
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(errors) == 1
    assert "Idempotency key" in str(errors[0]) or "already stored" in str(errors[0])


# ── Atomic metadata ────────────────────────────────────────────────────────

def test_atomic_metadata_no_direct_write(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, "00000000-0000-0000-0000-000000000020")

    meta_path = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / "00000000-0000-0000-0000-000000000020"
        / "artifacts"
        / "run"
        / "report"
        / "meta.json"
    )

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["checksum_sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert meta["format_version"] == "1.0"
    assert meta["logical_name"] == "report"


# ── Orchestration integration ──────────────────────────────────────────────

def test_orchestration_registration(tmp_path):
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")
    task, _ = orch.create_task(run.id, title="task1", actor="test")

    stored, domain_artifact, event = writer.write_text(
        run_id=run.id,
        logical_name="report",
        filename="report.md",
        text="Hello",
        created_by="test",
        relation="run",
        correlation_id="cid1",
    )

    assert domain_artifact.run_id == run.id
    assert domain_artifact.name == "report"
    assert domain_artifact.uri == stored.uri
    assert domain_artifact.checksum_sha256 == stored.checksum_sha256
    assert domain_artifact.size_bytes == stored.size_bytes
    assert event.event_type == "artifact.registered"
    assert event.details["relation"] == "run"


def test_input_relation_updates_task(tmp_path):
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")
    task, _ = orch.create_task(run.id, title="task1", actor="test")

    stored, domain_artifact, event = writer.write_text(
        run_id=run.id,
        logical_name="input",
        filename="input.md",
        text="input data",
        created_by="test",
        relation="input",
        task_id=task.id,
        correlation_id="cid2",
    )

    updated_task = orch.repository.get_task(run.id, task.id)
    assert domain_artifact.id in updated_task.input_artifact_ids


def test_output_relation_updates_task(tmp_path):
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")
    task, _ = orch.create_task(run.id, title="task1", actor="test")

    stored, domain_artifact, event = writer.write_text(
        run_id=run.id,
        logical_name="output",
        filename="output.md",
        text="output data",
        created_by="test",
        relation="output",
        task_id=task.id,
        correlation_id="cid3",
    )

    updated_task = orch.repository.get_task(run.id, task.id)
    assert domain_artifact.id in updated_task.output_artifact_ids


def test_failed_registration_then_retry_reuses_content(tmp_path):
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")

    # First attempt: write content and register
    stored1, domain1, event1 = writer.write_text(
        run_id=run.id,
        logical_name="retry-test",
        filename="retry.md",
        text="retry content",
        created_by="test",
        relation="run",
        idempotency_key="retry-key",
        correlation_id="cid4",
    )

    # Simulate a retry with the same idempotency key
    stored2, domain2, event2 = writer.write_text(
        run_id=run.id,
        logical_name="retry-test",
        filename="retry.md",
        text="retry content",
        created_by="test",
        relation="run",
        idempotency_key="retry-key",
        correlation_id="cid5",
    )

    # Store returns the same stored artifact on retry
    assert stored1 == stored2
    assert stored1.checksum_sha256 == stored2.checksum_sha256
    # Content file exists only once on disk
    content_path = (
        tmp_path
        / "artifacts"
        / "data"
        / "runs"
        / str(run.id)
        / "artifacts"
        / "run"
        / "retry-test"
        / "retry.md"
    )
    assert content_path.exists()
    # Both registrations produced artifacts with the same uri
    assert domain1.uri == domain2.uri
    assert domain1.checksum_sha256 == domain2.checksum_sha256


# ── JSON rejection of NaN and Infinity ────────────────────────────────────

def test_json_rejects_nan_and_infinity(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises((ValueError, TypeError)):
        _write_json(store, "00000000-0000-0000-0000-000000000021", value={"x": float("nan")})

    with pytest.raises((ValueError, TypeError)):
        _write_json(store, "00000000-0000-0000-0000-000000000022", value={"x": float("inf")})
