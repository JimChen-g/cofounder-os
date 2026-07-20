# Evaluation Dashboard

D13 adds deterministic evaluation to the existing CoFounder OS Product API and
Founder Mission Control. It is a read-only presentation layer over accepted
workflow evidence; it does not call a provider or change authoritative state.

## Start and open

Use the existing runtime:

```bash
source .venv/bin/activate
bash scripts/run_gateway.sh
```

Open:

```text
http://127.0.0.1:9000/ui
```

Select **Evaluation** in the workspace navigation. No separate frontend process
or evaluation service is required.

## API

```text
GET /api/evaluation/summary?limit=50
GET /api/evaluation/runs/{run_id}
```

`limit` is required to be between 1 and 200. Summary results are ordered by
persisted `Run.updated_at`, newest first. Unknown Runs use the existing bounded
404 response shape.

The endpoints:

- read Run, Task, RouteDecision, Approval, Artifact, and AuditEvent records from
  the existing filesystem repository;
- verify registered Artifact metadata against the existing Artifact Store;
- return strict response models with human-readable evidence;
- make no workflow transition, approval decision, audit append, artifact write,
  prompt, or model call.

## Score contract

The overall score is the weighted sum of five repeatable dimensions:

| Dimension | Weight | Calculation |
| --- | ---: | --- |
| Workflow outcome | 25% | completed 100; running/waiting 50; queued 25; failed/cancelled 0 |
| Execution reliability | 25% | 70% task completion plus 30% first-pass completion |
| Artifact evidence | 25% | required-output completeness, required integrity, and total registered integrity |
| Governance | 15% | percentage of approval decisions resolved; no approvals is fully governed |
| Auditability | 10% | expected lifecycle event coverage plus per-task route evidence |

Grades:

```text
excellent >= 85
good      >= 70
attention >= 50
critical  <  50
```

Artifact scoring expects the accepted nine-output D11 decision bundle:

```text
product-brief
product-brief-md
finance-brief
finance-brief-md
executive-decision-memo
prd-product-brief
budget-summary
risk-register
action-plan
```

A missing or corrupt artifact lowers the score. The dashboard reports bounded
integrity evidence and never exposes a local filesystem path.

## Dashboard behavior

The Evaluation view displays:

- evaluated Run count, completion rate, average score, and Artifact integrity;
- the newest Run's weighted dimensions and evidence;
- Agent task success, retry, and average-attempt metrics;
- recent Run history with a direct **Inspect Run** action;
- Provider distribution from persisted routing decisions.

Evaluation is cross-Run and remains available when no Run is selected in the
Mission view. Selecting **Inspect Run** loads that Run through the existing D11
read endpoints.

## Verification

```bash
pytest tests/test_evaluation.py tests/test_state_repository.py tests/test_ui.py
pytest
ruff check app tests
mypy app
node --check app/ui/static/app.js
python -m build
```

D13 also carries the first D12 presentation corrective: stale Run responses are
discarded, terminal failure is distinguished from replay success, and compact
mission controls remain available on narrow screens.
