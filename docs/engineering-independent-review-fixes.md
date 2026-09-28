# Engineering independent-review corrections

This patch addresses the engineering findings against `9bdf757`. Synthetic
regressions are not historical Run evidence, model quality evidence, or approval.
All application changes require a new Spark release; old candidate evidence and
frozen T27/T28 files remain unchanged.

| Finding | Change | Remaining limitation |
|---|---|---|
| F1 | Shared server feedback preflight reads the durable budget before mutation: two model calls, actual repair prompt charge plus prior reviewer charge and estimated output growth, and prior measured execution/review/test duration must fit. Missing model timing uses the client's configured timeout. The original 600-second effective window and total budget are unchanged. Accepted repairs that fail end with `repair_failed`, retaining the prior candidate/history. | Admission is a feasibility estimate, not a reservation or guarantee: new output size, model latency, queueing and runtime errors can differ. Review growth estimate is four UTF-8 bytes per repair output token. Dispatch still makes authoritative reservations. A previously failed legacy Run is not repaired retroactively. |
| F2 | Task/repair exhaustion raises a conflict without changing Run, Task, candidate, approval, receipts or feedback history. The pending candidate remains approvable. | No additional attempts are granted. |
| F3 | Persist repair intent in the Run before writing the Task; a crash between records is now visible to existing no-replay restart reconciliation. | **Partially addressed.** Engineering delivery still directly changes statuses outside the generic transition API; status-change audit coverage and multi-file atomicity are not solved. No claim of a single state authority for this path. |
| F4 | Current reviews reject comment/import citations and require both implementation and test files. | **Partially addressed.** These are quotation-hygiene checks, not semantic support. Same-model isolated-context review can still cite irrelevant executable lines. Locally generated session IDs only distinguish records, not independent reasoning. |
| F5 | Generated tests must reject two versioned known-wrong implementations: always return `{}` and always raise `ValueError`. A forged passing summary followed by exit 0 cannot satisfy these mutation checks. Collection errors/timeouts do not count as detected mutations. | **Partially addressed.** Two mutants measure limited test effectiveness; pytest summaries are still untrusted output and deliberately adaptive malicious tests could spoof failure too. No complete test-authenticity guarantee. |
| F6 | New Runs use separately versioned 21-case oracle, adding the seven reported categories; legacy Runs retain the 14-case oracle. Input snapshots and serializer methods are captured before candidate import, blocking the reviewed deepcopy and simple encoder monkeypatches. | **Partially addressed.** Candidate and observation code still share a Python process; arbitrary adversarial interpreter manipulation is not a secure isolation boundary. The original frozen oracle file/hash is unchanged. |
| F7 | Evidence JSON uses temp-file/fsync/replace, with directory fsync. New Run metadata owns explicit workspace pointers; readers never scan other Runs. Corrupt pointed evidence is logically quarantined and audited once, with original bytes preserved. | Legacy evidence without pointers is not scanned or silently reconstructed. This is individual-file atomicity, not a database transaction across artifacts. |
| F12 | A running engineering task without verified outputs fails explicitly with `engineering_recovery_not_supported` without another model call or consuming the next attempt. Verified outputs can still be reconciled. | No automatic model replay or automatic continuation of uncertain execution is supported. |
| F13 | Every new candidate receives `refs/cofounder/candidates/workspaces/<workspace-id>` immediately after commit creation. A regression creates an all-refs bundle, restores a mirror, and reads candidate source from it. | Existing candidates require operational object verification/ref migration before backup; this code does not reconstruct missing objects. Worktree retirement remains manual. |

Reviewer context contains compact gate summaries; full mutation subprocess logs
remain in evidence and are not copied into model prompts.

`tests/test_engineering_review_fixes.py` covers rejection without state changes,
corrupt unrelated/current evidence, atomic-write failure, bundle restore,
no-replay recovery, crash-visible repair intent, citation hygiene and adversarial
probes. `scripts/verify_engineering_review_fixes.py` runs actual Docker probes in
an isolated clone: positive 21-case oracle and generated tests with mutations,
forged passing output, and deepcopy hijack. It does not call models or product
APIs and never changes production Run state. Its root must not already exist.
