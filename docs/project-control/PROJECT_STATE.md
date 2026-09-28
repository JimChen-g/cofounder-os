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
| D12 | Founder Mission Control UI | b96b557 |
| D13 | Evaluation Dashboard | 5feb555 |
| G01 | Project delivery unification | g01-accepted (annotated tag) |
| T17–T22 (September) | Accepted implementation and isolated Spark engineering/feedback chain; component and evaluation limits disclosed; production release/recovery pending receipts | 5229238181b56a593ea5969c31885cc100c9f446 |

## Release Candidates

- D14 Insurance POC golden demo: local P0-P6 implementation and release gates
  complete on branch `codex/d14-insurance-poc`. It remains a release candidate
  pending the existing independent review, publication, Spark deployment, and
  recovery-package acceptance process.
- D13 passed corrective independent review, governance-delta review, GitHub
  publication, Spark deployment, three-plane verification, desktop plus 390px
  browser/API flows, and recovery packaging on 2026-07-21.

## Accepted Follow-up

D13 has no open P0-P3 findings. Its deterministic Evaluation service remains
read-only, and the D12 presentation correctives are accepted without changing
D06-D12 workflow authority, provider routing, persistence, or Product API
contracts.

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
| b96b557 | D12 Qwen output-budget correction | Corrective: Product brief completion budget and regression evidence |
| 290fb31 | Close D13 independent-review findings | Corrective: Run isolation, malformed artifact handling, scoring semantics, provider denominator |
| 8c3409e | Preserve immutable candidates during bounded failed-check retries | September T17/T18; original failed Runs retained |
| 0fe1ffb | Collect completed gate failures and stop on execution/cleanup faults | September T17/T18; no extra Task attempts |
| a2facebf | Present immutable numbered source to the independent reviewer | September T17/T18; original review gate retained |
| 5229238 | Bind six strict review checks to exact source evidence and preserve truncation diagnostics | Accepted September implementation; real initial/feedback checks passed |

## Current State

- **Current accepted implementation HEAD**: `5229238181b56a593ea5969c31885cc100c9f446`
- **Resolve accepted implementation with**: `git rev-parse 5229238181b56a593ea5969c31885cc100c9f446`
- **Resolve current repository HEAD with**: `git rev-parse HEAD`
- **Current governance stage**: September T17–T22 implementation acceptance;
  normal PR #4 merge, production deployment and recovery require their receipts
- **Current product stage**: T17/T18 real version-bound engineering feedback
  accepted; T19–T22 component/evaluation limits in `docs/t17-t22.md`
- **Current release worktree**: `feat/t17-t22`; authoritative September Mac
  checkout and fixed Spark application target from the stage handoff
- **Accepted real Run / Task**: `95e51ba9-37e7-49a5-b5c4-49626523378f` /
  `5ec673ee-2fa5-4f9d-8889-d8eaf000efd1`; initial and Web-feedback revisions
  each passed fixed 14, generated 20, regression 19 and independent v2 review
- **Business delivery status**: revision 2 remains pending and unapproved;
  12 isolated fixture checks are explicitly not actual user approval
- **Current verification**: 631 local tests passed; implementation CI passed
  Python 3.10/3.12. Release receipt records the later docs/merge commit and CI
- **Release receipt**: `outputs/t17-t22/status.json` in the task output package;
  production deployment is not claimed by this pre-deployment source record
- **Recovery receipt / private package path**:
  `outputs/t17-t22/evidence/production-recovery.json` in the task output package
  is planned; its actual private package path and checks must be recorded after
  execution. Earlier July packages below are historical
- **Next acceptance action**: normal PR merge, fixed-commit application deploy,
  three-end verification and new recovery validation; preserve live state
- **Next product stage**: T23–T24 under the September plan; T26 phone approvals,
  broader Skill evaluation and submission materials remain separate pending work
- **Historical July product reference**: D13 implementation `5feb555`; its
  accepted scope and recovery records below do not replace September receipts
- **Independent D13 reference**: `codex/d13-evaluation-dashboard`
- **Independent D12 reference**: `codex/d12-founder-mission-control`
- **Independent D11 reference**: `codex/d11-product-api`
- **Independent D07-D10 reference**: `codex/d07-d10`
- **Historical D13 release scope**: deterministic evaluation service and API,
  Evaluation UI, D12 presentation corrective, 418-test quality gates,
  independent review, publication, deployment, three-plane verification, and
  recovery packaging passed
- **Historical July next-stage record**: D14 — Insurance POC golden demo and Hackathon
  submission package — release candidate pending independent acceptance
- **Historical July implementation candidate**: `codex/d14-insurance-poc`, based on
  `8276561`; P0-P6 acceptance is defined in
  `tasks/D14_HACKATHON_SUBMISSION.md`
