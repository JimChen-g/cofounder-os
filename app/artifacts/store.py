"""Filesystem-backed Artifact Store for CoFounder OS binary content.

The store persists the *content* of artifacts (reports, code, data, checkpoints)
to a configurable root directory.  Metadata records live in the state repository;
this module owns the durable bytes plus integrity verification.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional
from uuid import UUID

from pydantic import BaseModel

from app.domain import Artifact, ArtifactKind


def _uuid_text(value: UUID | str) -> str:
    """Return a canonical UUID string or raise ValueError."""

    return str(UUID(str(value)))


def _sha256_file(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of *path*."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArtifactStoreError(RuntimeError):
    """Base error for filesystem artifact store operations."""


class ArtifactConflictError(ArtifactStoreError):
    """Raised when a store operation targets an already-stored artifact."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Raised when an artifact content check fails."""


class ArtifactPathError(ArtifactStoreError):
    """Raised when an artifact path is invalid or escapes the store root."""


class StoredArtifact(BaseModel):
    """In-memory record describing a stored artifact's content and location."""

    model_config = {"use_enum_values": True, "extra": "forbid"}

    artifact_id: UUID
    run_id: UUID
    kind: ArtifactKind
    name: str
    content_type: Optional[str] = None
    size_bytes: int
    checksum_sha256: str
    relative_path: str


