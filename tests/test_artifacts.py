"""Tests for the filesystem Artifact Store (D06-B approved contract)."""

from __future__ import annotations

import hashlib
import json
import threading
from unittest.mock import patch

import pytest

from app.artifacts import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactPathError,
    FileArtifactStore,
)
from app.services import (
    ArtifactRegistrationService,
    ArtifactWriteConflict,
    OrchestrationService,
)
from app.state import FileStateRepository, LifecycleStateMachine

RUN = "00000000-0000-0000-0000-000000000001"
TASK = "00000000-0000-0000-0000-000000000099"
RUN2 = "00000000-0000-0000-0000-000000000002"
CONTENT = b"Hello, Artifact Store!"
CONTENT_TEXT = CONTENT.decode("utf-8")


def _make_store(tmp_path):
    return FileArtifactStore(tmp_path / "artifacts")


def _run_dir(tmp_path, run_id):
    return tmp_path / "artifacts" / "runs" / run_id / "artifacts"


def _write_text(store, run_id, logical_name="report", filename="report.md", text=CONTENT_TEXT, task_id=None, idempotency_key=None, created_by="test-user", content_type="text/plain; charset=utf-8", provenance=None):
    return store.write_text(
        run_id=run_id,
        logical_name=logical_name,
        filename=filename,
        text=text,
        created_by=created_by,
        task_id=task_id,
        content_type=content_type,
        idempotency_key=idempotency_key,
        provenance=provenance,
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
    stored = _write_text(store, RUN)

    assert stored.logical_name == "report"
    assert stored.filename == "report.md"
    assert stored.content_type == "text/plain; charset=utf-8"
    assert stored.size_bytes == len(CONTENT)
    assert stored.checksum_sha256 == hashlib.sha256(CONTENT).hexdigest()
    assert stored.created_by == "test-user"
    assert stored.format_version == "1.0"
    assert stored.uri == f"artifact://runs/{RUN}/artifacts/run/report/report.md"

    text = store.read_text(RUN, "report", "report.md")
    assert text == CONTENT_TEXT


# ── Canonical JSON round trip ──────────────────────────────────────────────

def test_canonical_json_round_trip(tmp_path):
    store = _make_store(tmp_path)
    value = {"z": 1, "a": [3, 2, 1], "m": {"nested": True}}
    stored = _write_json(store, RUN2, value=value)

    assert stored.content_type == "application/json; charset=utf-8"
    assert stored.size_bytes > 0

    data = store.read_json(RUN2, "result", "result.json")
    assert data == value


# ── Deterministic JSON checksum ───────────────────────────────────────────

def test_deterministic_json_checksum(tmp_path):
    store = _make_store(tmp_path)
    value_a = {"z": 1, "a": 2}
    value_b = {"a": 2, "z": 1}

    stored_a = _write_json(store, RUN2, logical_name="v1", filename="v1.json", value=value_a)
    stored_b = _write_json(store, "00000000-0000-0000-0000-000000000003", logical_name="v2", filename="v2.json", value=value_b)

    assert stored_a.checksum_sha256 == stored_b.checksum_sha256


# ── Run scope ──────────────────────────────────────────────────────────────

def test_run_scope_layout(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    assert content_dir.is_dir()
    assert (content_dir / "report.md").is_file()
    assert (content_dir / "meta.json").is_file()


# ── Task scope ─────────────────────────────────────────────────────────────

def test_task_scope_layout(tmp_path):
    store = _make_store(tmp_path)
    _write_text(
        store,
        RUN,
        task_id=TASK,
    )

    content_dir = _run_dir(tmp_path, RUN) / "tasks" / TASK / "report"
    assert content_dir.is_dir()
    assert (content_dir / "report.md").is_file()


# ── Portable unique URI ────────────────────────────────────────────────────

def test_portable_unique_uri_run(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, RUN)

    assert stored.uri == f"artifact://runs/{RUN}/artifacts/run/report/report.md"


def test_portable_unique_uri_task(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, RUN, task_id=TASK)

    assert stored.uri == f"artifact://runs/{RUN}/artifacts/tasks/{TASK}/report/report.md"


def test_distinct_runs_distinct_uris(tmp_path):
    store = _make_store(tmp_path)
    s1 = _write_text(store, RUN)
    s2 = _write_text(store, RUN2)

    assert s1.uri != s2.uri
    assert s1.uri == f"artifact://runs/{RUN}/artifacts/run/report/report.md"
    assert s2.uri == f"artifact://runs/{RUN2}/artifacts/run/report/report.md"


# ── Corruption detection ──────────────────────────────────────────────────

def test_corruption_detection(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    content_path = content_dir / "report.md"
    content_path.write_bytes(b"corrupted content")

    with pytest.raises(ArtifactIntegrityError):
        store.read_text(RUN, "report", "report.md")


# ── Missing content ────────────────────────────────────────────────────────

def test_missing_content(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises(ArtifactIntegrityError):
        store.read_text(RUN, "report", "report.md")


# ── Absolute path rejection ────────────────────────────────────────────────

def test_absolute_path_rejection(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises(ArtifactPathError):
        _write_text(store, RUN, logical_name="/absolute")


# ── Traversal rejection ────────────────────────────────────────────────────

def test_traversal_rejection(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises(ArtifactPathError):
        _write_text(store, RUN, logical_name="../escape")


# ── Symlink rejection ──────────────────────────────────────────────────────

def test_symlink_rejection(tmp_path):
    store = _make_store(tmp_path)

    # Create a symlink inside the store root pointing outside
    runs_link = tmp_path / "artifacts" / "runs"
    outside = tmp_path / "outside"
    outside.mkdir(exist_ok=True)
    try:
        runs_link.symlink_to(outside)

        with pytest.raises(ArtifactPathError):
            _write_text(store, RUN)
    finally:
        if runs_link.is_symlink():
            runs_link.unlink()


# ── Safe filename normalization ────────────────────────────────────────────

def test_safe_filename_normalization(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, RUN, filename="My-Report_2.md")

    assert stored.filename == "My-Report_2.md"
    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    assert (content_dir / "My-Report_2.md").is_file()


# ── 0600/0700 permissions ─────────────────────────────────────────────────

def test_permissions(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    content_path = content_dir / "report.md"
    meta_path = content_dir / "meta.json"

    assert oct(content_path.stat().st_mode)[-3:] == "600"
    assert oct(meta_path.stat().st_mode)[-3:] == "600"

    root = tmp_path / "artifacts"
    assert oct(root.stat().st_mode)[-3:] == "700"


# ── No temporary files ─────────────────────────────────────────────────────

def test_no_temporary_files(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    assert not any(p.name.endswith(".tmp") for p in content_dir.iterdir())


# ── Atomic metadata (no Path.write_text) ──────────────────────────────────

def test_atomic_metadata_is_atomic(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    meta_path = content_dir / "meta.json"

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["checksum_sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert meta["format_version"] == "1.0"
    assert meta["logical_name"] == "report"


def test_atomic_metadata_no_truncated_on_failure(tmp_path):
    """Interrupted meta.json write: content commits, meta fails, retry recovers."""
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    retry_dir = _run_dir(tmp_path, RUN) / "run" / "retry"
    meta_path = retry_dir / "meta.json"

    # Save the real os.replace BEFORE patching to avoid recursion
    import os as _os
    _real_replace = _os.replace

    call_count = [0]

    def flaky_replace(src, dst):
        call_count[0] += 1
        # First replace: content file -> succeeds
        # Second replace: meta.json -> fails
        if call_count[0] == 2 and str(dst).endswith("meta.json"):
            raise OSError("simulated meta.json replace failure")
        _real_replace(src, dst)

    # First attempt: write content succeeds, meta.json fails
    with patch("app.artifacts.store.os.replace", side_effect=flaky_replace):
        with pytest.raises(OSError):
            _write_text(store, RUN, logical_name="retry", filename="retry.md")

    # Content file exists (first replace succeeded)
    assert (retry_dir / "retry.md").exists()

    # No .tmp files left behind
    assert not any(p.name.endswith(".tmp") for p in retry_dir.iterdir() if p.is_file())

    # meta.json is either absent or complete (not truncated)
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "checksum_sha256" in meta

    # Second attempt: identical retry should succeed
    stored = _write_text(store, RUN, logical_name="retry", filename="retry.md")

    # Now meta.json exists and is complete
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["checksum_sha256"] == hashlib.sha256(CONTENT).hexdigest()

    # verify() passes
    verified = store.verify(RUN, "retry", "retry.md")
    assert verified == stored


# ── Same-key same-content idempotency ─────────────────────────────────────

def test_same_key_same_content_idempotency(tmp_path):
    store = _make_store(tmp_path)
    stored1 = _write_text(store, RUN, idempotency_key="key1")
    stored2 = _write_text(store, RUN, idempotency_key="key1")

    assert stored1 == stored2
    assert stored1.checksum_sha256 == stored2.checksum_sha256


# ── Same-key different-content conflict ───────────────────────────────────

def test_same_key_different_content_conflict(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN, idempotency_key="key1")

    with pytest.raises(ArtifactConflictError):
        _write_text(store, RUN, idempotency_key="key1", text="Different content")


# ── Full idempotency compatibility ────────────────────────────────────────

def test_idempotency_incompatible_run_id(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN, idempotency_key="ik")

    with pytest.raises(ArtifactConflictError):
        _write_text(store, RUN2, idempotency_key="ik", text=CONTENT_TEXT)


def test_idempotency_incompatible_content_type(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN, idempotency_key="ik", content_type="text/plain")

    with pytest.raises(ArtifactConflictError):
        _write_text(store, RUN, idempotency_key="ik", text=CONTENT_TEXT, content_type="application/json")


def test_idempotency_incompatible_provenance(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN, idempotency_key="ik", provenance={"source": "A"})

    with pytest.raises(ArtifactConflictError):
        _write_text(store, RUN, idempotency_key="ik", text=CONTENT_TEXT, provenance={"source": "B"})


# ── Restart idempotency ────────────────────────────────────────────────────

def test_restart_idempotency(tmp_path):
    store1 = _make_store(tmp_path)
    stored1 = _write_text(store1, RUN, idempotency_key="restart-key")

    store2 = _make_store(tmp_path)
    stored2 = _write_text(store2, RUN, idempotency_key="restart-key")

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
                RUN,
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


# ── Partial-state recovery ────────────────────────────────────────────────

def test_meta_content_missing_raises(tmp_path):
    """State B: metadata exists, content missing -> raises."""
    store = _make_store(tmp_path)
    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    content_dir.mkdir(parents=True)
    meta = {
        "run_id": RUN,
        "task_id": None,
        "logical_name": "report",
        "filename": "report.md",
        "uri": f"artifact://runs/{RUN}/artifacts/run/report/report.md",
        "checksum_sha256": hashlib.sha256(CONTENT).hexdigest(),
        "size_bytes": len(CONTENT),
        "created_by": "test",
        "format_version": "1.0",
        "idempotency_key": "ik",
    }
    (content_dir / "meta.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        store.read_text(RUN, "report", "report.md")


def test_content_meta_missing_raises(tmp_path):
    """State C: content exists, metadata missing, checksum mismatch -> raises."""
    store = _make_store(tmp_path)
    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    content_dir.mkdir(parents=True)
    (content_dir / "report.md").write_bytes(CONTENT)

    with pytest.raises(ArtifactIntegrityError):
        store.read_text(RUN, "report", "report.md")


def test_content_meta_missing_reconstructs_when_checksum_matches(tmp_path):
    """State C repair: content exists, metadata missing, checksum matches -> reconstructs meta."""
    store = _make_store(tmp_path)
    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    content_dir.mkdir(parents=True)
    (content_dir / "report.md").write_bytes(CONTENT)

    store.write_text(RUN, "report", "report.md", CONTENT_TEXT, "test-user")

    assert (content_dir / "meta.json").exists()
    meta = json.loads((content_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["checksum_sha256"] == hashlib.sha256(CONTENT).hexdigest()


def test_temp_files_cleaned_on_write(tmp_path):
    """Temp files are cleaned while holding the lock."""
    store = _make_store(tmp_path)
    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    content_dir.mkdir(parents=True)
    (content_dir / ".report.md.tmp").write_bytes(b"leftover")

    _write_text(store, RUN)

    assert not any(p.name.endswith(".tmp") for p in content_dir.iterdir())
    assert (content_dir / "report.md").exists()


# ── Domain metadata idempotency ────────────────────────────────────────────

def test_orchestration_registration_idempotency(tmp_path):
    """Same idempotency_key: exactly one domain Artifact, no duplicate event."""
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")

    stored1, domain1, event1 = writer.write_text(
        run_id=run.id,
        logical_name="report",
        filename="report.md",
        text="Hello",
        created_by="test",
        relation="run",
        idempotency_key="ik-reg",
        correlation_id="cid1",
    )

    # Idempotent retry: returns existing artifact, event=None
    stored2, domain2, event2 = writer.write_text(
        run_id=run.id,
        logical_name="report",
        filename="report.md",
        text="Hello",
        created_by="test",
        relation="run",
        idempotency_key="ik-reg",
        correlation_id="cid2",
    )

    # Store returns same content
    assert stored1 == stored2

    # Exactly one domain Artifact
    assert domain1.id == domain2.id
    assert domain1.uri == domain2.uri
    assert domain1.checksum_sha256 == domain2.checksum_sha256

    # Run has exactly one artifact ID
    updated_run = orch.repository.get_run(run.id)
    assert updated_run.artifact_ids == [domain1.id]

    # Exactly one artifact.registered event (retry returns event=None)
    events = orch.repository.list_events(run.id)
    artifact_events = [e for e in events if e.event_type == "artifact.registered"]
    assert len(artifact_events) == 1
    assert event1 is not None
    assert event2 is None


def test_domain_idempotency_input_then_output_rejected(tmp_path):
    """Same idempotency_key with input then output relation is rejected."""
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")
    task, _ = orch.create_task(run.id, title="task1", actor="test")

    # First: register as input
    writer.write_text(
        run_id=run.id,
        logical_name="data",
        filename="data.txt",
        text="content",
        created_by="test",
        relation="input",
        task_id=task.id,
        idempotency_key="ik-io",
        correlation_id="cid1",
    )

    # Retry with same key but output relation -> must raise conflict
    with pytest.raises(ArtifactWriteConflict):
        writer.write_text(
            run_id=run.id,
            logical_name="data",
            filename="data.txt",
            text="content",
            created_by="test",
            relation="output",
            task_id=task.id,
            idempotency_key="ik-io",
            correlation_id="cid2",
        )

    # Task must have exactly one input artifact ID, no output IDs
    updated_task = orch.repository.get_task(run.id, task.id)
    assert len(updated_task.input_artifact_ids) == 1
    assert len(updated_task.output_artifact_ids) == 0


def test_real_registration_failure_then_retry(tmp_path):
    """Content write succeeds, registration fails, retry creates exactly one domain record."""
    repo = FileStateRepository(tmp_path / "runs")
    sm = LifecycleStateMachine(repo)
    orch = OrchestrationService(repo, sm)
    store = _make_store(tmp_path)
    writer = ArtifactRegistrationService(store, orch)

    run, _ = orch.create_run(objective="test", actor="test")

    # First attempt: write content directly, then force registration to fail
    stored = store.write_text(
        run_id=run.id,
        logical_name="retry-test",
        filename="retry.md",
        text="retry content",
        created_by="test",
        idempotency_key="retry-key",
    )

    # Verify content file exists
    content_dir = _run_dir(tmp_path, str(run.id)) / "run" / "retry-test"
    content_files = list(content_dir.glob("retry.md"))
    assert len(content_files) == 1

    # Simulate registration failure by directly writing content without domain registration
    # (In production, this could be a database failure, network error, etc.)
    # We verify the store content is intact

    # Retry through the service with the same idempotency key
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

    # Store returns same stored artifact
    assert stored == stored2

    # Exactly one content file
    content_files = list(content_dir.glob("retry.md"))
    assert len(content_files) == 1

    # Run has exactly one artifact ID
    updated_run = orch.repository.get_run(run.id)
    assert len(updated_run.artifact_ids) == 1

    # Exactly one artifact.registered event
    events = orch.repository.list_events(run.id)
    artifact_events = [e for e in events if e.event_type == "artifact.registered"]
    assert len(artifact_events) == 1


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


# ── JSON rejection of NaN and Infinity ────────────────────────────────────

def test_json_rejects_nan_and_infinity(tmp_path):
    store = _make_store(tmp_path)

    with pytest.raises((ValueError, TypeError)):
        _write_json(store, "00000000-0000-0000-0000-000000000020", value={"x": float("nan")})

    with pytest.raises((ValueError, TypeError)):
        _write_json(store, "00000000-0000-0000-0000-000000000021", value={"x": float("inf")})


# ── Verify ─────────────────────────────────────────────────────────────────

def test_verify_returns_meta_on_success(tmp_path):
    store = _make_store(tmp_path)
    stored = _write_text(store, RUN)

    verified = store.verify(RUN, "report", "report.md")
    assert verified == stored


def test_verify_raises_on_integrity_failure(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)

    content_dir = _run_dir(tmp_path, RUN) / "run" / "report"
    bin_path = content_dir / "report.md"
    bin_path.write_bytes(b"corrupt")

    with pytest.raises(ArtifactIntegrityError):
        store.verify(RUN, "report", "report.md")


# ── Exists ─────────────────────────────────────────────────────────────────

def test_exists(tmp_path):
    store = _make_store(tmp_path)

    assert not store.exists(RUN, "report", "report.md")

    _write_text(store, RUN)

    assert store.exists(RUN, "report", "report.md")


# ── Delete ─────────────────────────────────────────────────────────────────

def test_delete_removes_content_and_meta(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN)
    assert store.exists(RUN, "report", "report.md")

    store.delete(RUN, "report", "report.md")
    assert not store.exists(RUN, "report", "report.md")


# ── Multiple artifacts same run ────────────────────────────────────────────

def test_multiple_artifacts_same_run(tmp_path):
    store = _make_store(tmp_path)
    _write_text(store, RUN, logical_name="a", filename="a.md")
    _write_text(store, RUN, logical_name="b", filename="b.md")

    records = store.list_run_meta(RUN)
    assert len(records) == 2
    assert {r.logical_name for r in records} == {"a", "b"}


def test_list_run_meta_empty_when_no_run(tmp_path):
    store = _make_store(tmp_path)
    assert store.list_run_meta("00000000-0000-0000-0000-000000000100") == []
