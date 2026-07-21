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
              Explainable Router (decision)
                         |
                         v
 Evidence -> Executive -> Product + Finance -> Engineering + Risk
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

This is a D14 product overlay. It reuses the accepted D06-D13 Run, Task,
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

The deterministic golden path works without a live model service and binds
executed routes to separate local Agent adapters. When the DGX Qwen and Step providers are healthy,
the existing Gateway remains their only access boundary; this fixture does not
pretend that a routing decision performed a live call.

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
- Click **Simulate route fallback** before launch if the fallback branch is
  part of the recording. Emphasize that this constraint is submitted with the
  Run and the selected local fallback is bound to actual execution.

### 1:10-2:05 — Golden workflow and conflict resolution

- Launch the Mission.
- Show the fixed DAG and the two parallel stages.
- Show Finance reducing Product's CNY 63,000 proposal to CNY 45,000 while
  preserving the CNY 5,000 reserve.
- Show Risk replacing autonomous liability language with “model recommendation
  plus human review.”
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
- Local deterministic Agent adapters execute this stable offline demo; the same
  DGX boundary can host Qwen when provider health is explicitly confirmed.
- The Artifact Store, policy rules, batch evaluation, and multi-Agent state run
  beside local inference.
- Step remains a candidate only for cloud-eligible sanitized context when its
  health, latency, cost, and privacy constraints all pass.
- Provider unavailability has an explicit local fallback or human-review path.

## Known limitations

- The two PNG findings come from a SHA-256-bound synthetic fixture Adapter;
  arbitrary images are rejected recoverably.
- No real-time video, audio, speech transcription, or model fine-tuning exists.
- Routes are persisted decisions; this offline golden workflow performs no
  live Qwen or Step call.
- The Router applies dynamic hard filters (availability/health, capability,
  modality, privacy/cloud eligibility, context, latency, and cost), followed by
  a fixed per-task preference order. It is deterministic policy routing, not a
  learned Router. With the frozen restricted fixture and no confirmed live
  provider health, local Adapters are expected to win consistently.
- A preview may identify a human/live-provider fallback, but the offline Run
  refuses to auto-execute that route and asks the operator to restore a local
  executable route or provide the missing assignment.
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