class FileArtifactStore:
    """Atomic filesystem-backed artifact content store.

    Artifact content is stored under::

        root/
          {run_id}/
            {artifact_id}.bin
            {artifact_id}.meta.json

    One sidecar metadata file records the declared checksum so integrity can
    be verified without re-reading the full content.
    """

    def __init__(self, root: str | Path = "data/artifacts") -> None:
        self.root = Path(root)
        self._ensure_directory(self.root)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    def _run_dir(self, run_id: UUID | str) -> Path:
        return self.root / _uuid_text(run_id)

    def _artifact_bin_path(
        self, run_id: UUID | str, artifact_id: UUID | str
    ) -> Path:
        return self._run_dir(run_id) / f"{_uuid_text(artifact_id)}.bin"

    def _artifact_meta_path(
        self, run_id: UUID | str, artifact_id: UUID | str
    ) -> Path:
        return self._run_dir(run_id) / f"{_uuid_text(artifact_id)}.meta.json"

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _validate_store_root(self, path: Path) -> None:
        """Raise ArtifactPathError if *path* escapes the store root."""

        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise ArtifactPathError(
                f"Artifact path escapes store root: {path}"
            )

    @contextmanager
    def _run_lock(
        self, run_id: UUID | str
    ) -> Iterator[None]:
        """Advisory exclusive lock for one run's artifact directory."""

        canonical_id = _uuid_text(run_id)
        locks_dir = self.root / ".locks"
        self._ensure_directory(locks_dir)
        lock_path = locks_dir / f"{canonical_id}.lock"

        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _atomic_write(
        self,
        destination: Path,
        payload: bytes,
    ) -> str:
        """Write *payload* to *destination* atomically and return its checksum."""

        self._ensure_directory(destination.parent)
        checksum = hashlib.sha256(payload).hexdigest()

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        temporary_path = Path(temporary_name)

        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary_path, destination)
            os.chmod(destination, 0o600)
            self._fsync_directory(destination.parent)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

        return checksum

    def _read_meta(self, meta_path: Path) -> Dict:
        """Return parsed metadata dict or raise ArtifactIntegrityError."""

        if not meta_path.exists():
            raise ArtifactIntegrityError(
                f"Artifact metadata missing: {meta_path}"
            )

        import json

        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ArtifactIntegrityError(
                f"Artifact metadata corrupt: {meta_path}"
            ) from exc

    # ── Public API ─────────────────────────────────────────────────────────

    def store(
        self,
        artifact: Artifact,
        content: bytes,
        expected_checksum: Optional[str] = None,
    ) -> StoredArtifact:
        """Persist *content* for *artifact*, returning a stored record.

        *expected_checksum* may be provided for a pre-flight integrity check.
        The computed checksum must match or ArtifactIntegrityError is raised.

        If the artifact content already exists, ArtifactConflictError is raised.
        """

        run_id = _uuid_text(artifact.run_id)
        artifact_id = _uuid_text(artifact.id)
        bin_path = self._artifact_bin_path(run_id, artifact_id)
        meta_path = self._artifact_meta_path(run_id, artifact_id)

        self._validate_store_root(bin_path)
        self._validate_store_root(meta_path)

        if bin_path.exists() or meta_path.exists():
            raise ArtifactConflictError(
                f"Artifact already stored: {artifact_id}"
            )

        if expected_checksum is not None:
            computed = hashlib.sha256(content).hexdigest()
            if computed != expected_checksum:
                raise ArtifactIntegrityError(
                    f"Artifact checksum mismatch for {artifact_id}: "
                    f"expected {expected_checksum}, got {computed}"
                )

        with self._run_lock(run_id):
            checksum = self._atomic_write(bin_path, content)

            meta = StoredArtifact(
                artifact_id=artifact.id,
                run_id=artifact.run_id,
                kind=artifact.kind,
                name=artifact.name,
                content_type=artifact.content_type,
                size_bytes=len(content),
                checksum_sha256=checksum,
                relative_path=f"{run_id}/{artifact_id}.bin",
            )
            meta_path.write_text(
                meta.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(meta_path, 0o600)
            self._fsync_directory(meta_path.parent)

        return meta

    def get(self, artifact_id: UUID | str, run_id: UUID | str) -> bytes:
        """Return the content bytes for *artifact_id* under *run_id*.

        Verifies SHA-256 integrity against the sidecar metadata.
        """

        rid = _uuid_text(run_id)
        aid = _uuid_text(artifact_id)
        bin_path = self._artifact_bin_path(rid, aid)
        meta_path = self._artifact_meta_path(rid, aid)

        self._validate_store_root(bin_path)
        self._validate_store_root(meta_path)

        if not bin_path.exists():
            raise ArtifactIntegrityError(
                f"Artifact content missing: {aid}"
            )

        meta = self._read_meta(meta_path)
        computed = _sha256_file(bin_path)
        if computed != meta.get("checksum_sha256"):
            raise ArtifactIntegrityError(
                f"Artifact integrity failure for {aid}: "
                f"expected {meta.get('checksum_sha256')}, got {computed}"
            )

        return bin_path.read_bytes()

    def get_meta(
        self, artifact_id: UUID | str, run_id: UUID | str
    ) -> StoredArtifact:
        """Return the StoredArtifact metadata record without reading content."""

        rid = _uuid_text(run_id)
        aid = _uuid_text(artifact_id)
        meta_path = self._artifact_meta_path(rid, aid)

        self._validate_store_root(meta_path)

        meta = self._read_meta(meta_path)
        return StoredArtifact(**meta)

    def list_run_meta(
        self, run_id: UUID | str
    ) -> List[StoredArtifact]:
        """Return StoredArtifact records for every artifact in *run_id*."""

        rid = _uuid_text(run_id)
        run_dir = self._run_dir(rid)

        self._validate_store_root(run_dir)

        if not run_dir.exists():
            return []

        records: List[StoredArtifact] = []
        for meta_path in sorted(run_dir.glob("*.meta.json")):
            try:
                meta = self._read_meta(meta_path)
                records.append(StoredArtifact(**meta))
            except ArtifactIntegrityError:
                continue

        return records

    def exists(
        self, artifact_id: UUID | str, run_id: UUID | str
    ) -> bool:
        """Return True if both content and metadata exist for *artifact_id*."""

        rid = _uuid_text(run_id)
        aid = _uuid_text(artifact_id)
        bin_path = self._artifact_bin_path(rid, aid)
        meta_path = self._artifact_meta_path(rid, aid)

        self._validate_store_root(bin_path)
        self._validate_store_root(meta_path)

        return bin_path.exists() and meta_path.exists()

    def delete(
        self, artifact_id: UUID | str, run_id: UUID | str
    ) -> None:
        """Remove content and metadata for *artifact_id*."""

        rid = _uuid_text(run_id)
        aid = _uuid_text(artifact_id)
        bin_path = self._artifact_bin_path(rid, aid)
        meta_path = self._artifact_meta_path(rid, aid)

        self._validate_store_root(bin_path)
        self._validate_store_root(meta_path)

        if bin_path.exists():
            bin_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

        run_dir = self._run_dir(rid)
        if run_dir.exists() and not any(run_dir.iterdir()):
            run_dir.rmdir()

    def verify(
        self, artifact_id: UUID | str, run_id: UUID | str
    ) -> StoredArtifact:
        """Verify integrity and return the stored record.

        Raises ArtifactIntegrityError if the check fails.
        """

        self.get(artifact_id, run_id)
        return self.get_meta(artifact_id, run_id)
