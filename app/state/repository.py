"""Atomic filesystem repository for CoFounder OS domain state.

The repository stores one isolated directory per run and uses advisory file
locks plus atomic replacement for state records. It intentionally introduces
no database, queue, network service, or background process.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Type, TypeVar
from uuid import UUID

from pydantic import BaseModel

from app.domain import (
    AgentMessage,
    Approval,
    Artifact,
    AuditEvent,
    RouteDecision,
    Run,
    Task,
)

ModelT = TypeVar("ModelT", bound=BaseModel)


class StateRepositoryError(RuntimeError):
    """Base error for filesystem state operations."""


class RecordAlreadyExists(StateRepositoryError):
    """Raised when a create operation targets an existing record."""


class RecordNotFound(StateRepositoryError):
    """Raised when a requested state record does not exist."""


class RecordScopeError(StateRepositoryError):
    """Raised when a child record does not belong to the selected run."""


def _uuid_text(value: UUID | str) -> str:
    """Return a canonical UUID string or raise ValueError."""

    return str(UUID(str(value)))


class RunTransaction:
    """Operations performed under one exclusive run lock."""

    def __init__(self, repository: "FileStateRepository", run_id: UUID | str):
        self._repository = repository
        self.run_id = _uuid_text(run_id)

    def _run_path(self) -> Path:
        return self._repository._run_dir(self.run_id) / "run.json"

    def _child_path(self, collection: str, record_id: UUID | str) -> Path:
        return (
            self._repository._run_dir(self.run_id)
            / collection
            / f"{_uuid_text(record_id)}.json"
        )

    def _assert_scope(self, record: BaseModel) -> None:
        record_run_id = getattr(record, "run_id", None)
        if record_run_id is None:
            raise RecordScopeError("Child record has no run_id")
        if _uuid_text(record_run_id) != self.run_id:
            raise RecordScopeError(
                f"Record run_id {record_run_id} does not match {self.run_id}"
            )

    def create_run(self, run: Run) -> Run:
        if _uuid_text(run.id) != self.run_id:
            raise RecordScopeError(
                f"Run id {run.id} does not match transaction {self.run_id}"
            )

        path = self._run_path()
        if path.exists():
            raise RecordAlreadyExists(f"Run already exists: {self.run_id}")

        self._repository._prepare_run_directories(self.run_id)
        self._repository._atomic_write_model(path, run)
        return run

    def get_run(self) -> Run:
        return self._repository._read_model(self._run_path(), Run)

    def save_run(self, run: Run) -> Run:
        if _uuid_text(run.id) != self.run_id:
            raise RecordScopeError(
                f"Run id {run.id} does not match transaction {self.run_id}"
            )

        path = self._run_path()
        if not path.exists():
            raise RecordNotFound(f"Run not found: {self.run_id}")

        self._repository._atomic_write_model(path, run)
        return run

    def _create_child(
        self,
        collection: str,
        record: ModelT,
    ) -> ModelT:
        self._assert_scope(record)
        self.get_run()

        path = self._child_path(collection, record.id)
        if path.exists():
            raise RecordAlreadyExists(
                f"{record.__class__.__name__} already exists: {record.id}"
            )

        self._repository._atomic_write_model(path, record)
        return record

    def _get_child(
        self,
        collection: str,
        record_id: UUID | str,
        model_type: Type[ModelT],
    ) -> ModelT:
        self.get_run()
        path = self._child_path(collection, record_id)
        return self._repository._read_model(path, model_type)

    def _save_child(
        self,
        collection: str,
        record: ModelT,
    ) -> ModelT:
        self._assert_scope(record)
        self.get_run()

        path = self._child_path(collection, record.id)
        if not path.exists():
            raise RecordNotFound(
                f"{record.__class__.__name__} not found: {record.id}"
            )

        self._repository._atomic_write_model(path, record)
        return record

    def _list_children(
        self,
        collection: str,
        model_type: Type[ModelT],
    ) -> List[ModelT]:
        self.get_run()
        directory = self._repository._run_dir(self.run_id) / collection
        if not directory.exists():
            return []

        return [
            self._repository._read_model(path, model_type)
            for path in sorted(directory.glob("*.json"))
        ]

    def create_task(self, task: Task) -> Task:
        return self._create_child("tasks", task)

    def get_task(self, task_id: UUID | str) -> Task:
        return self._get_child("tasks", task_id, Task)

    def save_task(self, task: Task) -> Task:
        return self._save_child("tasks", task)

    def list_tasks(self) -> List[Task]:
        return self._list_children("tasks", Task)

    def create_message(self, message: AgentMessage) -> AgentMessage:
        return self._create_child("messages", message)

    def get_message(self, message_id: UUID | str) -> AgentMessage:
        return self._get_child("messages", message_id, AgentMessage)

    def list_messages(self) -> List[AgentMessage]:
        return self._list_children("messages", AgentMessage)

    def create_route_decision(
        self,
        decision: RouteDecision,
    ) -> RouteDecision:
        return self._create_child("route-decisions", decision)

    def get_route_decision(
        self,
        decision_id: UUID | str,
    ) -> RouteDecision:
        return self._get_child(
            "route-decisions",
            decision_id,
            RouteDecision,
        )

    def list_route_decisions(self) -> List[RouteDecision]:
        return self._list_children("route-decisions", RouteDecision)

    def create_approval(self, approval: Approval) -> Approval:
        return self._create_child("approvals", approval)

    def get_approval(self, approval_id: UUID | str) -> Approval:
        return self._get_child("approvals", approval_id, Approval)

    def save_approval(self, approval: Approval) -> Approval:
        return self._save_child("approvals", approval)

    def list_approvals(self) -> List[Approval]:
        return self._list_children("approvals", Approval)

    def create_artifact(self, artifact: Artifact) -> Artifact:
        return self._create_child("artifacts", artifact)

    def get_artifact(self, artifact_id: UUID | str) -> Artifact:
        return self._get_child("artifacts", artifact_id, Artifact)

    def list_artifacts(self) -> List[Artifact]:
        return self._list_children("artifacts", Artifact)

    def append_event(self, event: AuditEvent) -> AuditEvent:
        self._assert_scope(event)
        self.get_run()

        path = self._repository._run_dir(self.run_id) / "events.jsonl"
        self._repository._append_jsonl(path, event)
        return event

    def list_events(self, limit: int | None = None) -> List[AuditEvent]:
        self.get_run()
        path = self._repository._run_dir(self.run_id) / "events.jsonl"
        return self._repository._read_jsonl(path, AuditEvent, limit=limit)


class FileStateRepository:
    """Filesystem-backed repository rooted at ``data/runs`` by default."""

    _CHILD_DIRECTORIES = (
        "tasks",
        "messages",
        "route-decisions",
        "approvals",
        "artifacts",
    )

    def __init__(self, root: str | Path = "data/runs") -> None:
        self.root = Path(root)
        self._locks_dir = self.root / ".locks"
        self._ensure_directory(self.root)
        self._ensure_directory(self._locks_dir)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)

    def _run_dir(self, run_id: UUID | str) -> Path:
        return self.root / _uuid_text(run_id)

    def _prepare_run_directories(self, run_id: UUID | str) -> None:
        run_dir = self._run_dir(run_id)
        self._ensure_directory(run_dir)
        for name in self._CHILD_DIRECTORIES:
            self._ensure_directory(run_dir / name)

    @contextmanager
    def transaction(
        self,
        run_id: UUID | str,
    ) -> Iterator[RunTransaction]:
        canonical_id = _uuid_text(run_id)
        self._ensure_directory(self._locks_dir)
        lock_path = self._locks_dir / f"{canonical_id}.lock"

        with lock_path.open("a+", encoding="utf-8") as lock_handle:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield RunTransaction(self, canonical_id)
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def _atomic_write_model(self, path: Path, model: BaseModel) -> None:
        self._ensure_directory(path.parent)
        payload = model.model_dump_json(indent=2) + "\n"

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        temporary_path = Path(temporary_name)

        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(temporary_path, path)
            os.chmod(path, 0o600)
            self._fsync_directory(path.parent)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _read_model(path: Path, model_type: Type[ModelT]) -> ModelT:
        if not path.exists():
            raise RecordNotFound(f"Record not found: {path}")

        return model_type.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def _append_jsonl(self, path: Path, model: BaseModel) -> None:
        self._ensure_directory(path.parent)

        with path.open("a", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            handle.write(model.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        self._fsync_directory(path.parent)

    @staticmethod
    def _read_jsonl(
        path: Path,
        model_type: Type[ModelT],
        limit: int | None = None,
    ) -> List[ModelT]:
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")

        if not path.exists():
            return []

        lines = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        if limit is not None:
            if limit == 0:
                return []
            lines = lines[-limit:]

        return [
            model_type.model_validate_json(line)
            for line in lines
        ]

    def create_run(self, run: Run) -> Run:
        with self.transaction(run.id) as transaction:
            return transaction.create_run(run)

    def get_run(self, run_id: UUID | str) -> Run:
        with self.transaction(run_id) as transaction:
            return transaction.get_run()

    def save_run(self, run: Run) -> Run:
        with self.transaction(run.id) as transaction:
            return transaction.save_run(run)

    def create_task(self, task: Task) -> Task:
        with self.transaction(task.run_id) as transaction:
            return transaction.create_task(task)

    def get_task(
        self,
        run_id: UUID | str,
        task_id: UUID | str,
    ) -> Task:
        with self.transaction(run_id) as transaction:
            return transaction.get_task(task_id)

    def save_task(self, task: Task) -> Task:
        with self.transaction(task.run_id) as transaction:
            return transaction.save_task(task)

    def list_tasks(self, run_id: UUID | str) -> List[Task]:
        with self.transaction(run_id) as transaction:
            return transaction.list_tasks()

    def create_message(self, message: AgentMessage) -> AgentMessage:
        with self.transaction(message.run_id) as transaction:
            return transaction.create_message(message)

    def get_message(
        self,
        run_id: UUID | str,
        message_id: UUID | str,
    ) -> AgentMessage:
        with self.transaction(run_id) as transaction:
            return transaction.get_message(message_id)

    def list_messages(
        self,
        run_id: UUID | str,
    ) -> List[AgentMessage]:
        with self.transaction(run_id) as transaction:
            return transaction.list_messages()

    def create_route_decision(
        self,
        decision: RouteDecision,
    ) -> RouteDecision:
        with self.transaction(decision.run_id) as transaction:
            return transaction.create_route_decision(decision)

    def get_route_decision(
        self,
        run_id: UUID | str,
        decision_id: UUID | str,
    ) -> RouteDecision:
        with self.transaction(run_id) as transaction:
            return transaction.get_route_decision(decision_id)

    def list_route_decisions(
        self,
        run_id: UUID | str,
    ) -> List[RouteDecision]:
        with self.transaction(run_id) as transaction:
            return transaction.list_route_decisions()

    def create_approval(self, approval: Approval) -> Approval:
        with self.transaction(approval.run_id) as transaction:
            return transaction.create_approval(approval)

    def get_approval(
        self,
        run_id: UUID | str,
        approval_id: UUID | str,
    ) -> Approval:
        with self.transaction(run_id) as transaction:
            return transaction.get_approval(approval_id)

    def save_approval(self, approval: Approval) -> Approval:
        with self.transaction(approval.run_id) as transaction:
            return transaction.save_approval(approval)

    def list_approvals(
        self,
        run_id: UUID | str,
    ) -> List[Approval]:
        with self.transaction(run_id) as transaction:
            return transaction.list_approvals()

    def create_artifact(self, artifact: Artifact) -> Artifact:
        with self.transaction(artifact.run_id) as transaction:
            return transaction.create_artifact(artifact)

    def get_artifact(
        self,
        run_id: UUID | str,
        artifact_id: UUID | str,
    ) -> Artifact:
        with self.transaction(run_id) as transaction:
            return transaction.get_artifact(artifact_id)

    def list_artifacts(
        self,
        run_id: UUID | str,
    ) -> List[Artifact]:
        with self.transaction(run_id) as transaction:
            return transaction.list_artifacts()

    def append_event(self, event: AuditEvent) -> AuditEvent:
        with self.transaction(event.run_id) as transaction:
            return transaction.append_event(event)

    def list_events(
        self,
        run_id: UUID | str,
        limit: int | None = None,
    ) -> List[AuditEvent]:
        with self.transaction(run_id) as transaction:
            return transaction.list_events(limit=limit)
