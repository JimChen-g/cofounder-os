# D11: Product API

## Scope

D11 exposes the accepted CoFounder OS workflow through FastAPI without
changing the Gateway, provider, model, port, persistence, or deployment
architecture.

The Product API remains a presentation-layer adapter. It does not mutate
durable Run, Task, Approval, retry, dependency, or artifact state directly.
All writes continue through the Executive Orchestrator, Orchestration Service,
Workflow Controller, and Artifact Store.

## Minimum API

```text
POST /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/events
GET  /api/runs/{run_id}/artifacts
POST /api/runs/{run_id}/approvals/{approval_id}
POST /api/runs/{run_id}/retry
GET  /api/health
```

## Runtime Contract

- `POST /api/runs` performs one bounded Executive planning call, persists the
  accepted three-task MVP plan, and invokes the D10 Workflow Controller.
- The MVP plan contains exactly one Product task, one Finance task, and one
  Executive synthesis task that depends directly on both.
- A plan-level or policy-level approval pauses execution and is returned to
  the caller as persisted state.
- Approval decisions are one-shot, expiry-aware, and enforce the
  policy-selected reviewer when present.
- `retry` delegates to the D10 bounded recovery/retry/replay controller. It
  cannot reset attempt counts or reopen terminal state.
- Artifact retrieval verifies content checksums before returning UTF-8 text.
  Binary or oversized content is represented by metadata only.
- Errors use a stable envelope and do not expose filesystem paths, provider
  payloads, credentials, or raw exception text.

## Acceptance

- The seven minimum endpoints appear in OpenAPI.
- A real API request completes Product + Finance + Synthesis with nine
  registered artifacts.
- A required approval pauses before agent execution and resumes only after a
  valid decision.
- Rejection terminally fails without agent execution.
- Completed-run retry is an idempotent replay with no additional model calls.
- Events are bounded, artifacts are integrity checked, and missing records
  return a stable 404.
- D11 targeted tests, the full suite, Ruff, mypy, governance, and secret scan
  pass.
- No direct Qwen or Step access, database, queue, new framework, UI, or
  infrastructure change is introduced.
