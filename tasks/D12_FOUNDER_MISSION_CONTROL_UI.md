# D12: Founder Mission Control UI

## Scope

D12 adds the Hackathon demonstration UI on top of the accepted D11 Product
API. It is a presentation-layer change only: workflow, task, approval, retry,
artifact, policy, routing, and audit state remain owned by the existing D06–D11
authorities.

The UI is served by the existing FastAPI process at `/ui` and uses same-origin
requests to the stable `/api` contract. It adds no frontend runtime, database,
queue, provider path, model, port, or deployment service.

## Minimum UI

- Founder mission, context, and owner input with bounded submission state
- Run status, task dependencies, agent activity, route evidence, and attempts
- Pending approval review with approve and reject actions
- Policy and risk evidence sourced from persisted task and approval metadata
- Integrity-checked artifact list, viewer, and browser download
- Audit timeline sourced from the append-only event endpoint
- Failed, stalled, retry, replay, recovery, empty, loading, and error states
- Desktop-first layout with a responsive narrow-screen presentation

## API Boundary

The UI may call only:

```text
GET  /api/health
POST /api/runs
GET  /api/runs/{run_id}
GET  /api/runs/{run_id}/events
GET  /api/runs/{run_id}/artifacts
POST /api/runs/{run_id}/approvals/{approval_id}
POST /api/runs/{run_id}/retry
```

Artifact downloads are created in the browser from content already returned by
the D11 integrity-checking endpoint. The UI does not read runtime files.

## Acceptance

- `/ui` and its same-origin assets are served by the existing FastAPI app.
- The root system response advertises the UI without changing existing fields.
- UI actions use only the accepted D11 Product API and never call a provider.
- A completed three-agent run renders nine artifacts and its audit trace.
- A pending approval can be approved or rejected with the policy reviewer.
- Failed or stalled workflows expose bounded retry/recovery controls.
- Empty, loading, API-error, and narrow-screen states remain usable.
- UI contract tests, the full suite, Ruff, mypy, JavaScript syntax check, and
  package build pass.
- No secret, runtime state, external asset, or generated data enters Git.
