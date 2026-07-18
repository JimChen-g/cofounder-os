# CoFounder OS Executive Orchestrator

## Scope

D05 introduces the first bounded multi-agent planning layer:

```text
User Objective
      |
      v
ExecutiveOrchestrator
      |
      +--> GatewayClient
      |      |
      |      v
      |  cofounder-auto
      |
      +--> AgentRegistry
      |
      +--> OrchestrationService
             |
             v
      data/runs/<run_id>/
```

D05 is not an orchestration HTTP API. It is internal product logic that plans
and persists a controlled multi-agent workflow.

## Components

### AgentRegistry

The registry is explicit and immutable at runtime. The initial registered
agents are:

- `executive-orchestrator`
- `research-agent`
- `product-agent`
- `finance-agent`
- `legal-agent`
- `operations-agent`

The planning model receives this exact catalog. Any unregistered assignment is
rejected after generation. D05 does not support dynamic agent creation.

### GatewayClient

`GatewayClient` calls the public OpenAI-compatible Gateway endpoint:

```text
POST /v1/chat/completions
```

This is distinct from `app/providers/openai_compat.py`, which is an internal
Gateway provider adapter for Qwen and Step.

Runtime configuration is explicit:

```text
Mac:       COFOUNDER_GATEWAY_URL=http://127.0.0.1:19000
DGX Spark: COFOUNDER_GATEWAY_URL=http://127.0.0.1:9000
```

The implementation does not change either port or the SSH tunnel.

### ExecutivePlan

The model output must contain:

- one objective;
- one concise summary;
- 3 to 6 tasks;
- a registered agent for every task;
- dependency keys;
- one concrete deliverable for every task;
- task-level approval hints;
- a plan-level approval decision.

The validator rejects:

- fewer than 3 or more than 6 tasks;
- duplicate task keys;
- self-dependencies;
- unknown dependencies;
- cyclic dependencies;
- unregistered agents;
- unknown fields.

Nested task lists are not part of the schema, so recursive planning is not
accepted.

### ExecutiveOrchestrator

The orchestrator performs one planning request, validates the result, and then
materializes the plan through `OrchestrationService`.

Materialization records:

- the Run;
- the complete structured plan as an `AgentMessage`;
- every Task;
- dependency UUIDs;
- assigned agents;
- deliverables and approval hints;
- the Gateway `RouteDecision`;
- all lifecycle and creation audit events.

## Approval Behavior

When `approval_required=true`:

```text
run created
-> run running
-> run waiting_approval
```

Tasks remain pending. After the founder approves the Run, the caller invokes
`activate_ready_tasks`, which moves dependency-free tasks to `ready`.

When `approval_required=false`, root tasks are activated immediately.

## Execution Boundary

D05 plans and materializes work. It does not yet execute specialist agents.
Later stages may claim `ready` tasks and invoke the assigned registered agent.

## Safety and Architecture Contract

D05 guarantees:

- exactly one Executive Orchestrator;
- one bounded plan call;
- 3 to 6 tasks;
- no dynamic agents;
- no recursive planning;
- no cyclic dependencies;
- strict Pydantic validation;
- auditable routing and plan persistence;
- no database or queue;
- no API route changes;
- no service, model, port, systemd, or launchd changes.
