# CoFounder OS State Management

## Scope

D03 adds a local, filesystem-backed state repository and explicit lifecycle
state machines. It does not add a database, message queue, background service,
model, port, or infrastructure dependency.

The existing Gateway request audit logger remains unchanged. D03 introduces a
separate product-state ledger scoped to each run.

## Storage Layout

```text
data/runs/
├── .locks/
│   └── <run_id>.lock
└── <run_id>/
    ├── run.json
    ├── events.jsonl
    ├── tasks/
    │   └── <task_id>.json
    ├── messages/
    │   └── <message_id>.json
    ├── route-decisions/
    │   └── <route_decision_id>.json
    ├── approvals/
    │   └── <approval_id>.json
    └── artifacts/
        └── <artifact_id>.json
```

`data/runs/` remains ignored by Git and is excluded from deployment. Product
runtime state is therefore not overwritten by Mac-to-DGX code deployment.

## Reliability Properties

- One advisory `fcntl` lock serializes operations for each run.
- JSON state files are written to a temporary file, flushed, and atomically
  promoted with `os.replace`.
- State files and directories use restrictive local permissions.
- Audit events are appended to `events.jsonl` and flushed with `fsync`.
- Child records are rejected when their `run_id` does not match the selected
  run.
- Duplicate creation and missing records raise explicit repository errors.
- Invalid lifecycle transitions produce no state mutation and no audit event.

## Run Lifecycle

```text
queued
├── running
└── cancelled

running
├── waiting_approval
├── completed
├── failed
└── cancelled

waiting_approval
├── running
├── failed
└── cancelled
```

`completed`, `failed`, and `cancelled` are terminal.

## Task Lifecycle

```text
pending
├── ready
├── blocked
└── cancelled

ready
├── running
├── blocked
└── cancelled

running
├── waiting_approval
├── blocked
├── completed
├── failed
└── cancelled

waiting_approval
├── running
├── blocked
├── failed
└── cancelled

blocked
├── ready
├── failed
└── cancelled
```

`completed`, `failed`, and `cancelled` are terminal.

## Transition Audit Contract

Each successful transition appends one canonical `AuditEvent` containing:

- previous status;
- next status;
- actor;
- reason;
- target type and identifier;
- optional correlation identifier;
- UTC timestamp.

Invalid transitions do not append failure events because they do not represent
accepted product state. Callers may separately record rejected user actions in
the Gateway request audit when needed.

## Usage

```python
from app.domain import Run, RunStatus, Task
from app.state import FileStateRepository, LifecycleStateMachine

repository = FileStateRepository("data/runs")
machine = LifecycleStateMachine(repository)

run = Run(objective="Prepare a launch decision")
repository.create_run(run)

updated_run, event = machine.transition_run(
    run.id,
    RunStatus.RUNNING,
    actor="orchestrator",
    reason="Execution started.",
)
```
