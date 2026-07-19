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

1. LOCAL TESTS — run targeted and full test suite locally
2. MAC COMMIT — commit to the local Mac repository (pre-deployment source of truth)
3. SPARK DEPLOYMENT — deploy via `scripts/deploy-to-spark.sh`
4. GITHUB PUSH — push to GitHub origin
5. THREE-PLANE VERIFICATION — verify local HEAD == Spark HEAD == origin/main HEAD
6. FINAL LOCAL RECOVERY PACKAGE — create timestamped recovery package via `scripts/create-stage-backup.sh`
7. INDEPENDENT REVIEW — stop and wait for independent review

The committed Mac repository is the pre-deployment local source of truth.
The timestamped recovery package is the final accepted recovery asset.

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
