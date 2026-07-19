# Project State

## Purpose

This document records the accepted factual state of CoFounder OS. It is the
single source of truth for what has been delivered, what is active, and what
comes next.

This document must be updated in the same commit as every accepted stage.

## Accepted Delivery History

| Stage | Description | Commit SHA |
|-------|-------------|------------|
| D00 | Infrastructure baseline | 92aba6d |
| D01 | Spark deployment workflow | b47b5e9 |
| D02 | Domain models | 12f990b |
| D03 | State repository and lifecycle | e8d38c8 |
| D04 | Orchestration service | 076f97c |
| D05 | Executive Orchestrator | 63ded5f |
| D06-A | Agent execution contract | 4f29e18 |
| D06-B | Filesystem Artifact Store | (pending commit) |
| G01 | Project delivery unification | g01-accepted (annotated tag) |

## Corrective History

| Commit | Description | Context |
|--------|-------------|---------|
| 01bb44a | Rejected premature ProductAgent implementation | Rejected by independent review |
| 946ccf2 | Normal revert of 01bb44a | Preserved history |
| 533b6ac | Initial D06-A execution foundation | Superseded by 4f29e18 |
| 4f29e18 | Accepted lifecycle and ownership correction | Current accepted D06-A |
| e7dd0f4 | Complete G01 governance document set (partial) | Premature acceptance — corrective commit required |
| be99554 | Mark G01 accepted in PROJECT_STATE.md (partial) | Premature acceptance — corrective commit required |
| ca1fe04 | Correct G01 stage ID regex and add tests | Corrective for full G01 scope |
| 1f0691a | Fix zsh read-only variable in backup script | Corrective for full G01 scope |
| 2dbfc2b | Add PATH exports to G01 governance scripts | Corrective for full G01 scope |
| 37cf702 | Replace awk with zsh builtins for portability | Corrective for full G01 scope |
| 58af6ed | Use full path for git commands in backup script | Corrective for full G01 scope |
| 03de57c | Use full paths for date and shasum | Corrective for full G01 scope |
| 028f6d5 | Final G01 closeout — all gates passed | Accepted G01 |
| 102f6cb | Mark G01 accepted after all checks pass | Final G01 acceptance record |
| e7f698f | Complete G01 closeout corrections per independent review | Corrective: scripts, templates, schema alignment |
| b999645 | Use grep -F for path matching in test script | Corrective |
| b3ab61c | Correct PROJECT_STATE stage grep pattern and annotation | Corrective |
| c842150 | Use /usr/bin paths for sed and grep in all scripts | Corrective |
| 2bccc83 | Use correct sed and mktemp paths in test script | Corrective |
| ccda3a7 | Use unanchored grep pattern for PROJECT_STATE stage parsing | Corrective |
| c9efda1 | Print WORKTREE_STATUS=DIRTY when worktree is dirty | Corrective |
| e1101d9 | Echo clipboard content to stdout in project-preflight.sh | Corrective |
| aff5e67 | Fix preflight exit code and complete G01 final closeout | Final G01 commit |

## Current State

- **Current accepted reference**: annotated tag `g01-accepted`
- **Resolve accepted commit with**: `git rev-parse 'g01-accepted^{}'`
- **Resolve current repository HEAD with**: `git rev-parse HEAD`
- **Current governance stage**: G01 — accepted
- **Current product stage**: D06-B — Filesystem Artifact Store
- **Next product stage**: D06-C — Product Agent
- **G01 recovery package directory**: `/Users/jimcheng/Documents/CoFounderOS/stage-backups/G01/`
- **Latest G01 recovery package**: `/Users/jimcheng/Documents/CoFounderOS/stage-backups/G01/<UTC timestamp>/`
- **Historical D06-A recovery package**: `/Users/jimcheng/Documents/CoFounderOS/stage-backups/D06-A/20260718-142836Z`
- **D06-B recovery package directory**: `/Users/jimcheng/Documents/CoFounderOS/stage-backups/D06-B/`
- **Latest D06-B recovery package**: `/Users/jimcheng/Documents/CoFounderOS/stage-backups/D06-B/20260719-043444Z`

## Mandatory Update Block

Every future accepted stage must update this document in the same commit with:

- New stage entry in Accepted Delivery History
- New stage entry in Corrective History (if applicable)
- Updated Current accepted HEAD
- Updated Current governance stage
- Updated Next product stage
- Updated recovery package path
