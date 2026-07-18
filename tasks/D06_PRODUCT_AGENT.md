# D06: Specialist Agent Execution Layer

## Scope

D06 introduces the bounded specialist agent execution contract. It builds on
D02 (domain models), D03 (state repository and lifecycle machine), D04
(orchestration service), and D05 (Executive Orchestrator).

D06 is decomposed into four independent sub-tasks:

- **D06-A**: Agent execution contract (AgentExecutionService with claim,
  attempt, retry, and audit contract)
- **D06-B**: Filesystem Artifact Store
- **D06-C**: Product Agent specialization
- **D06-D**: Product lifecycle integration

## Architecture

```text
Task (ready)
    |
    v
AgentExecutionService.claim_task(agent_id, claim_token)
    |
    +--> Task (running, claimed)
    |
    +--> GatewayClient.complete(messages)
    |       |
    |       v
    |   cofounder-auto / cofounder-qwen / cofounder-step
    |
    +--> complete_claimed_task(claim_token)  --> Task (completed)
    +--> record_attempt_failure(error)       --> Task (blocked or failed)
    +--> prepare_retry()                     --> Task (ready)
```

## D06-A Contract

### New module

```text
app/services/execution.py
```

### Public surface

```python
from app.services import (
    AgentExecutionService,
    TaskClaim,
    AttemptFailureResult,
    RetryPreparationResult,
    TaskNotReadyError,
    AgentNotExecutableError,
    TaskAlreadyClaimedError,
    ClaimTokenMismatchError,
    AttemptLimitExceededError,
    TaskTerminallyFailedError,
)
```

### Agent Registry Changes

Update `app/agents/registry.py` to mark only three agents as executable:

- `executive-orchestrator` (can_executable=True, can_plan=True)
- `product-agent` (can_execute=True, can_plan=False)
- `finance-agent` (can_execute=True, can_plan=False)

Research, legal, and operations agents remain `can_execute=False` placeholders.

### Task Model Changes

Extend `app/domain/models.py` Task with backward-compatible defaults:

```python
attempt_count: int = Field(default=0, ge=0)
max_attempts: int = Field(default=2, ge=1)
last_error: Optional[str] = None
claimed_by: Optional[str] = None
claim_token: Optional[str] = None
claimed_at: Optional[datetime] = None
```

Existing D02-D05 JSON files without these fields load correctly because
Pydantic applies the defaults.

### Lifecycle Alignment

D06-A aligns with the existing LifecycleStateMachine:

- **claim_task**: READY -> RUNNING (via state machine)
- **record_attempt_failure** (first failure): RUNNING -> BLOCKED (via state machine)
- **prepare_retry**: BLOCKED -> READY (via state machine)
- **record_attempt_failure** (exhausted): RUNNING -> FAILED (via state machine, terminal)
- **complete_claimed_task**: RUNNING -> COMPLETED (via state machine)
- FAILED is terminal and cannot return to READY

### claim_task

```python
def claim_task(
    self,
    run_id: UUID | str,
    task_id: UUID | str,
    *,
    agent_id: str,
    claim_token: str | None = None,
    correlation_id: str | None = None,
) -> TaskClaim:
```

Behavior:
1. Validates agent is executable via registry.
2. Attempt-budget guard: READY task with `attempt_count >= max_attempts` raises `AttemptLimitExceededError`.
3. Reads task under transaction lock.
4. Rejects if task is not READY.
5. Rejects if `assigned_agent` does not match `agent_id`.
6. If task already has a claim:
   - Idempotent re-claim requires same `agent_id` AND same `claim_token`.
   - Another executable agent with the same token is rejected.
   - If `claim_token` matches and `agent_id` matches: idempotent return.
   - Otherwise: reject with `ClaimTokenMismatchError`.
7. If task is unclaimed:
   - Generate or accept `claim_token`.
   - Set `claimed_by`, `claimed_at`, `claim_token`.
   - Transition task to RUNNING via state machine.
   - Increment `attempt_count` by 1.
   - Append `task.claimed` audit event with `outcome=SUCCESS`.
   - Return `TaskClaim`.

### complete_claimed_task

