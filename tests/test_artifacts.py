"""Tests for the filesystem Artifact Store."""

from __future__ import annotations

import pytest

from app.artifacts import (
    ArtifactConflictError,
    ArtifactIntegrityError,
    FileArtifactStore,
)
from app.domain import Artifact, ArtifactKind


CONTENT = b"Hello, Artifact Store!"


def _make_artifact(run_id, artifact_id=None, kind=ArtifactKind.REPORT, name="report.md"):
    aid = artifact_id if artifact_id is not None else run_id
    return Artifact(
        run_id=run_id,
        id=aid,
        kind=kind,
        name=name,
        uri=f"artifact://{name}",
        created_by="test",
    )


def test_store_and_retrieve_round_trip(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000001")

    stored = store.store(artifact, CONTENT)

    assert stored.artifact_id == artifact.id
    assert stored.run_id == artifact.run_id
    assert stored.kind == artifact.kind
    assert stored.name == artifact.name
    assert stored.size_bytes == len(CONTENT)
    assert stored.checksum_sha256 == __import__("hashlib").sha256(CONTENT).hexdigest()
    assert stored.relative_path == f"{str(artifact.run_id)}/{str(artifact.id)}.bin"

    retrieved = store.get(artifact.id, artifact.run_id)
    assert retrieved == CONTENT


def test_store_detects_checksum_mismatch(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000002")

    with pytest.raises(ArtifactIntegrityError):
        store.store(artifact, CONTENT, expected_checksum="sha256:bad")


def test_store_rejects_duplicate(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000003")

    store.store(artifact, CONTENT)

    with pytest.raises(ArtifactConflictError):
        store.store(artifact, CONTENT)


def test_get_verifies_integrity(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000004")

    store.store(artifact, CONTENT)

    meta_path = (
        tmp_path / "artifacts" / str(artifact.run_id) / f"{artifact.id}.meta.json"
    )
    meta = __import__("json").loads(meta_path.read_text(encoding="utf-8"))
    meta["checksum_sha256"] = "sha256:corrupt"
    meta_path.write_text(__import__("json").dumps(meta) + "\n", encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        store.get(artifact.id, artifact.run_id)


def test_get_missing_content_raises(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000005")

    with pytest.raises(ArtifactIntegrityError):
        store.get(artifact.id, artifact.run_id)


def test_get_meta_without_reading_content(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000006")

    stored = store.store(artifact, CONTENT)
    meta = store.get_meta(artifact.id, artifact.run_id)

    assert meta == stored


def test_list_run_meta(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = "00000000-0000-0000-0000-000000000007"
    a1 = _make_artifact(run_id=run_id, name="a.md")
    a2 = _make_artifact(run_id=run_id, artifact_id="00000000-0000-0000-0000-000000000008", name="b.md")

    store.store(a1, CONTENT)
    store.store(a2, CONTENT + b" extra")

    records = store.list_run_meta(run_id)
    assert [r.artifact_id for r in records] == [a1.id, a2.id]
    assert [r.name for r in records] == ["a.md", "b.md"]


def test_list_run_meta_empty_when_no_run(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    assert store.list_run_meta("00000000-0000-0000-0000-000000000009") == []


def test_exists(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000010")

    assert not store.exists(artifact.id, artifact.run_id)

    store.store(artifact, CONTENT)

    assert store.exists(artifact.id, artifact.run_id)


def test_delete_removes_content_and_meta(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000011")

    store.store(artifact, CONTENT)
    assert store.exists(artifact.id, artifact.run_id)

    store.delete(artifact.id, artifact.run_id)
    assert not store.exists(artifact.id, artifact.run_id)

    run_dir = tmp_path / "artifacts" / str(artifact.run_id)
    assert not run_dir.exists()


def test_delete_cleans_empty_run_dir(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000012")

    store.store(artifact, CONTENT)
    store.delete(artifact.id, artifact.run_id)

    run_dir = tmp_path / "artifacts" / str(artifact.run_id)
    assert not run_dir.exists()


def test_verify_returns_meta_on_success(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000013")

    stored = store.store(artifact, CONTENT)
    verified = store.verify(artifact.id, artifact.run_id)

    assert verified == stored


def test_verify_raises_on_integrity_failure(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000014")

    store.store(artifact, CONTENT)

    bin_path = (
        tmp_path / "artifacts" / str(artifact.run_id) / f"{artifact.id}.bin"
    )
    bin_path.write_bytes(b"corrupt")

    with pytest.raises(ArtifactIntegrityError):
        store.verify(artifact.id, artifact.run_id)


def test_store_creates_permission_restricted_files(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    artifact = _make_artifact(run_id="00000000-0000-0000-0000-000000000015")

    store.store(artifact, CONTENT)

    bin_path = (
        tmp_path / "artifacts" / str(artifact.run_id) / f"{artifact.id}.bin"
    )
    meta_path = (
        tmp_path / "artifacts" / str(artifact.run_id) / f"{artifact.id}.meta.json"
    )

    assert oct(bin_path.stat().st_mode)[-3:] == "600"
    assert oct(meta_path.stat().st_mode)[-3:] == "600"


def test_root_directory_has_restricted_permissions(tmp_path):
    FileArtifactStore(tmp_path / "artifacts")

    assert oct((tmp_path / "artifacts").stat().st_mode)[-3:] == "700"


def test_store_multiple_artifacts_same_run(tmp_path):
    store = FileArtifactStore(tmp_path / "artifacts")
    run_id = "00000000-0000-0000-0000-000000000016"
    artifacts = [
        _make_artifact(run_id=run_id, artifact_id=f"00000000-0000-0000-0000-{i:012d}", name=f"f{i}.bin")
        for i in range(5)
    ]

    for idx, art in enumerate(artifacts):
        store.store(art, CONTENT + idx.to_bytes(1, "big") * 100)

    records = store.list_run_meta(run_id)
    assert len(records) == 5

    for art in artifacts:
        assert store.exists(art.id, art.run_id)
