# Insurance POC golden demo

## Submission claim

CoFounder OS turns a synthetic insurer requirement PDF, two synthetic accident
images, a Founder Mission, budget constraints, and project-state facts into a
source-linked, governed two-week POC delivery package.

The primary demo proves five system capabilities together:

1. multimodal evidence normalization;
2. capability/privacy/cost-aware model and tool routing;
3. shared-evidence cross-Agent coordination;
4. deterministic policy control and human approval;
5. independently revised, integrity-checked delivery artifacts.

Generic Founder Tasks remain available as secondary examples. They are not the
submission's primary scenario.

## Architecture overlay

```text
Founder Mission + PDF + two PNGs + budget/project JSON
                         |
                         v
                 Evidence Package
                         |
                         v
          Adaptive Explainable Router
                         |
                         v
 Evidence -> Executive -> Product + Finance -> Engineering LLM + Risk LLM
                         |                         |
                         +------ conflicts -------+
                                      |
                                      v
                         Artifact Synthesizer
                                      |
                                      v
                                  Verifier
                                      |
                                      v
             Policy Gate -> Founder Approval -> completed Run
```

This is a D14 product overlay with the bounded D15 live-Agent upgrade. It reuses the accepted D06-D13 Run, Task,
Artifact Store, append-only Audit, Policy Gate, Approval, recovery, Product API,
Mission Control, and Evaluation authorities. It does not replace the Agent or
frontend framework and does not introduce a general DAG editor.

## Clean setup and startup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
python scripts/build_insurance_poc_fixtures.py --verify-only
PRODUCT_DATA_DIR=/tmp/cofounder-os-insurance-demo/data GATEWAY_PORT=9100 bash scripts/run_gateway.sh
```

Open `http://127.0.0.1:9100/ui` and choose **Load stable demo**.

The golden path remains recoverable without a live model service. When DGX Qwen
or Step passes the server-side health check, Engineering Planning and Risk
Review perform real Gateway calls and persist provider, upstream model, request
ID, token usage, latency, and repair evidence. A failed live call executes the
declared local fallback and is never counted as live.

For the primary DGX path, configure the existing Gateway provider before
startup (values are deployment secrets and are not committed):

```bash
export QWEN_BASE_URL=http://127.0.0.1:8000/v1
export QWEN_API_KEY=local-main
export QWEN_MODEL=<served-model-id>
PRODUCT_DATA_DIR=/tmp/cofounder-os-insurance-demo/data \
  GATEWAY_PORT=9100 bash scripts/run_gateway.sh
```

`GET /health` must report `cofounder-qwen` as `healthy` before the UI may plan a
live Qwen route.

## Reproducible demo evaluation

Run the committed six-sample comparison:

```bash
source .venv/bin/activate
python scripts/run_insurance_poc_evaluation.py \
  --data-dir /tmp/cofounder-os-insurance-evaluation \
  --output examples/insurance-poc/demo-evaluation-results.json
```

The result is explicitly labeled `demo evaluation`. Because no approved live
single-model service is configured, the baseline is marked `unavailable` and
no comparative delta is claimed. CoFounder OS metrics come from persisted
workflow records; local Agent latency and the harness runtime are measured.

## Four-minute backup demo script

### 0:00-0:30 — Mission and honesty boundary

- State that all documents, images, companies, vehicles, and claim facts are
  synthetic.
- Click **Load stable demo**.
- Point out the PDF and two PNG files.
- Explain that the checksum-bound image Adapter is stable demo infrastructure,
  not live arbitrary-image understanding.

### 0:30-1:10 — Evidence and routing

- Click **Build Evidence Package** or launch the Mission directly.
- Show business, accident, technical, financial, and compliance evidence.
- Point to source filename, modality, confidence, privacy level, and Agent use.
- Show the ten routing decisions and their capability, modality, privacy,
  provider-health, context, latency, cost, fallback, and validation evidence.
- Show the server-measured Qwen/Step health and eligible-candidate scores.
- Click **Simulate Engineering route outage** before launch if the fallback
  branch is part of the recording. Emphasize that simulation changes only the
  submitted availability constraint and never increments the live-call count.

### 1:10-2:05 — Golden workflow and conflict resolution

- Launch the Mission.
- Show the fixed DAG and the two parallel stages.
- Show Finance reducing Product's CNY 63,000 proposal to CNY 45,000 while
  preserving the CNY 5,000 reserve.
- Show the Engineering and Risk cards labeled **LIVE LLM** when Qwen is healthy,
  including request ID, actual upstream model, latency, and tokens.
- Show Risk advice being constrained to “model recommendation plus human
  review” by the deterministic governance boundary.
- Point out that all ten tasks reference one Evidence Package.

### 2:05-2:45 — Delivery package and verification

- Open **Artifacts**.
- Show the six independent files plus the verification report.
- Open the Executive Decision Memo, Budget Summary, Risk Register, and
  Verification Report.
- Point out checksums, source Evidence IDs, source Agents, version, and
  validation status.
- State that Engineering is plan-only in this fixture: no code diff or test
  success is claimed.

### 2:45-3:25 — Governance and approval

- Open **Approvals**.
- Explain that private accident-data upload was denied by the Policy Gate.
- Explain that only a sanitized package is proposed, and it still requires the
  Founder.
- Use reviewer `founder`, enter a reason restricting approval to sanitized demo
  content, and click **Approve & resume**.
- Return to **Mission** and show the completed Run.

### 3:25-4:00 — Evaluation and DGX Spark value

- Open **Evaluation**.
- Show the six measured CoFounder OS samples, the source case, and the explicit
  unavailable-baseline disclosure.
- Show the completed Run's scenario-aware D13 score, Artifact integrity, Agent
  reliability, routing mix, and Audit trace.
- Close with the DGX value: restricted material stays on the local product
  plane, local Agents execute without data export, and live providers remain
  candidates only after health and privacy eligibility are proven.

## DGX Spark value

- Private insurer documents, accident evidence, budget rows, policy review, and
  complete verification packages remain on the local product plane.
- Healthy local Qwen executes the Engineering Planning and Risk Review Agents
  beside the governed data, while deterministic adapters remain safe fallbacks.
- The Artifact Store, policy rules, batch evaluation, and multi-Agent state run
  beside local inference.
- Step remains a candidate only for cloud-eligible sanitized context when its
  health, latency, cost, and privacy constraints all pass.
- Provider unavailability has an explicit local fallback or human-review path.

## Known limitations

- The two PNG findings come from a SHA-256-bound synthetic fixture Adapter;
  arbitrary images are rejected recoverably.
- No real-time video, audio, speech transcription, or model fine-tuning exists.
- Only Engineering Planning and Risk Review have D15 live LLM execution. Other
  D14 roles remain deterministic controls or previously accepted Agents.
- The Router uses dynamic hard filters plus transparent adaptive scores. It is
  not trained and does not claim to be a learned Router.
- Provider health must be measured by the server. Without a healthy configured
  provider, the two live-capable tasks execute and disclose local fallback.
- The Engineering deliverable is a plan only. It exposes `code_diff=null` and
  `test_result=null` instead of fabricating execution.
- No external insurer write, email, payment, or production integration occurs.
- The six-task measurement is a demo acceptance evaluation, not statistical
  model-quality evidence; the live single-model baseline is unavailable.
- Single-process filesystem state is appropriate for the hackathon demo, not a
  multi-worker production deployment.

## Release verification

```bash
ruff check app tests scripts/run_insurance_poc_evaluation.py
mypy app
pytest -q
node --check app/ui/static/app.js
python -m build --no-isolation
git diff --check
```
