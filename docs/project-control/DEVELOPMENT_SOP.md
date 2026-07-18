# Development Standard Operating Procedure

## Purpose

This document defines the mandatory implementation lifecycle for every
CoFounder OS stage. No code change, test update, or deployment may skip
any step in this procedure.

## Document Authority

The full governance hierarchy is recorded in `CLAUDE_MASTER_OPERATING_CONTRACT.md`.
In summary:

1. `CLAUDE_MASTER_OPERATING_CONTRACT.md` — immutable operating rules
2. `docs/project-control/PROJECT_STATE.md` — accepted factual state
3. `docs/project-control/ROADMAP.md` — approved development sequence
4. `tasks/D##_*.md` — current stage requirements
5. `docs/` architecture contracts

When documents conflict, stop and resolve the lower-priority document.
Do not proceed until the conflict is resolved.

## Mandatory Implementation Lifecycle

Every implementation step must follow this sequence in order:

1. Read the task definition in `tasks/D##_*.md`.
2. Verify branch, local HEAD, origin/main HEAD, and clean worktree.
3. Confirm the approved baseline commit hash.
4. Inspect existing domain, state, service, and test files.
5. State the exact files to change and the exact tests to add or update.
6. Confirm that model services, ports, tunnels, systemd, launchd,
   credentials, and `.env` files will not be modified.
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

## Worktree Hygiene

- Always start from a clean worktree.
- Never reset, restore, clean, revert, or discard current changes
  unless explicitly instructed.
- If a session fails mid-stage, resume from the existing worktree state
  rather than starting over.
- Each stage commits must update `docs/project-control/PROJECT_STATE.md`
  in the same commit.

## Definition of Done

A development task is complete only when:
- All steps in the mandatory implementation lifecycle have been executed.
- All checks pass without exceptions.
- The delivery report has been returned.
- Independent review has been accepted.
