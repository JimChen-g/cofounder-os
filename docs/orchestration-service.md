# CoFounder OS Orchestration Service

## Purpose

D04 introduces an application service that composes D02 domain records and D03
state primitives into decision-ready product workflows.

The service is not an HTTP route, provider client, model router, database,
queue, worker, or background process. API and agent layers call it to perform
coherent business operations.

## Package

```text
app/services/
├── __init__.py
└── orchestration.py
```

The public entry point is `OrchestrationService`.

## Responsibilities

The service supports:

- creating runs and tasks;
- linking tasks and artifacts to their parent run;
- enforcing task dependency readiness;
- starting, completing, failing, blocking, and cancelling work;
- recording agent messages without copying raw content into audit details;
- recording model-routing decisions after the router selects a provider;
- requesting and resolving run-level or task-level approvals;
- resuming approved work or failing rejected work;
- registering run, input, and output artifacts;
- returning a consistent `RunSnapshot` under one run lock.

## Layer Boundaries

```text
API / Agent / Future UI
        |
        v
OrchestrationService
        |
        +--> LifecycleStateMachine
        |
        +--> FileStateRepository
        |
        v
data/runs/<run_id>/
```

`app/router/selector.py` remains the runtime model selector. D04 does not call
the provider itself. Once routing has occurred, the caller records the
canonical decision through `record_route_decision`.

The existing Gateway request audit remains in `app/audit`. Product workflow
events remain in `data/runs/<run_id>/events.jsonl`.

## Approval Semantics

A task approval can be requested only when the task can transition to
`waiting_approval`. A run approval follows the corresponding run transition.

Resolution behavior is explicit:

```text
approved -> resume running
rejected -> fail
```

Approval records and lifecycle events share the same per-run lock. Expected
validation failures occur before writes. Filesystem records remain atomically
written per file; multi-record application commands use the final audit event
as their commit marker for future reconciliation.

## Dependency Semantics

`mark_task_ready` verifies every referenced dependency exists in the same run
and has reached `completed`. A blocked or unfinished dependency prevents the
transition and produces no status event.

## Run Completion Semantics

`complete_run` rejects completion while any task is pending, ready, running,
waiting for approval, blocked, or failed. Tasks must be completed or cancelled.

## Snapshot

`get_snapshot` reads the run, tasks, messages, route decisions, approvals,
artifacts, and audit events under one run lock:

```python
snapshot = service.get_snapshot(run_id)
```

An optional event limit can return only the newest workflow events.

## Example

```python
from app.services import OrchestrationService
from app.state import FileStateRepository

repository = FileStateRepository("data/runs")
service = OrchestrationService(repository)

run, _ = service.create_run(
    objective="Prepare a launch recommendation",
    actor="founder",
)

task, _ = service.create_task(
    run.id,
    title="Draft the recommendation",
    actor="orchestrator",
)

service.start_run(
    run.id,
    actor="orchestrator",
    reason="Execution started.",
)

service.mark_task_ready(
    run.id,
    task.id,
    actor="orchestrator",
    reason="Dependencies are complete.",
)
```

## Infrastructure Contract

D04 introduces no changes to:

- Qwen or vLLM parameters;
- Gateway routing behavior;
- systemd or launchd;
- SSH tunnel or ports;
- model files;
- database or message queue;
- API endpoints.
