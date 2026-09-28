# Independent review corrections — September 28, 2026

The review fixed source `9bdf757c4c8e2a7822235322fe0ca8a3acf052b5`.
This correction release preserves historical Runs, approvals, budgets and frozen
T27/T28 evidence. It changes neither the production model nor UI layout.
Release validation and deployment receipts are recorded separately from coding.

| Finding | Implementation and verification boundary | Residual limitation |
|---|---|---|
| F1/F2 | Feedback checks persistent time/call/token and Task attempt capacity before mutation; denial is a conflict preserving the pending candidate. Accepted repair failures get an explicit delivery failure state. | Preflight cannot guarantee inference finishes before the unchanged total deadline; later reservation remains authoritative. |
| F3 | Repair intent is persisted before the Task write so crash reconciliation can see it. | Cross-file Run/Task updates are not a single durable transaction and do not uniformly use general state-machine transitions. Partially mitigated; full transition refactor remains post-submission. |
| F4 | Source quotations must be substantive and cover implementation and tests. | Same-model separate context and quotation hygiene do not establish semantic support or independent model judgment. Partial mitigation only. |
| F5 | Generated tests must reject two known incorrect implementations, in addition to normal pass evidence. | This catches the reported fake-pass/exit-zero case; hostile Python can still target specific probes. Not comprehensive adversarial test integrity. |
| F6 | New Runs use versioned oracle v2 covering seven omitted contract categories; import-time deepcopy and simple encoder substitution no longer hide mutation. | Old Runs retain their original oracle; this is no proof of completeness against arbitrary malicious code. |
| F7 | Evidence writes use fsync and atomic replace; reads use current-Run workspace pointers. Corrupt current evidence is logically quarantined and audited with original bytes retained. | Multi-file state is not atomic; unrelated legacy evidence is not scanned on each execution. |
| F8 | Dedicated decision credential is endpoint-scoped. General gateway cloud use requires an explicit server setting. | Per-credential durable quotas/rate limiting are not implemented. Production's existing authorized Step access requires explicit opt-in. |
| F9 | SSH key and pinned known_hosts; proxy reads a private local token. Product and bridge configuration/environment are separated; optional single-Run proxy scope is supported. | Same-UID processes are not an OS isolation boundary. Full local portal retains founder authority by design. |
| F10 | Corrected v2 harness accepts only HTTP 422 for the invalid-request contract; reproducible missing-trial continuation creates a new manifest and refuses partial replay. | Original 0/10 multi-legal-choice benchmark only measures protocol adherence. Historical continuation manifest is missing; no replacement was fabricated. No new decision-quality experiment. |
| F11 | Request uniqueness, response shadow and demo output match public schemas; executable schema regression. | Shadow scores are uncalibrated diagnostics, not confidence. |
| F12 | Recovery of a claimed engineering attempt fails explicitly without replaying model calls. | Recovery resumes no unknown in-flight inference; human initiation of a new task remains separate. |
| F13 | New candidate snapshots receive Git references. Existing Spark objects are verified and protected before publishing; bundle restore verifies all 16 historical objects. | Worktree lifecycle cleanup remains deferred; no history removed. |
| F14 | Fixed error reply and version-bound review/test notification summary, tested with mock receivers. | Platform acceptance is not human reading; uncertain sends are not blindly replayed. |
| F15 | README and current project state now distinguish engineering execution, Skill and historical planning; stale authentication and test claims removed. | Historical stage statements remain historical, not current acceptance. |
| F16 | Exact observed dependency constraints, declared proxy/timezone/schema dependencies, pinned CI runner/Node/actions and observed base-image digest. | Version constraints are not a complete cross-platform wheel-hash lock; existing production image is retained, not claimed freshly rebuilt. |

## Evaluation interpretation

Original T28: with Skill 13/20, without 11/20, including seven transport failures
per arm. The separate recovery supplement was 7/7 per arm. Across batches 20/20
versus 18/20 is descriptive only. All ten cases avoid choosing among multiple
legal alternatives; the difference consists of two repetitions of one tool-use
case. Fisher two-sided values reported by the independent review are approximately
0.748 and 0.487; repeated temperature-zero trials are not independent evidence of
general quality improvement. Frozen files are preserved under the original
revision and the byte-identical `evaluation/t28/frozen-skill` snapshot.

Use `evaluation/t28-v2` for corrected scoring and continuation. Do not overwrite
old results or compare a changed protocol as if it were the original experiment.

## Release process

Run targeted adversarial regressions and the full suite, Ruff, Mypy, frontend
syntax and build before commit. CI must pass on the PR and merged main. Deploy
only the reviewed commit after checking no active user dispatch; retain rollback
source/configuration and all runtime ledgers. Verify health, credential scopes,
protected Run snapshots, candidate bundle objects and private restore afterwards.
No real founder approval or Feishu test message is submitted by this correction.

## Local verification before PR

703 full-suite tests passed, followed by focused final exception/restart/recovery
regressions. Ruff, strict Mypy across 89 application files, both frontend syntax
checks and package build passed. Spark isolated Docker checks passed for the
21-case positive candidate and both mutation probes; forged-pass output and
deepcopy hijacking were rejected. These checks used no model call or approval.
The PR CI checks the final complete source snapshot on Python 3.10 and 3.12.

## Release acceptance

[PR #8](https://github.com/JimChen-g/cofounder-os/pull/8) merged as
`a44b9bdca7f4cbb65e54adaca721eb78c9695ac4`. [Main CI](https://github.com/JimChen-g/cofounder-os/actions/runs/36412668267)
passed Python 3.10/3.12 on the final 706-test snapshot. Spark received that exact
source, preserving Qwen identity and all production Runs. Both approved Runs
and the pending 61fe0f3a Run matched their pre-release snapshots exactly.

An isolated real Qwen Run `305a5521-fb47-461b-958a-05ff9e0c3dd9` passed initial
checks, accepted positioned feedback, passed revision 2 and remained pending.
Its two total attempts were exhausted; another feedback was rejected with
`repair_attempts_exhausted` and an unchanged full snapshot. No founder approval
or Feishu message was submitted. This is a new isolated validation, not a rewrite
of T27/T28 or a production user's Run.

The private correction restore verified 5,051 files and six SQLite databases,
with an offline authenticated API and approved export. Sixteen historical
candidate objects were additionally protected and restored from a Git bundle.
Credentials are excluded from this public repository. SSH key/known_hosts and
private local token access work at both local entry pages. Bridge configuration
and process environment exclude founder/provider keys; same-UID OS isolation
remains unimplemented. Full linked-worktree reexecution from the restore remains
unverified. Private receipt paths and hashes are in the owner's release report.