- **Historical July acceptance action**: run independent D14 review, publish the approved
  candidate, verify it on DGX Spark, and create the accepted recovery package
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
- **D12 recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D12/`
- **D12 accepted implementation HEAD**: b96b557324b98e10db5874ea806adcfab0de24b1
- **D12 recovery package**: `$HOME/Documents/CoFounderOS/stage-backups/D12/20260720-110401Z/`
- **D13 recovery package directory**: `$HOME/Documents/CoFounderOS/stage-backups/D13/`
- **D13 accepted implementation HEAD**: 5feb555c5cba211095e425ca97bc88b6d15e1a1c
- **D13 recovery package**: `$HOME/Documents/CoFounderOS/stage-backups/D13/20260721-000329Z/`

## Mandatory Update Block

Every future accepted stage must update this document in the same commit with:

- New stage entry in Accepted Delivery History
- New stage entry in Corrective History (if applicable)
- Updated Current accepted HEAD
- Updated Current governance stage
- Updated Next product stage
- Updated recovery package path

## September hackathon T07–T10 candidate

The founder authorized T07–T10 against `40509e8` on 2026-09-26. Implementation
and isolated Spark validation are documented in `docs/t07-t10.md`; current work
uses `codex/t07-t10`. This supplements the July history without declaring
T11–T18, independent review, or a public release accepted. The next implementation
scope is T11–T14 and T16 only after the T07–T10 evidence handoff is checked.

## September T11–T16 implementation candidate

Founder authorized T11–T16 on 2026-09-27 against `b53e58c`. Branch
`codex/t11-t16` adds the bounded engineering executor, independent code review,
small candidate sampling and paired Feishu task entry. See `docs/t11-t16.md`.
Acceptance is evidence-driven in the current task's T11–T16 output package;
T17 onward, mobile approvals and formal training/holdout evaluation remain pending.
The authoritative Mac checkout is the September T07–T10 workspace supplied by the
founder; the old Projects checkout remains untouched. July history is retained.

## September T17–T22 implementation candidate

Founder authorized version-bound delivery, bounded feedback repair, spark-decide,
portable Skill and frozen small-data/paired evaluation work against `0dbf5e1`.
Branch `feat/t17-t22`; implementation boundary is in `docs/t17-t22.md`.
Acceptance follows the current stage's actual evidence; T23+ remain pending.
The September source checkout and deploy-by-fixed-commit workflow supersede the
historical July paths for this authorized stage, without modifying infrastructure.

### T17–T22 engineering acceptance correction

The founder requested diagnosis, correction, normal PR merge and production
deployment on 2026-09-27. The original failed real Runs are preserved. The
correction uses immutable-candidate edits for failed-check retries and retains
the original Task limit of two total attempts. Real initial/feedback/review
acceptance passed on implementation `5229238`: the same Run/Task advanced from
revision 1 / attempt 1 to revision 2 / attempt 2, with a changed patch, artifact
and approval nonce, fresh tests and independent review. The business Run remains
pending/unapproved. Five earlier failed Runs and their ten attempts are retained.
Production deployment and recovery remain pending their actual receipts in this
pre-deployment source record.

Acceptance evidence is `outputs/t17-t22/evidence/engineering-acceptance-final.md`
in the task output package at
`/Users/jimcheng/Documents/Codex/2026-09-27/cofounder-os-t17-t22/`.
Production/recovery outcomes are recorded separately in the receipts above.
Production Gateway credential rotation remains separately unauthorized; this
application release does not imply rotation, Qwen restart or a second bridge.

## September T23–T27 candidate

The founder authorized T23–T27 against accepted production merge 560666a7.
Independent checkout `codex/t23-t27` implements phone text delivery commands,
paired notifications and conservative shadow quality prediction. See
`docs/t23-t27.md`. Real phone acceptance, CI/merge, deployment and recovery remain
pending the current stage output receipts. The original expired/unapproved Run
and its exhausted attempt count are preserved. This stage follows the September
PR-first/fixed-commit release authorization rather than the historical July order.

T26 export follow-up: provide the actual approved JSON as a Feishu attachment,
with a backwards-compatible inbox column retaining its platform message ID.
Metadata-only replies are not claimed as a downloadable phone delivery.

## September T28–T29 development acceptance

Current product baseline remains `57502708e30e8db9fcb72c508f37b04de590a2dd`.
T23–T27 actual acceptance is recorded in its stage receipts; historical pending
entries above are preserved. T28 bounded Agent trials and T29 638-test CPU
regression completed with explicit transport/evaluation limits in
`docs/t28-t29.md`. This stage adds evaluation/docs only; no production redeploy.
U01 local connection and user acceptance are recorded separately; T30+ not done.

U01 local-only proxy follows development handoff 6d3f669. It reuses the existing
production UI/Controller, with no production release. Live self-test failures and
final pending-candidate identity are explicitly separated in docs/u01-local-access.md.
Founder approval is not submitted; subsequent T stages remain pending.
