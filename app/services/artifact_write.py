"""Thin application service for writing artifacts through FileArtifactStore
and registering them in the orchestration state repository.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Union
from uuid import UUID

from app.artifacts import ArtifactConflictError, ArtifactIntegrityError, ArtifactPathError, FileArtifactStore
from app.domain import ArtifactKind
from app.services.orchestration import ArtifactRelationError, OrchestrationService


class ArtifactWriteError(RuntimeError):
    """Base error for artifact write and registration operations."""


class ArtifactWriteConflict(ArtifactWriteError):
    """Raised when an artifact write conflicts with existing content."""


class ArtifactWriteIntegrity(ArtifactWriteError):
    """Raised when an artifact write or registration integrity check fails."""


class ArtifactWritePath(ArtifactWriteError):
    """Raised when an artifact write path is invalid."""


class ArtifactRegistrationService:
    """Coordinate artifact content storage and orchestration registration.

    This service writes content through ``FileArtifactStore`` and then calls
    ``OrchestrationService.register_artifact`` so that domain records are
    updated atomically.
    """

    def __init__(
        self,
        artifact_store: FileArtifactStore,
        orchestration_service: OrchestrationService,
    ) -> None:
        self.artifact_store = artifact_store
        self.orchestration_service = orchestration_service

    def write_text(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        text: str,
        created_by: str,
        *,
        task_id: Optional[Union[UUID, str]] = None,
        content_type: str = "text/plain; charset=utf-8",
        relation: Literal["run", "input", "output"] = "run",
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, Any]:
        """Write text content and register the artifact in orchestration.

        Returns ``(stored_artifact, domain_artifact)`` where ``stored_artifact``
        is the ``StoredArtifact`` record and ``domain_artifact`` is the
        orchestration ``Artifact`` domain record.
        """
        if relation == "run" and task_id is not None:
            raise ArtifactRelationError("Run artifacts must not include task_id")

        if relation in {"input", "output"} and task_id is None:
            raise ArtifactRelationError(
                f"{relation} artifacts require task_id"
            )

        kind = ArtifactKind.REPORT
        if content_type.startswith("text/"):
            kind = ArtifactKind.REPORT
        elif content_type.startswith("application/json"):
            kind = ArtifactKind.DATA
        elif content_type.startswith("application/") or content_type.startswith("binary/"):
            kind = ArtifactKind.DATA

        try:
            stored = self.artifact_store.write_text(
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
        except (ArtifactConflictError, ArtifactIntegrityError, ArtifactPathError) as exc:
            if isinstance(exc, ArtifactConflictError):
                raise ArtifactWriteConflict(str(exc)) from exc
            if isinstance(exc, ArtifactIntegrityError):
                raise ArtifactWriteIntegrity(str(exc)) from exc
            raise ArtifactWritePath(str(exc)) from exc

        domain_artifact, event = self.orchestration_service.register_artifact(
            run_id=run_id,
            kind=kind,
            name=logical_name,
            uri=stored.uri,
            created_by=created_by,
            actor=created_by,
            relation=relation,
            task_id=task_id,
            content_type=content_type,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            correlation_id=correlation_id,
            metadata={
                "filename": stored.filename,
                "logical_name": stored.logical_name,
                "format_version": stored.format_version,
                "idempotency_key": stored.idempotency_key,
                "provenance": stored.provenance,
            },
        )

        return stored, domain_artifact, event

    def write_json(
        self,
        run_id: Union[UUID, str],
        logical_name: str,
        filename: str,
        value: Any,
        created_by: str,
        *,
        task_id: Optional[Union[UUID, str]] = None,
        content_type: str = "application/json; charset=utf-8",
        relation: Literal["run", "input", "output"] = "run",
        idempotency_key: Optional[str] = None,
        correlation_id: Optional[str] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> tuple[Any, Any]:
        """Write JSON content and register the artifact in orchestration.

        Returns ``(stored_artifact, domain_artifact, event)``.
        """
        if relation == "run" and task_id is not None:
            raise ArtifactRelationError("Run artifacts must not include task_id")

        if relation in {"input", "output"} and task_id is None:
            raise ArtifactRelationError(
                f"{relation} artifacts require task_id"
            )

        try:
            stored = self.artifact_store.write_json(
                run_id=run_id,
                logical_name=logical_name,
                filename=filename,
                value=value,
                created_by=created_by,
                task_id=task_id,
                content_type=content_type,
                idempotency_key=idempotency_key,
                provenance=provenance,
            )
        except (ArtifactConflictError, ArtifactIntegrityError, ArtifactPathError) as exc:
            if isinstance(exc, ArtifactConflictError):
                raise ArtifactWriteConflict(str(exc)) from exc
            if isinstance(exc, ArtifactIntegrityError):
                raise ArtifactWriteIntegrity(str(exc)) from exc
            raise ArtifactWritePath(str(exc)) from exc

        domain_artifact, event = self.orchestration_service.register_artifact(
            run_id=run_id,
            kind=ArtifactKind.DATA,
            name=logical_name,
            uri=stored.uri,
            created_by=created_by,
            actor=created_by,
            relation=relation,
            task_id=task_id,
            content_type=content_type,
            checksum_sha256=stored.checksum_sha256,
            size_bytes=stored.size_bytes,
            correlation_id=correlation_id,
            metadata={
                "filename": stored.filename,
                "logical_name": stored.logical_name,
                "format_version": stored.format_version,
                "idempotency_key": stored.idempotency_key,
                "provenance": stored.provenance,
            },
        )

        return stored, domain_artifact, event
