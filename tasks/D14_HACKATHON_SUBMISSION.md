# D14 - NVIDIA DGX Spark Hackathon Insurance POC Demo

## Status

RELEASE CANDIDATE. P0-P6 implementation and local release gates completed on
2026-07-21. The accepted D06-D13 authorities remain unchanged. D14 is not
marked accepted until the repository's independent review and release process
is performed.

## Deadline

2026-07-22 18:00 Asia/Shanghai. Code freeze begins at 17:30.

## Product claim

CoFounder OS turns private, multimodal company evidence into an executable and
governed two-week insurance POC by routing work across local and cloud models,
coordinating specialist Agents, verifying the decision bundle, and requiring a
human decision before any external write.

## One golden mission

Use the uploaded accident evidence, insurer POC requirements, current code
capabilities, and bounded budget to define and begin executing a two-week
insurance-company POC.

Generic Founder tasks remain supported as secondary examples. They are not the
submission's primary demonstration.

## Frozen inputs

- Founder mission text from `examples/insurance-poc/scenario.json`.
- One synthetic insurer requirements PDF.
- Two synthetic accident-scene PNG images.
- Structured budget and current-project facts in the scenario JSON.
- All data is synthetic and contains no person, vehicle, insurer, or location
  identifier from a real claim.

## Required decision bundle

1. Executive Decision Memo.
2. Insurance POC Product Brief.
3. Technical Implementation Plan.
4. Budget Summary.
5. Risk Register.
6. Two-week Action Plan.
7. Code diff and test result only when the existing Engineering execution
   chain performs a real repository action. A missing execution capability must
   be disclosed; success must not be simulated.

## Golden workflow

```text
Evidence Extraction
  -> Executive Orchestrator
  -> Product + Finance
  -> Engineering + Risk
  -> Artifact Synthesizer
  -> Verifier
  -> Human Approval
```

The workflow is fixed for the submission. D14 does not introduce a general DAG
editor or a new Agent framework.

## Implementation phases and acceptance

### P0 - scenario and fixture freeze

- Stable scenario, PDF, two images, budget, technical baseline, manifest, and
  acceptance criteria exist in `examples/insurance-poc/`.
- `scripts/build_insurance_poc_fixtures.py` regenerates the binary inputs.
- Rendered PDF inspection and asset checksum verification pass.

### P1 - multimodal Evidence Package

- PDF and image files enter the current Mission creation experience.
- Evidence includes ID, category, content, source filename, modality,
  confidence, privacy level, Agent consumers, timestamp, processing status, and
  Adapter provenance.
- Extraction failure is explicit and recoverable.
- At least one PDF fact and one image fact affect downstream Product, Finance,
  or Risk output.

### P2 - explainable routing

- Capability, modality, privacy, complexity, context, tool, latency, cost, and
  validation requirements are persisted with each route.
- Candidate, selected, excluded, fallback, estimated cost/latency, and verifier
  decisions are visible and auditable.
- At least three task classes route differently and a deterministic simulated
  availability constraint exercises fallback without claiming a live call.

### P3 - shared-evidence golden workflow

- Every Agent task references the same Evidence Package and relevant upstream
  Artifacts.
- Budget scope conflict and human-review language conflict are structured
  records produced from Agent proposals plus deterministic constraints.
- Dependency, parallelism, revision, and accepted resolution are visible.

### P4 - governed delivery package

- Six independent, versioned Artifacts are viewable and downloadable.
- Artifact source Agents, source Evidence, validation status, and revision
  provenance are preserved.
- Policy blocks one private-data external write and creates a real pending
  Approval.
- Verifier preserves before/after versions and correction reasons.

### P5 - demo evaluation

- Five to eight existing representative tasks are run as a clearly labeled
  demo evaluation.
- Single-model/no-router baseline is compared with routed multi-Agent plus
  Verifier execution using persisted run evidence.
- Results are reproducible and do not claim statistical significance.

### P6 - release gate

- Ruff, MyPy, full Pytest, JavaScript syntax, package build, API tests, and UI
  tests pass.
- Success, fallback/recovery, and policy-block flows pass.
- The fixed demonstration succeeds twice without state loss.
- README, architecture, launch guide, known limitations, DGX Spark value, and
  a 3-5 minute backup-demo script are complete.

## Release-candidate evidence

- Six deterministic demo-evaluation runs exercise the success path and persist
  the comparison result in `examples/insurance-poc/demo-evaluation-results.json`.
- Workflow tests exercise approval, rejection, fallback, replay, recovery, and
  evaluation without a live model call.
- Eight real 1280x720 browser walkthrough frames and a three-minute H.264
  backup walkthrough were generated outside the repository as submission
  deliverables. The video is explicitly a silent guided screenshot walkthrough,
  not a live screen recording or a claim of live model inference.
- Model endpoints were unavailable during local acceptance. Routing records are
  therefore auditable decisions with estimates and deterministic fallbacks;
  they are not represented as successful provider calls.

## Frozen exclusions

- No real-time video or speech.
- No model fine-tuning or second local model.
- No new Growth or HR Agent.
- No general DAG editor.
- No framework replacement, broad UI rewrite, or D06-D13 authority change.
- No email, payment, or production-system write integration.
- No claim that a fixture Adapter performed live model inference.

## Rollback

The D14 branch starts at `8276561`. Reverting D14 commits restores the accepted
D06-D13 contracts plus the pre-existing synthetic traffic-liability fixture.
