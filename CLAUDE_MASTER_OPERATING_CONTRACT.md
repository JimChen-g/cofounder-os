# CoFounder OS — Master Operating Contract

## Purpose

This document is the single source of truth for how CoFounder OS is developed,
reviewed, and deployed. Every implementation session must read and follow this
contract before changing any file.

## Source of Truth Hierarchy

1. This contract (CLAUDE_MASTER_OPERATING_CONTRACT.md)
2. Task definitions in `tasks/D##_*.md`
3. Architecture documents in `docs/`
4. Frozen infrastructure contract in `docs/architecture-contract.md`

When documents conflict, the higher-ranked document wins. Conflicts must be
resolved by updating the lower-ranked document, not by ignoring the higher one.

## Deployment Planes

### Mac Development Plane

- Source-code editing and local Git history
- Claude Code, Cursor, and browser-based development
- GitHub submission and release packaging
- Persistent SSH tunnel management
- Product testing through the local Gateway endpoint

### DGX Spark Product Plane

- Qwen local inference through vLLM
- CoFounder Gateway execution
- Step provider access through the Gateway
- Product API and UI runtime
- Runtime logs, run state, evaluation output, and artifacts

The DGX Spark checkout is a deployment target. It must not become an
independent source of truth that diverges from the Mac repository.

## Mandatory Implementation Workflow

Every implementation step must follow this sequence:

1. Read the task definition in `tasks/D##_*.md`.
2. Verify branch, local HEAD, origin/main HEAD, and clean worktree.
3. Confirm the approved baseline commit hash.
4. Inspect existing domain, state, service, and test files.
5. State the exact files to change and the exact tests to add or update.
6. Confirm that model services, ports, tunnels, systemd, launchd, credentials,
   and `.env` files will not be modified.
7. Implement the smallest viable change.
8. Run targeted tests.
9. Run full test suite.
10. Run `git diff --check`.
11. Run Ruff on changed Python files.
12. Review staged files for secrets and runtime data.
13. Commit only if all checks pass.
14. Deploy with `scripts/deploy-to-spark.sh`.
15. Run status, health, and smoke validation.
16. Push to GitHub.
17. Verify Mac, Spark, and GitHub HEAD equality.
18. Return the mandatory delivery report.
19. Stop and wait for independent review.

## Layer Ownership

### Infrastructure Layer (DO NOT MODIFY)

- Qwen model weights
- vLLM environment and startup parameters
- Gateway provider connectivity
- systemd services
- launchd SSH tunnel
- SSH configuration
- Fixed ports

### Product Platform Layer (PRIMARY DEVELOPMENT)

- Domain models (`app/domain/`)
- Run and Task state (`app/state/`)
- Orchestration service (`app/services/`)
- Agent contracts and implementations (`app/agents/`)
- Gateway Client (`app/clients/`)
- Orchestrators (`app/orchestrators/`)
- Workflow Controller
- Policy Gate and Approval Gate
- Artifact generation
- Evaluation framework

### Presentation Layer

- FastAPI product endpoints
- HTML, CSS, and JavaScript
- Demo timeline, route visualization, approval controls, artifact viewer,
  audit view

## Prohibited Changes During Ordinary Feature Development

- Replacing the working Qwen model
- Rebuilding the known-good vLLM environment
- Changing vLLM startup parameters
- Changing the frozen port topology
- Bypassing the Gateway
- Adding a second local model
- Replacing systemd or launchd supervision
- Introducing Kubernetes, Docker Compose, PostgreSQL, Redis, or a queue
- Expanding SSH credentials or public network exposure
- Modifying `.env` files or credential stores

A prohibited change requires:
1. A documented product-level blocker
2. Evidence that the current contract cannot satisfy the requirement
3. A rollback plan
4. A separate architecture decision record
5. Full infrastructure and product regression testing

## State Authority

Only the Workflow Controller (currently `OrchestrationService`) may change:
- Run status
- Task status
- Approval status
- Dependency resolution
- Retry count
- Terminal failure state

Agents may propose actions and return structured results, but they are not
authoritative state controllers.

## Model Output Discipline

All model output must be treated as untrusted input.

Every structured response must have:
- A versioned schema
- Pydantic validation
- Bounded repair attempts
- A terminal failure path
- An audit event
- Original provider and routing metadata

Invalid model output must never silently become valid system state.

## Logging

Logs must provide enough diagnostic information to avoid large screenshot sets.

Logs may contain:
- IDs
- State transitions
- Timing
- Provider names
- Routing reasons
- Error classes
- Redacted payload summaries

Logs must not contain:
- API keys
- SSH private keys
- Gateway key values
- Full `.env` contents
- Unredacted user secrets

## Definition of Done

A development task is complete only when:
- Acceptance criteria pass
- Changed files are listed
- Test results are recorded
- Infrastructure remains healthy
- No secret or generated runtime data enters Git
- A rollback path exists
- The next dependency is explicit
- Delivery report is returned

## Delivery Report Format

Every implementation session must end with this report:

```
FINAL_RESULT=PASS|FAIL
CHANGED_FILES=...
BACKUP_PATH=...
TEST_RESULT=...
CURRENT_SERVICES=...
NEXT_ACTION=...
```
