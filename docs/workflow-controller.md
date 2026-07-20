# D07-D10 Workflow Controller

## Boundary

This implementation remains inside the Product Platform layer:

```text
WorkflowController
    ├── AgentExecutionService
    ├── OrchestrationService
    ├── DeterministicPolicyGate
    ├── ProductAgentService
    ├── FinanceAgentService
    ├── ArtifactSynthesizer
    └── FileArtifactStore
```

Every model call still passes through the existing Gateway virtual models.
The Product and Finance agents return validated results and artifacts; they do
not own authoritative lifecycle state.

## Execution Loop

For each bounded controller cycle:

1. Reconcile persisted `running` tasks.
2. Prepare eligible `blocked` tasks for retry, or fail exhausted tasks.
3. Fail the Run if a Task is terminally failed.
4. Activate `pending` tasks whose dependencies are complete.
5. Evaluate each `ready` task with the deterministic Policy Gate.
6. Deny, request a time-bounded action-bound approval, or claim and execute
   the task.
7. Complete the Run when every Task is completed or cancelled.
8. Stop safely on approval wait, dependency stall, or cycle exhaustion.

## Recovery and Idempotency

A `running` task is recovered from durable claim and artifact evidence:

- complete verified output bundle: complete the existing claim without
  another model call;
- approved, claimed task with no output yet: re-evaluate policy and resume
  only when the exact approval ID, action digest, rule set, reviewer, claim,
  and expiry remain valid;
- missing or incomplete output: record an attempt failure, then retry only if
  the configured budget remains.

Product, Finance, and synthesis artifacts use content-bound idempotency keys.
A completed Run returns a read-only replay result with zero controller cycles
only after every completed Task output is re-verified against its registered
checksum, size, URI, relation, and required bundle.

Product and Finance contexts include only artifacts explicitly listed in
`Task.input_artifact_ids`; every input must pass the D06 run scope, task
lineage, relation, store address, checksum, size, and URI checks.

## Implemented Task Adapters

- `product-agent`
- `finance-agent`
- `executive-orchestrator` only when `metadata.task_type` is
  `artifact_synthesis`

The registry and the controller both reject placeholder agents. This preserves
the D06 implemented-agent enforcement contract.

## Infrastructure

D07-D10 do not modify:

- model selection identifiers;
- Gateway routing;
- vLLM;
- ports;
- SSH tunnels;
- systemd or launchd;
- deployment scripts;
- credentials or `.env` files;
- the JSONL/filesystem persistence architecture.
