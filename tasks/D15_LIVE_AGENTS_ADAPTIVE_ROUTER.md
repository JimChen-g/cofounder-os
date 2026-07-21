# D15 — Live Specialist Agents and Adaptive Explainable Routing

Deadline: 2026-07-22 18:00 Asia/Shanghai

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
