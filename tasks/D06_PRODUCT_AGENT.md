# D06: Specialist Agent Execution Layer

## Scope

D06 introduces the first bounded specialist agent that can execute a planned
task. It builds on D02 (domain models), D03 (state repository and lifecycle
machine), D04 (orchestration service), and D05 (Executive Orchestrator).

D06 is decomposed into four independent sub-tasks:

- **D06-A**: execution foundation (ProductAgent with Gateway protocol, result
  model, and error handling)
- **D06-B**: Finance Agent specialization
- **D06-C**: Legal Agent specialization
- **D06-D**: Research and Operations agents plus execution service wiring

Only D06-A is in scope for this session.

## Architecture

```text
Task (ready)
    |
    v
ProductAgent.execute(task, context)
    |
    +--> GatewayClient.complete(messages)
    |       |
    |       v
    |   cofounder-auto / cofounder-qwen / cofounder-step
    |
    +--> OrchestrationService.record_route_decision(...)
    +--> OrchestrationService.append_message(...)
    +--> OrchestrationService.complete_task(...)
    |
    v
ProductAgentExecutionResult
```

## D06-A Contract

### New module

```text
app/agents/product.py
```

### Public surface

```python
from app.agents import ProductAgent, ProductAgentError
```

### Gateway protocol

`ProductAgent` accepts any object satisfying the `GatewayExecutionProtocol`:

```python
class GatewayExecutionProtocol(Protocol):
    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str = "cofounder-auto",
        temperature: float = 0.1,
        max_tokens: int = 1800,
    ) -> GatewayCompletion: ...
```

This matches the `GatewayPlanningProtocol` used by `ExecutiveOrchestrator`,
ensuring both planners and executors share the same Gateway boundary.

### Execution result

```python
class ProductAgentExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    task_id: UUID
    completion: GatewayCompletion
    route_decision: RouteDecision
    message: AgentMessage
    transition_event: AuditEvent
    final_task: Task
```

### Execute method

```python
async def execute(
    self,
    run_id: UUID | str,
    task_id: UUID | str,
    *,
    context: str | None = None,
    actor: str | None = None,
    correlation_id: str | None = None,
) -> ProductAgentExecutionResult:
```

Behavior:

1. Validates that `run_id` and `task_id` are non-empty.
2. Reads the task through the orchestration service snapshot.
3. Asserts the task is in `running` status.
4. Asserts `task.assigned_agent` matches the agent's `agent_id`.
5. Builds a `ChatMessage` list:
   - system prompt describing the agent's role;
   - the task title and description as a user message;
   - optional `context` appended to the user message.
6. Calls `gateway.complete(messages)`.
7. Records a `RouteDecision` through `service.record_route_decision`.
8. Appends an `AgentMessage` through `service.append_message`.
9. Transitions the task to `completed` through `service.complete_task`.
10. Returns `ProductAgentExecutionResult`.

### Error model

```python
class ProductAgentError(RuntimeError):
    """Base error for Product Agent execution."""

class TaskNotReadyError(ProductAgentError):
    """Raised when the task is not in running status."""

class AgentMismatchError(ProductAgentError):
    """Raised when the task is not assigned to this agent."""
```

### System prompt

The Product Agent system prompt is fixed and describes the agent's analytical
role. It does not instruct the model to emit structured output — the Product
Agent returns natural-language analysis that downstream stages consume.

## D06-A Acceptance Criteria

- `ProductAgent` is importable from `app.agents`.
- `execute` produces a `ProductAgentExecutionResult` with all five fields
  populated.
- A `RouteDecision` is persisted and links to the run and task.
- An `AgentMessage` is persisted and links to the run and task.
- The task transitions from `running` to `completed`.
- `TaskNotReadyError` is raised when the task is not `running`.
- `AgentMismatchError` is raised when `task.assigned_agent` does not match.
- `ProductAgentError` is raised when the Gateway returns empty content.
- All new code passes targeted tests, full test suite, and Ruff linting.
- No infrastructure files (services, ports, systemd, launchd, credentials,
  `.env`) are modified.

## D06-B through D06-D Preview

These sub-tasks are deferred. Rough scope:

- **D06-B**: FinanceAgent with financial-analysis system prompt and
  `FinancialAnalysisResult` model.
- **D06-C**: LegalAgent with legal-review system prompt and
  `LegalAnalysisResult` model.
- **D06-D**: ResearchAgent, OperationsAgent, and `AgentExecutionService` that
  selects the correct agent by `task.assigned_agent` and invokes `execute`.
