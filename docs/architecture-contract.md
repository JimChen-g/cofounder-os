# CoFounder OS Architecture Contract

## Status

This document defines the frozen infrastructure boundary established by D00.
Product development must preserve this contract unless an architecture-level
failure is demonstrated and a deliberate migration is approved.

## Deployment Planes

### Mac Development Plane

The Mac is the authoritative development and submission environment.

Responsibilities:

- Source-code editing and local Git history
- Cursor, Claude Code, Codex, and browser-based development
- GitHub submission and release packaging
- Persistent SSH tunnel management
- Product testing through the local Gateway endpoint

### DGX Spark Product Plane

DGX Spark is the product runtime and local inference environment.

Responsibilities:

- Qwen local inference through vLLM
- CoFounder Gateway execution
- Step provider access through the Gateway
- Product API and UI runtime
- Runtime logs, run state, evaluation output, and artifacts

The DGX Spark checkout is a deployment target. It must not become an
independent source of truth that diverges from the Mac repository.

## Frozen Request Path

```text
Mac application or browser
    -> http://127.0.0.1:19000
    -> persistent SSH tunnel
    -> DGX Gateway http://127.0.0.1:9000
         -> local Qwen provider http://127.0.0.1:8000
         -> remote Step provider
```

Product code must call the Gateway. Product code must not call the local Qwen
vLLM endpoint or the Step API directly.

## Frozen Virtual Model Identifiers

- `cofounder-auto`
- `cofounder-qwen`
- `cofounder-step`

These identifiers are application contracts. Renaming them requires a
versioned migration and regression test.

## Frozen Service Identifiers

DGX Spark:

- `cofounder-qwen.service`
- `cofounder-gateway.service`
- `cofounder-maintenance.timer`

Mac:

- `com.cofounder.ssh-tunnel`
- `com.cofounder.maintenance`

## Frozen Infrastructure Ports

| Purpose | Address |
|---|---|
| Mac Gateway entry | `127.0.0.1:19000` |
| DGX Gateway | `127.0.0.1:9000` |
| DGX Qwen vLLM | `127.0.0.1:8000` |
| SSH access | Current configured Spark SSH endpoint |

The Qwen endpoint and DGX Gateway must remain loopback-only and must not be
directly exposed to the public network.

## Component Responsibilities

### Gateway

The Gateway owns:

- Provider registration
- Qwen and Step access
- Provider health reporting
- Model routing
- Standardized upstream responses
- Routing reason and provider metadata

The Gateway does not own:

- Founder task lifecycle
- Agent orchestration
- Human approval state
- Product artifacts
- Product UI state

### Product API

The Product API will own:

- Runs and tasks
- Agent orchestration
- Approval state
- Audit events
- Artifact creation and retrieval
- Product-level retries and recovery

### Product UI

The Product UI will visualize:

- Founder objective
- Task decomposition
- Agent activity
- Model route and routing reason
- Approval requirements
- Generated artifacts
- Audit trace

## Data Boundary

The preliminary MVP uses append-only JSONL plus structured JSON files.
A database, queue, or distributed state system is not required.

Expected runtime structure:

```text
data/runs/<run_id>/
    events.jsonl
    run.json
    tasks.json
    artifacts/
```

Agents return results. Only the Workflow Controller may change authoritative
Run, Task, and Approval state.

## Security Boundary

The following must never enter Git history, snapshots, generated artifacts, or
logs:

- Real `.env` files
- API keys
- SSH private keys
- Gateway authentication values
- Provider credentials
- Unredacted environment dumps

`.env.example` is intentionally tracked as a value-free configuration template.

## Change Control

The following changes are prohibited during ordinary feature development:

- Replacing the working Qwen model
- Rebuilding the known-good vLLM environment
- Changing vLLM startup parameters
- Changing the frozen port topology
- Bypassing the Gateway
- Adding a second local model
- Replacing systemd or launchd supervision
- Introducing Kubernetes, Docker Compose, PostgreSQL, Redis, or a queue
- Expanding SSH credentials or public network exposure

A prohibited change requires all of the following:

1. A documented product-level blocker
2. Evidence that the current contract cannot satisfy the requirement
3. A rollback plan
4. A separate architecture decision record
5. Full infrastructure and product regression testing

## Recovery Order

1. Restart Qwen and wait for model readiness.
2. Restart the Gateway.
3. Run `cofounderctl status`.
4. Run `cofounderctl health`.
5. Run `cofounderctl smoke`.
6. Roll back product code before modifying the model runtime.

## D00 Acceptance

D00 is accepted only when:

- The repository is clean before freezing.
- A verified Git Bundle rollback backup exists.
- Architecture and development guardrails are committed.
- Infrastructure health checks pass before and after the commit.
- The annotated `infra-stable-20260717` tag points to the accepted commit.

## D14 Insurance POC overlay

D14 preserves every frozen plane and authority above. It adds one bounded
product overlay:

```text
synthetic PDF/PNG/JSON -> Evidence Package -> explainable route decisions
-> fixed eight-task DAG -> Artifact Store -> Verifier -> Policy Gate/Approval
```

Raw restricted inputs remain on the DGX product plane. The Router may select
Step only for sanitized, minimum-necessary planning context. Finance, Risk, and
verification prefer local Qwen. Repository actions retain the existing
Engineering execution boundary. The offline golden fixture stores route
decisions as `decision_only` and executes deterministic acceptance rules; it
does not claim a provider call.

This overlay does not change ports, virtual model identifiers, service names,
deployment source of truth, Workflow Controller authority, or the prohibition
against direct Qwen/Step access.
