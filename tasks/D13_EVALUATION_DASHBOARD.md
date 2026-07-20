# D13 — Evaluation Dashboard

Status: IMPLEMENTED — PENDING INDEPENDENT REVIEW

## Objective

Add a deterministic, read-only evaluation dashboard to the accepted D06–D12
product without changing workflow authority, provider routing, persistence
architecture, or the Product API contract.

## Frozen boundaries

- Existing Run, Task, RouteDecision, Approval, Artifact, AuditEvent, Artifact
  Store, and Workflow Controller records remain authoritative.
- Evaluation performs no model call, workflow transition, approval decision,
  artifact write, or audit append.
- No new database, queue, service, port, provider, or frontend runtime.
- Existing `/api` Product API and `/ui` deployment remain the only product
  service and UI entry points.
- Artifact integrity is verified against Artifact Store evidence; failure lowers
  the score and is exposed as bounded evidence instead of mutating state.

## MVP scope

1. `GET /api/evaluation/summary?limit=50`
   - bounded, newest-first Run set;
   - completion, score, task, retry, artifact integrity, provider, grade, and
     agent metrics;
   - recent per-Run evaluations.
2. `GET /api/evaluation/runs/{run_id}`
   - weighted dimensions for workflow outcome, execution reliability, artifact
     evidence, governance, and auditability;
   - human-readable evidence for every score;
   - no prompt or model dependency.
3. Founder Mission Control `Evaluation` view
   - KPI summary;
   - latest/recent Run scores and dimension evidence;
   - agent reliability and provider distribution;
   - one-click navigation from an evaluated Run to the existing Mission view;
   - desktop-first with a usable narrow-screen layout.
4. D12 presentation corrective work
   - stale Run responses cannot overwrite a newer/New Mission state;
   - terminal failed retry messages are not mislabeled as replay success;
   - Refresh and New mission remain available on narrow screens.

## Deterministic score contract

| Dimension | Weight | Evidence |
| --- | ---: | --- |
| Workflow outcome | 25% | persisted Run terminal/lifecycle status |
| Execution reliability | 25% | task completion and first-pass reliability |
| Artifact evidence | 25% | nine required D11 outputs plus store integrity |
| Governance | 15% | approval resolution state |
| Auditability | 10% | expected lifecycle events and route evidence |

Grades: `excellent >= 85`, `good >= 70`, `attention >= 50`, otherwise
`critical`.

## Acceptance gates

- Evaluation results are repeatable for an unchanged persisted snapshot.
- Corrupt, missing, or inconsistent artifact evidence reduces the artifact
  score without exposing filesystem paths.
- Unknown Run returns the existing bounded 404 envelope.
- Summary limit is validated and cross-Run discovery ignores non-canonical and
  symlinked directories.
- Existing Product API, workflow, provider, policy, approval, artifact, and
  audit tests remain green.
- Evaluation service/API tests, repository discovery tests, UI contract tests,
  lint, typecheck, build, and a real local browser/API flow pass.
- Independent review is required before D13 is marked accepted or published.
