# D06: Specialist Agent Execution Layer

## Scope

D06 introduces the bounded specialist agent execution contract. It builds on
D02 (domain models), D03 (state repository and lifecycle machine), D04
(orchestration service), and D05 (Executive Orchestrator).

D06 is decomposed into four independent sub-tasks:

- **D06-A**: execution foundation (AgentExecutionService with claim, attempt,
  retry, and audit contract)
- **D06-B**: Product Agent specialization
- **D06-C**: Finance Agent specialization
- **D06-D**: Research and Operations agents plus execution service wiring

Only D06-A is in scope for this stage.

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
    +--> record_attempt_failure(error)       --> Task (failed, retryable or terminal)
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

- `executive-orchestrator` (can_execute=True, can_plan=True)
- `product-agent` (can_execute=True, can_plan=False)
- `finance-agent` (can_execute=True, can_plan=False)

Research, legal, and operations agents remain `can_execute=False` placeholders.

### Task Model Changes

Extend `app/domain/models.py` Task with backward-compatible defaults:

```python
attempt_count: int = 0
max_attempts: int = 2
last_error: Optional[str] = None
claimed_by: Optional[str] = None
claim_token: Optional[str] = None
claimed_at: Optional[datetime] = None
```

Existing D02-D05 JSON files without these fields load correctly because
Pydantic applies the defaults.

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
2. Reads task under transaction lock.
3. Rejects if task is not READY.
4. Rejects if `assigned_agent` does not match `agent_id`.
5. If task already has a claim:
   - If `claim_token` matches: idempotent return.
   - If `claim_token` is None or mismatches: reject.
6. If task is unclaimed:
   - Generate or accept `claim_token`.
   - Set `claimed_by`, `claimed_at`, `claim_token`.
   - Transition task to RUNNING.
   - Increment `attempt_count` by 1.
   - Append `task.claimed` audit event.
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
2. Rejects if task is not RUNNING.
3. Rejects if `claim_token` does not match.
4. Transitions task to COMPLETED.
5. Clears `claim_token`, `claimed_by`, `claimed_at`.
6. Appends `task.completed` audit event.
7. Returns (completed task, audit event).

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
2. Rejects if task is not RUNNING.
3. Rejects if `claim_token` does not match.
4. If `attempt_count >= max_attempts`:
   - Transitions task to FAILED (terminal).
   - Sets `last_error`.
   - Clears claim fields.
   - Appends `task.failed` audit event with `terminal: true`.
   - Returns `terminal_failure=True, retry_available=False`.
5. Otherwise:
   - Sets `last_error`.
   - Clears claim fields (allows re-claim).
   - Keeps task in current status (or transitions to FAILED non-terminal).
   - Appends `task.attempt_failed` audit event with `terminal: false`.
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
2. Rejects if task is not FAILED.
3. Transitions task to READY.
4. Clears `last_error`.
5. Appends `task.retry_prepared` audit event.
6. Returns RetryPreparationResult.

## D06-A Acceptance Criteria

- Task model accepts legacy JSON without new fields.
- `claim_task` atomically claims a READY task.
- Same-token re-claim is idempotent.
- Competing claim is rejected without side effects.
- Wrong agent assignment is rejected.
- `complete_claimed_task` requires matching token.
- Completion clears claim fields.
- First failure sets `last_error` and allows retry.
- Second failure (at `max_attempts`) produces terminal FAILED.
- `prepare_retry` moves FAILED task back to READY.
- Retry after exhaustion raises `AttemptLimitExceededError`.
- Executive prompt exposes only executable agents (3 of 6).
- All new code passes targeted tests, full suite, and Ruff.
- No infrastructure files modified.

## D06-B through D06-D Preview

These sub-tasks are deferred. Rough scope:

- **D06-B**: ProductAgent with product-analysis system prompt.
- **D06-C**: FinanceAgent with financial-analysis system prompt.
- **D06-D**: ResearchAgent, OperationsAgent placeholders, and agent
  selection wiring.