```python
def complete_claimed_task(
    self,
    run_id: UUID | str,
    task_id: UUID | str,
    *,
    claim_token: str,
    actor: str,
    correlation_id: str | None = None,
) -> tuple[Task, AuditEvent]:
```

Behavior:
1. Reads task under transaction lock.
2. Verifies ownership: `agent_id == claimed_by` AND `agent_id == assigned_agent`.
3. Rejects if `claim_token` does not match.
4. Rejects if task is not RUNNING.
5. Transitions task to COMPLETED via state machine.
6. Clears `claim_token`, `claimed_by`, `claimed_at`.
7. Appends `task.completed` audit event with `outcome=SUCCESS`.
8. Returns (completed task, audit event).

### record_attempt_failure

```python
def record_attempt_failure(
    self,
    run_id: UUID | str,
    task_id: UUID | str,
    *,
    claim_token: str,
    error: str,
    actor: str,
    correlation_id: str | None = None,
) -> AttemptFailureResult:
```

Behavior:
1. Reads task under transaction lock.
2. Verifies ownership: `agent_id == claimed_by` AND `agent_id == assigned_agent`.
3. Rejects if `claim_token` does not match.
4. Rejects if task is not RUNNING.
5. If `attempt_count >= max_attempts`:
   - Transitions task to FAILED via state machine (terminal).
   - Sets `last_error`.
   - Clears claim fields.
   - Appends `task.failed` audit event with `outcome=FAILURE` and `terminal: True`.
   - Returns `terminal_failure=True, retry_available=False`.
6. Otherwise (first failure):
   - Transitions task to BLOCKED via state machine.
   - Sets `last_error`.
   - Clears claim fields.
   - Appends `task.attempt_failed` audit event with `outcome=FAILURE` and `terminal: False`.
   - Returns `retry_available=True, terminal_failure=False`.

### prepare_retry

```python
def prepare_retry(
    self,
    run_id: UUID | str,
    task_id: UUID | str,
    *,
    actor: str,
    correlation_id: str | None = None,
) -> RetryPreparationResult:
```

Behavior:
1. Reads task under transaction lock.
2. Checks attempt budget first: if `attempt_count >= max_attempts`, raises `AttemptLimitExceededError`.
3. Rejects if task is not BLOCKED.
4. Transitions task to READY via state machine.
5. Clears `last_error`.
6. Appends `task.retry_prepared` audit event with `outcome=SUCCESS`.
7. Returns RetryPreparationResult.

## D06-A Acceptance Criteria

- Task model accepts legacy JSON without new fields.
- `attempt_count >= 0` and `max_attempts >= 1` enforced.
- `claim_task` atomically claims a READY task.
- Same agent and token re-claim is idempotent.
- Different agent with same token is rejected.
- Competing claim without token is rejected.
- Wrong agent assignment is rejected.
- Non-executable agent is rejected.
- READY task with exhausted attempt budget cannot be claimed.
- `complete_claimed_task` requires matching token and agent.
- Completion clears claim fields.
- First failure transitions to BLOCKED.
- `prepare_retry` accepts only BLOCKED.
- FAILED is terminal and cannot retry.
- Second failure (at max_attempts) produces terminal FAILED.
- Failure audit outcome is FAILURE.
- Terminal failure audit outcome is FAILURE.
- All lifecycle transitions use LifecycleStateMachine.
- Executive prompt exposes only 3 executable agents.
- All new code passes targeted tests, full suite, and Ruff.
- No infrastructure files modified.

## D06-B: Filesystem Artifact Store

D06-B introduces a filesystem-backed artifact store for task inputs and outputs.
Artifacts are stored under `data/runs/<run_id>/artifacts/` with metadata in
JSON files and content in files.

## D06-C: Product Agent

D06-C introduces the Product Agent specialization with:
- Product-analysis system prompt
- `ProductAgent` class that uses `AgentExecutionService`
- Integration with Gateway for execution
- Product result schemas

## D06-D: Product Lifecycle Integration

D06-D wires the Product Agent into the orchestration workflow:
- Automatic task claiming and execution
- Artifact registration after completion
- Integration with Executive Orchestrator's `activate_ready_tasks`

## D07: Finance Agent

D07 introduces the Finance Agent specialization with:
- Financial-analysis system prompt
- `FinanceAgent` class
- Financial result schemas
