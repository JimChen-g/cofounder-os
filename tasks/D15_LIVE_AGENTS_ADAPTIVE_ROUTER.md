# D15 — Live Specialist Agents and Adaptive Explainable Routing

Deadline: 2026-07-22 18:00 Asia/Shanghai

## Execution status — accepted 2026-07-22

- P0-P5 completed on branch `codex/d15-live-agents-adaptive-router`.
- Accepted checkpoint: `7f9276d`. Earlier checkpoints preserve the feature,
  live-output hardening, verified-call accounting, and structured-output budget
  fixes in Git history.
- Local verification: 455 tests pass; Ruff, Mypy (66 source files), frontend
  JavaScript syntax, and `git diff --check` pass.
- DGX isolated verification: 17 focused D15 tests pass from a detached clone in
  `/tmp`; the existing deployment checkout and port 9000 were not
  modified.
- Browser acceptance at `127.0.0.1:9100/ui`: the stable fixture produces five
  sources, ten evidence facts, sixteen artifacts, and one pending approval.
  Engineering route-outage simulation recalculates to the generic declared
  fallback, restores normally, and produces no browser warning/error.
- The measured Router marked `cofounder-qwen` healthy and Step unavailable. It
  selected Qwen for both live tasks with score 147.996 versus 117.969 for each
  specialist local fallback, while retaining restricted evidence on the local
  DGX product plane.
- Consecutive real run 1 (`7bdad3c7-70fe-4dde-b3bd-8aa4e8a4a0bf`): Engineering
  completed once with request `req-d1ebb8309801437e` (6,560 tokens, 60,001.91
  ms); Risk completed after one bounded repair with final request
  `req-93d6193dc9364294` (5,780 tokens, 40,816.57 ms). Neither used fallback.
- Consecutive real run 2 (`47c6798b-3995-4ca7-bf24-da6365a224fe`): Engineering
  completed once with request `req-e2cd687ebd7d4a96` (7,153 tokens, 67,992.22
  ms); Risk completed after one bounded repair with final request
  `req-8998183b477d4dad` (5,510 tokens, 38,634.11 ms). Neither used fallback.
- Both runs stopped at one Founder approval with sixteen artifacts and one
  persisted `policy.denied` event. All six delivery artifacts were
  `verified_with_revision`, cited all ten evidence facts, and retained their
  Product, Finance, Engineering, Risk, Synthesizer, and Verifier provenance.
- A fresh GET of run 2 restored `waiting_approval`, ten routes, sixteen
  artifacts, one approval, 114 audit events, and three verified model calls.

## Scope

D15 upgrades the accepted D14 insurance POC without replacing the D06–D14
runtime, Workflow Controller, Artifact Store, Policy Gate, or Mission Control.

### P0 — Frozen acceptance contract

- Engineering Planning and Risk Review become real Gateway-backed LLM Agents.
- Both Agents consume the shared Evidence Package plus persisted upstream
  Product and Finance artifacts.
- Both responses use strict versioned schemas and allow at most one repair call.
- A completion counts as live only when the Gateway returns provider, upstream
  model, request ID, usage, and latency evidence.
- Release remains a deterministic Policy Gate + Founder approval executor.

### P1 — Engineering Planning Agent

- Produce a bounded two-week implementation plan from accepted scope and
  verified platform capabilities.
- Never claim a code diff, test run, deployment, or external write.
- Persist structured output, source Evidence IDs, model call evidence, and
  validation state.
- On model unavailability, execute the declared deterministic local fallback
  and label it as such.

### P2 — Risk Review Agent

- Identify semantic authority, privacy, evidence-quality, and delivery risks.
- Recommend controls and cite Evidence IDs.
- The deterministic Policy Gate remains authoritative and may override the LLM.
- Persist both the LLM recommendation and the final governed decision.
- On model unavailability, execute the declared deterministic local fallback.

### P3 — Adaptive explainable Router

- Hard-filter candidates by measured availability, required capabilities,
  modality, privacy/cloud eligibility, context, latency, and cost.
- Score remaining candidates using task semantic depth, specialist fit,
  locality, measured health latency, and budget headroom.
- Persist candidate scores, score factors, health snapshot, selected candidate,
  exclusion reasons, fallback, and actual execution evidence.
- Simulation may change availability only; it must never count as a live call.

### P4 — D14 integration and Mission Control

- Preserve the fixed D14 DAG.
- Engineering and Risk use live model routes only when measured healthy.
- UI distinguishes `LIVE LLM`, `LOCAL FALLBACK`, and `DETERMINISTIC CONTROL`.
- UI exposes candidate scores, measured health, actual provider/model, latency,
  token usage, request ID, and fallback reason.

### P5 — Verification

- Unit-test schema validation, bounded repair, adaptive ranking, health filters,
  live execution evidence, and deterministic fallback.
- Run lint, type checking, the complete test suite, frontend syntax check, and
  packaging checks.
- Run the fixed demo twice when a live provider is available; otherwise record
  the provider limitation and do not claim live acceptance.

## Explicit non-goals

- No learned or trained Router claim.
- No new Release LLM Agent.
- No autonomous liability decision.
- No real code modification, test execution, deployment, or external delivery.
- No framework replacement or generic DAG editor.

## Acceptance criteria

1. Healthy Qwen or Step causes Engineering and/or Risk to select a live model
   through persisted adaptive scoring.
2. Each selected live Agent performs a real Gateway call and persists verifiable
   call metadata.
3. Invalid model output gets one bounded repair; a second invalid result fails.
4. Provider failure triggers the declared local fallback without a false live
   call claim.
5. Risk LLM advice cannot bypass the deterministic Policy Gate or Founder
   approval.
6. Mission Control makes the live/fallback/control distinction visible without
   inspecting logs.
