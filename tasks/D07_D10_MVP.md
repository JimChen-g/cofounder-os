# D07-D10: Complete Founder Decision Workflow

## Scope

This stage extends the accepted D06 foundations without changing the frozen
Gateway, model, port, deployment, or persistence architecture.

The minimum runnable workflow is:

```text
Founder objective
    -> Product Agent + Finance Agent
    -> Deterministic Policy Gate
    -> Artifact Synthesizer
    -> Workflow Controller completion or terminal failure
```

## D07 Finance Agent

The Finance Agent reuses the D06 Gateway, validation, Artifact Store, routing
evidence, and execution contracts. Its versioned result contains:

- revenue assumptions;
- cost structure;
- unit economics;
- downside/base/upside budget scenarios;
- financial risks;
- decision thresholds.

Model output is untrusted, Pydantic-validated, bounded, and repaired at most
once. Accepted output is stored as `finance-brief.json` and
`finance-brief.md`.

## D08 Deterministic Policy Gate

The Policy Gate makes no model calls. It assigns:

- risk level;
- `read_only`, `guarded`, or `blocked` tool permission;
- `allow`, `require_approval`, or `deny` disposition;
- explicit rule identifiers and reasons.

Dangerous commands and private-data uploads are denied. External writes,
production changes, private-data access, external delivery, irreversible
operations, material budget commitments, and otherwise-unblocked command
execution require approval. Workflow approvals expire after one hour and are
bound to the exact task claim, normalized action digest, matched rule set, and
required reviewer.

## D09 Artifact Synthesizer

The Synthesizer accepts only validated Product and Finance results and writes
exactly five deterministic Markdown task outputs through the D06 Artifact
Store:

1. Executive Decision Memo
2. PRD / Product Brief
3. Budget Summary
4. Risk Register
5. Action Plan

Replaying identical inputs reuses the accepted artifact records. Changed
content cannot overwrite an accepted path silently.

## D10 Workflow Controller

The Workflow Controller is the single application-level coordinator for:

- bounded task-loop execution;
- dependency activation;
- implemented-agent enforcement;
- deterministic policy evaluation;
- formal approval pause and resume;
- retry limits and terminal failure;
- artifact-backed reconciliation;
- completed-run replay without model re-execution.

The controller composes the accepted D03-D06 authorities. Agents still do not
mutate Run, Task, Approval, retry, or dependency state.

## Acceptance

- D07-D10 targeted tests pass.
- The complete Product + Finance + Synthesis workflow completes.
- The workflow produces nine registered outputs: two Product, two Finance,
  and five synthesis artifacts.
- A completed run replays without additional Gateway calls or artifacts.
- A completed run with missing or corrupt output refuses a successful replay.
- An interrupted RUNNING task with valid outputs reconciles without another
  Gateway call.
- Failed attempts retry only within `max_attempts`.
- Exhausted attempts fail both the Task and Run.
- Approval-required work pauses before execution and resumes only after an
  unexpired, action-bound decision by the policy-selected reviewer.
- Missing, corrupt, wrongly related, or unrelated declared input artifacts
  fail before a Gateway call.
- Dangerous actions and unimplemented agents never execute.
- The pre-D07 full suite remains green.
- No infrastructure, credential, `.env`, provider, port, systemd, launchd, or
  DGX files change.
