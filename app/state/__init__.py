"""Public state-management surface for CoFounder OS."""

from app.state.machine import (
    InvalidTransition,
    LifecycleStateMachine,
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
)
from app.state.repository import (
    FileStateRepository,
    RecordAlreadyExists,
    RecordNotFound,
    RecordScopeError,
    RunTransaction,
    StateRepositoryError,
)

__all__ = [
    "FileStateRepository",
    "InvalidTransition",
    "LifecycleStateMachine",
    "RecordAlreadyExists",
    "RecordNotFound",
    "RecordScopeError",
    "RUN_TRANSITIONS",
    "RunTransaction",
    "StateRepositoryError",
    "TASK_TRANSITIONS",
]
