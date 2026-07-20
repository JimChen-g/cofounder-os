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
| D06-B | Filesystem Artifact Store | 4db36ed |
| D06-C | Product Agent | 7aa65a3 |
| D06-D | Product lifecycle integration | 0cda71c |
| D07 | Finance Agent | 4800001 |
| D08 | Deterministic Policy Gate | 4800001 |
| D09 | Artifact Synthesizer | 4800001 |
| D10 | Workflow Controller and recovery | 4800001 |
| D11 | Product API | 6c7de61 |
| G01 | Project delivery unification | g01-accepted (annotated tag) |

## Release Candidates

None. D11 passed independent review, publication, Spark deployment,
three-plane verification, real Product API execution, idempotent replay, and
recovery packaging on 2026-07-20.

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
| 8ee6968 | D06-D corrective: close lifecycle blockers per independent review | Corrective: product_lifecycle, tests, backup script |
| 4f2f14f | D06-D corrective: improve stage report and test-count extraction | Corrective: backup script |
| 85db29d | D06-D corrective: add real behavior tests and close exception paths | Corrective: product_lifecycle, tests |
| 6c7de61 | D11 independent-review corrections and privacy-safe release | Corrective: runtime token budgets, runtime lock isolation, public release hygiene |

## Current State

- **Current accepted product reference**: D11 implementation commit `6c7de61`
- **Resolve accepted product commit with**: `git rev-parse 6c7de61`
- **Resolve current repository HEAD with**: `git rev-parse HEAD`
- **Current governance stage**: G01 — accepted
- **Current product stage**: D11 — Product API — accepted
- **Current release worktree**: `main`
- **Independent D11 reference**: `codex/d11-product-api`
- **Independent D07-D10 reference**: `codex/d07-d10`
- **Current release scope**: D11 — independent review, public publication,
  Spark deployment, real end-to-end execution, three-plane verification, and
  recovery packaging passed
- **Next product stage**: D12 — Founder Mission Control UI — ready to start
- **D06-C recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D06-C/`
- **D06-C recovery package**: `$HOME/Documents/CoFounderOS/stage-backups/D06-C/20260719-115447Z/`
- **D06-B recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D06-B/`
- **Latest D06-B recovery package**: `$HOME/Documents/CoFounderOS/stage-backups/D06-B/20260719-065500Z` (accepted)
- **D06-D recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D06-D/`
- **D06-D accepted implementation HEAD**: 0cda71c33500fb114be28c973548067987430cc5
- **D06-D recovery package**: `$HOME/Documents/CoFounderOS/stage-backups/D06-D/20260719-171712Z/`
- **D07-D10 recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D07-D10/`
- **D07-D10 accepted implementation HEAD**: 4800001ed0b1e979894295c6401ffcfb59a7c98d
- **D07-D10 recovery package**: `$HOME/Documents/CoFounderOS/stage-backups/D07-D10/20260720-050834Z/`
- **D11 recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D11/`
- **D11 accepted implementation HEAD**: 6c7de61a2dad0d7e882713235fbc98f75ddeb0a3

## Mandatory Update Block

Every future accepted stage must update this document in the same commit with:

- New stage entry in Accepted Delivery History
- New stage entry in Corrective History (if applicable)
- Updated Current accepted HEAD
- Updated Current governance stage
- Updated Next product stage
- Updated recovery package path
