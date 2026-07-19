"""Filesystem-backed Artifact Store for CoFounder OS binary content.

The store persists the *content* of artifacts (reports, code, data, checkpoints)
to a configurable root directory.  Metadata records live in the state repository;
this module owns the durable bytes plus integrity verification.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.models import utc_now

_LOGICAL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _uuid_text(value: Union[UUID, str]) -> str:
    """Return a canonical UUID string or raise ValueError."""
    return str(UUID(str(value)))


def _sha256_file(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of *path*."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    """Return the hex-encoded SHA-256 digest of *payload*."""
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: Any) -> str:
    """Encode *value* as canonical JSON.

    Only JSON-compatible values are accepted (dict, list, str, int, float,
    bool, None).  ``NaN`` and ``Infinity`` are rejected.
    """
    if isinstance(value, (bool, int, float, str, type(None))):
        pass
    elif isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"JSON object keys must be str, got {type(k).__name__}: {k!r}")
            _canonical_json(v)
        for v in value.values():
            _canonical_json(v)
    elif isinstance(value, list):
        for v in value:
            _canonical_json(v)
    else:
        raise TypeError(f"Cannot serialize {type(value).__name__} as JSON-compatible value")

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"
    except ValueError as exc:
        if "NaN" in str(exc) or "Infinity" in str(exc) or "-Infinity" in str(exc):
            raise ValueError("JSON values must not be NaN or Infinity") from exc
        raise


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

    model_config = ConfigDict(
        use_enum_values=True,
        extra="forbid",
        validate_assignment=True,
    )

    run_id: UUID
    task_id: Optional[UUID] = None
    logical_name: str
    filename: str
    uri: str
    content_type: Optional[str] = None
    checksum_sha256: str
    size_bytes: int
    created_by: str
    format_version: str = "1.0"
    idempotency_key: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    provenance: Dict[str, Any] = Field(default_factory=dict)


class _PathComponents(BaseModel):
    """Validated path components for an artifact."""

    model_config = ConfigDict(extra="forbid")

    scope: str  # "run" or "task"
    run_id: str  # always the run UUID
    scope_id: str  # task UUID for task scope, run UUID for run scope
    logical_name: str
    filename: str
    relative_dir: str  # e.g. "run/plan" or "tasks/0000.../plan"

    @property
    def uri(self) -> str:
        """Portable unique artifact URI.

        Format:
          run:   artifact://runs/<run-id>/artifacts/run/<logical-name>/<filename>
          task:  artifact://runs/<run-id>/artifacts/tasks/<task-id>/<logical-name>/<filename>
        """
        if self.scope == "task":
            return (
                f"artifact://runs/{self.run_id}/artifacts"
                f"/tasks/{self.scope_id}/{self.logical_name}/{self.filename}"
            )
        return (
            f"artifact://runs/{self.run_id}/artifacts"
            f"/run/{self.logical_name}/{self.filename}"
        )


class FileArtifactStore:
    """Atomic filesystem-backed artifact content store.

    Layout under ``root`` (default ``data``)::

        data/
          .locks/
          runs/<run-id>/
            artifacts/
              run/<logical-name>/
                <filename>
                meta.json
              tasks/<task-id>/<logical-name>/
                <filename>
                meta.json
    """

    def __init__(self, root: Union[str, Path] = "data") -> None:
        self.root = Path(root)
        self._ensure_directory(self.root)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    @staticmethod
    def _safe_logical_name(logical_name: str) -> str:
        """Validate and sanitize a logical name for use in paths."""
        cleaned = logical_name.strip().lower()
        if not cleaned:
            raise ArtifactPathError("logical_name must not be empty")
        if not _LOGICAL_NAME_RE.match(cleaned):
            raise ArtifactPathError(
                f"logical_name must match [a-z0-9][a-z0-9_-]*, got {logical_name!r}"
            )
        return cleaned

    @staticmethod
    def _safe_filename(filename: str) -> str:
        """Validate a filename for use as the actual content file."""
        if not filename or filename in (".", ".."):
            raise ArtifactPathError(f"Invalid filename: {filename!r}")
        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise ArtifactPathError(
                f"Filename must not contain path separators or NUL: {filename!r}"
            )
        return filename

    @staticmethod
    def _validate_no_components(value: str) -> None:
        """Raise ArtifactPathError if *value* contains path traversal."""
        if ".." in value.split("/"):
            raise ArtifactPathError(f"Path contains traversal: {value!r}")
        if value.startswith("/"):
            raise ArtifactPathError(f"Path must be relative: {value!r}")
        for component in value.split("/"):
            if not component:
                raise ArtifactPathError(f"Empty path component in: {value!r}")

    def _validate_store_root(self, path: Path) -> None:
        """Raise ArtifactPathError if *path* escapes the store root."""
        try:
            path.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise ArtifactPathError(
                f"Artifact path escapes store root: {path}"
            )

    @staticmethod
    def _assert_no_symlink(path: Path) -> None:
        """Raise ArtifactPathError if *path* is a symlink."""
        if path.is_symlink():
            raise ArtifactPathError(f"Symlink not allowed: {path}")

    def _assert_no_symlink_in_ancestors(self, path: Path) -> None:
        """Raise ArtifactPathError if any ancestor of *path* up to store root is a symlink."""
        current = path.parent
        root_resolved = self.root.resolve()
        while current != current.parent:  # Stop at filesystem root
            if current.resolve() == root_resolved:
                break
            if current.is_symlink():
                raise ArtifactPathError(f"Symlink not allowed in parent: {current}")
            current = current.parent

    def _resolve_paths(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> _PathComponents:
        """Return validated path components for an artifact."""
        rid = _uuid_text(run_id)
        safe_logical = self._safe_logical_name(logical_name)
        safe_filename = self._safe_filename(filename)

        scope = "task" if task_id is not None else "run"
        scope_id = _uuid_text(task_id) if task_id is not None else rid

        self._validate_no_components(safe_logical)
        self._validate_no_components(safe_filename)

        if scope == "task":
            relative_dir = f"tasks/{scope_id}/{safe_logical}"
        else:
            relative_dir = f"run/{safe_logical}"

        return _PathComponents(
            scope=scope,
            run_id=rid,
            scope_id=scope_id,
            logical_name=safe_logical,
            filename=safe_filename,
            relative_dir=relative_dir,
        )

    def _content_dir(self, run_id: Union[UUID, str], components: _PathComponents) -> Path:
        """Return the artifact content directory under root/runs/<run-id>/artifacts."""
        run_id_text = _uuid_text(run_id)
        return (
            self.root / "runs" / run_id_text / "artifacts" / components.relative_dir
        )

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @contextmanager
    def _run_lock(self, run_id: Union[UUID, str]) -> Iterator[None]:
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
        self, destination: Path, payload: bytes
    ) -> str:
        """Write *payload* to *destination* atomically and return its checksum."""
        self._ensure_directory(destination.parent)
        checksum = _sha256_bytes(payload)

        self._validate_store_root(destination)
        self._assert_no_symlink(destination)
        self._assert_no_symlink_in_ancestors(destination)

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

    @staticmethod
    def _read_meta(meta_path: Path) -> Dict[str, Any]:
        """Return parsed metadata dict or raise ArtifactIntegrityError."""
        if not meta_path.exists():
            raise ArtifactIntegrityError(
                f"Artifact metadata missing: {meta_path}"
            )
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ArtifactIntegrityError(
                f"Artifact metadata corrupt: {meta_path}"
            ) from exc

    @staticmethod
    def _clean_temp_files(directory: Path) -> None:
        """Remove controlled temporary files from *directory* under lock."""
        if not directory.exists():
            return
        for tmp in directory.glob("*.tmp"):
            try:
                tmp.unlink()
            except OSError:
                pass

    def _find_meta_by_idempotency(
        self, idempotency_key: str
    ) -> Iterator[tuple[Path, Dict[str, Any]]]:
        """Yield (meta_path, meta_dict) for every meta.json matching *idempotency_key*."""
        runs_dir = self.root / "runs"
        if not runs_dir.exists():
            return
        for meta_path in runs_dir.glob("*/artifacts/**/meta.json"):
            try:
                meta = self._read_meta(meta_path)
            except ArtifactIntegrityError:
                continue
            if meta.get("idempotency_key") == idempotency_key:
                yield meta_path, meta

    @staticmethod
    def _check_idempotency_compatibility(
        existing: Dict[str, Any],
        requested: Dict[str, Any],
    ) -> None:
        """Raise ArtifactConflictError if *existing* and *requested* are incompatible."""
        if existing.get("run_id") != requested.get("run_id"):
            raise ArtifactConflictError("idempotency key run_id conflict")
        if existing.get("task_id") != requested.get("task_id"):
            raise ArtifactConflictError("idempotency key task_id conflict")
        if existing.get("logical_name") != requested.get("logical_name"):
            raise ArtifactConflictError("idempotency key logical_name conflict")
        if existing.get("filename") != requested.get("filename"):
            raise ArtifactConflictError("idempotency key filename conflict")
        if existing.get("content_type") != requested.get("content_type"):
            raise ArtifactConflictError("idempotency key content_type conflict")
        if existing.get("checksum_sha256") != requested.get("checksum_sha256"):
            raise ArtifactConflictError("idempotency key checksum conflict")
        if existing.get("size_bytes") != requested.get("size_bytes"):
            raise ArtifactConflictError("idempotency key size_bytes conflict")
        if existing.get("created_by") != requested.get("created_by"):
            raise ArtifactConflictError("idempotency key created_by conflict")
        if existing.get("format_version") != requested.get("format_version"):
            raise ArtifactConflictError("idempotency key format_version conflict")
        if existing.get("provenance") != requested.get("provenance"):
            raise ArtifactConflictError("idempotency key provenance conflict")

    def _recover_or_validate(
        self,
        content_dir: Path,
        content_path: Path,
        meta_path: Path,
        requested: "StoredArtifact",
        requested_bytes: bytes,
    ) -> "StoredArtifact":
        """Handle partial-state recovery inside the run lock.

        Returns an existing StoredArtifact for compatible idempotent requests.
        Raises ArtifactIntegrityError or ArtifactConflictError for incompatible
        or unrecoverable states.
        """
        has_content = content_path.exists()
        has_meta = meta_path.exists()
        content_corrupt = False
        meta_corrupt = False

        if has_content:
            try:
                actual_checksum = _sha256_file(content_path)
            except OSError:
                has_content = False

        if has_meta:
            try:
                existing_meta = self._read_meta(meta_path)
            except ArtifactIntegrityError:
                has_meta = False
                meta_corrupt = True

        # State A: valid metadata + valid content
        if has_meta and has_content and not meta_corrupt and not content_corrupt:
            existing_key = existing_meta.get("idempotency_key")
            if existing_key is not None and existing_key == requested.idempotency_key:
                if actual_checksum != existing_meta.get("checksum_sha256"):
                    raise ArtifactIntegrityError(
                        f"Content checksum mismatch for idempotency key "
                        f"{existing_key!r}"
                    )
                self._check_idempotency_compatibility(
                    existing_meta,
                    {
                        "run_id": str(requested.run_id),
                        "task_id": str(requested.task_id) if requested.task_id else None,
                        "logical_name": requested.logical_name,
                        "filename": requested.filename,
                        "content_type": requested.content_type,
                        "checksum_sha256": requested.checksum_sha256,
                        "size_bytes": requested.size_bytes,
                        "created_by": requested.created_by,
                        "format_version": requested.format_version,
                        "provenance": requested.provenance,
                    },
                )
                return StoredArtifact(**existing_meta)

        # Cross-run idempotency check: search ALL existing metadata for the key
        if requested.idempotency_key is not None:
            requested_dict = {
                "run_id": str(requested.run_id),
                "task_id": str(requested.task_id) if requested.task_id else None,
                "logical_name": requested.logical_name,
                "filename": requested.filename,
                "content_type": requested.content_type,
                "checksum_sha256": requested.checksum_sha256,
                "size_bytes": requested.size_bytes,
                "created_by": requested.created_by,
                "format_version": requested.format_version,
                "provenance": requested.provenance,
            }
            for _meta_path, existing_meta in self._find_meta_by_idempotency(requested.idempotency_key):
                existing_key = existing_meta.get("idempotency_key")
                if existing_key == requested.idempotency_key:
                    try:
                        self._check_idempotency_compatibility(existing_meta, requested_dict)
                        return StoredArtifact(**existing_meta)
                    except ArtifactConflictError:
                        raise ArtifactConflictError(
                            f"Idempotency key {requested.idempotency_key!r} "
                            f"conflicts with existing artifact at {_meta_path.parent}"
                        )

        # State B: metadata exists but content is missing or corrupt
        if has_meta and not has_content:
            existing_key = existing_meta.get("idempotency_key")
            if existing_key is not None and existing_key == requested.idempotency_key:
                if existing_meta.get("checksum_sha256") == _sha256_bytes(requested_bytes):
                    stored_checksum = self._atomic_write(content_path, requested_bytes)
                    repaired = requested.model_copy(deep=True)
                    repaired.checksum_sha256 = stored_checksum
                    repaired.size_bytes = len(requested_bytes)
                    self._atomic_write(meta_path, repaired.model_dump_json(indent=2).encode("utf-8") + b"\n")
                    return repaired
            raise ArtifactIntegrityError(
                f"Artifact content missing for {content_path}"
            )

        # State C: content exists but metadata is absent or corrupt
        if has_content and not has_meta:
            if actual_checksum == _sha256_bytes(requested_bytes):
                self._atomic_write(meta_path, requested.model_dump_json(indent=2).encode("utf-8") + b"\n")
                return requested
            raise ArtifactIntegrityError(
                f"Artifact metadata missing for {content_path} and "
                f"content checksum does not match request"
            )

        # No usable state found
        return None

    # ── Internal bytes API ─────────────────────────────────────────────────

    def write_bytes(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        content: bytes,
        created_by: str,
        task_id: Optional[Union[UUID, str]] = None,
        content_type: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> StoredArtifact:
        """Persist *content* for an artifact and return a stored record.

        Same idempotency_key + identical canonical bytes returns the existing
        record without rewriting.  Same key + different bytes raises
        ArtifactConflictError.
        """
        components = self._resolve_paths(run_id, logical_name, filename, task_id)
        run_id_text = _uuid_text(run_id)
        content_dir = self._content_dir(run_id, components)
        content_path = content_dir / components.filename
        meta_path = content_dir / "meta.json"

        self._validate_store_root(content_dir)
        self._assert_no_symlink(content_dir)
        self._assert_no_symlink_in_ancestors(content_dir)

        canonical_checksum = _sha256_bytes(content)
        now = utc_now()

        requested = StoredArtifact(
            run_id=UUID(run_id_text),
            task_id=UUID(components.scope_id) if components.scope == "task" else None,
            logical_name=components.logical_name,
            filename=components.filename,
            uri=components.uri,
            content_type=content_type,
            checksum_sha256=canonical_checksum,
            size_bytes=len(content),
            created_by=created_by,
            format_version="1.0",
            idempotency_key=idempotency_key,
            created_at=now,
            provenance=dict(provenance or {}),
        )

        with self._run_lock(run_id):
            self._clean_temp_files(content_dir)

            recovered = self._recover_or_validate(
                content_dir, content_path, meta_path, requested, content
            )
            if recovered is not None:
                return recovered

            if content_path.exists() or meta_path.exists():
                raise ArtifactConflictError(
                    f"Artifact already stored: {content_path}"
                )

            stored_checksum = self._atomic_write(content_path, content)
            requested.checksum_sha256 = stored_checksum
            requested.size_bytes = len(content)

            meta_payload = requested.model_dump_json(indent=2) + "\n"
            self._atomic_write(meta_path, meta_payload.encode("utf-8"))

        return requested

    def read_bytes(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> bytes:
        """Return the content bytes for an artifact.

        Verifies SHA-256 integrity against the sidecar metadata.
        """
        components = self._resolve_paths(run_id, logical_name, filename, task_id)
        content_dir = self._content_dir(run_id, components)
        content_path = content_dir / components.filename
        meta_path = content_dir / "meta.json"

        self._validate_store_root(content_path)
        self._assert_no_symlink(content_path)
        self._assert_no_symlink_in_ancestors(content_path)

        if not content_path.exists():
            raise ArtifactIntegrityError(
                f"Artifact content missing: {content_path}"
            )

        meta = self._read_meta(meta_path)
        computed = _sha256_file(content_path)
        if computed != meta.get("checksum_sha256"):
            raise ArtifactIntegrityError(
                f"Artifact integrity failure for {content_path}: "
                f"expected {meta.get('checksum_sha256')}, got {computed}"
            )

        return content_path.read_bytes()

    # ── Approved public API ────────────────────────────────────────────────

    def write_text(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        text: str,
        created_by: str,
        task_id: Optional[Union[UUID, str]] = None,
        content_type: str = "text/plain; charset=utf-8",
        idempotency_key: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> StoredArtifact:
        """Persist *text* as an artifact and return a stored record."""
        return self.write_bytes(
            run_id=run_id,
            logical_name=logical_name,
            filename=filename,
            content=text.encode("utf-8"),
            created_by=created_by,
            task_id=task_id,
            content_type=content_type,
            idempotency_key=idempotency_key,
            provenance=provenance,
        )

    def canonical_json_bytes(self, value: Any) -> bytes:
        """Return the canonical JSON bytes for *value* without writing.

        The returned bytes are identical to what ``write_json`` persists,
        so callers can compute checksums or idempotency keys before writing.
        """
        return _canonical_json(value).encode("utf-8")

    def write_json(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        value: Any,
        created_by: str,
        task_id: Optional[Union[UUID, str]] = None,
        content_type: str = "application/json; charset=utf-8",
        idempotency_key: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> StoredArtifact:
        """Persist *value* as canonical JSON and return a stored record.

        Only JSON-compatible values are accepted.  ``NaN`` and ``Infinity``
        are rejected.
        """
        payload = _canonical_json(value)
        return self.write_bytes(
            run_id=run_id,
            logical_name=logical_name,
            filename=filename,
            content=payload.encode("utf-8"),
            created_by=created_by,
            task_id=task_id,
            content_type=content_type,
            idempotency_key=idempotency_key,
            provenance=provenance,
        )

    def read_text(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> str:
        """Return the text content for an artifact."""
        content = self.read_bytes(run_id, logical_name, filename, task_id)
        return content.decode("utf-8")

    def read_json(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> Any:
        """Return the parsed JSON content for an artifact."""
        content = self.read_bytes(run_id, logical_name, filename, task_id)
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError(
                f"Artifact is not valid JSON: {exc}"
            ) from exc

    def verify(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> StoredArtifact:
        """Verify integrity and return the stored record.

        Raises ArtifactIntegrityError if the check fails.
        """
        self.read_bytes(run_id, logical_name, filename, task_id)
        components = self._resolve_paths(run_id, logical_name, filename, task_id)
        content_dir = self._content_dir(run_id, components)
        meta_path = content_dir / "meta.json"
        meta = self._read_meta(meta_path)
        return StoredArtifact(**meta)

    def exists(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> bool:
        """Return True if both content and metadata exist."""
        components = self._resolve_paths(run_id, logical_name, filename, task_id)
        content_dir = self._content_dir(run_id, components)
        content_path = content_dir / components.filename
        meta_path = content_dir / "meta.json"
        return content_path.exists() and meta_path.exists()

    def delete(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        task_id: Optional[Union[UUID, str]] = None,
    ) -> None:
        """Remove content and metadata for an artifact."""
        components = self._resolve_paths(run_id, logical_name, filename, task_id)
        content_dir = self._content_dir(run_id, components)
        content_path = content_dir / components.filename
        meta_path = content_dir / "meta.json"

        self._validate_store_root(content_path)
        self._assert_no_symlink(content_path)
        self._assert_no_symlink_in_ancestors(content_path)

        if content_path.exists():
            content_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

        if content_dir.exists() and not any(content_dir.iterdir()):
            content_dir.rmdir()

    def list_run_meta(
        self, run_id: Union[UUID, str]
    ) -> List[StoredArtifact]:
        """Return StoredArtifact records for every artifact in *run_id*."""
        rid = _uuid_text(run_id)
        run_dir = self.root / "runs" / rid / "artifacts"

        self._validate_store_root(run_dir)

        if not run_dir.exists():
            return []

        records: List[StoredArtifact] = []
        for meta_path in sorted(run_dir.glob("**/meta.json")):
            try:
                meta = self._read_meta(meta_path)
                records.append(StoredArtifact(**meta))
            except ArtifactIntegrityError:
                continue

        return records
