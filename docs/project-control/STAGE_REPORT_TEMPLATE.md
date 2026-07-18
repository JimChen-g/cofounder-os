# Stage Report Template

## Purpose

This template defines the mandatory structure for every stage delivery
report. All fields must be populated. Missing or empty fields are a
delivery blocker.

## Report Header

```text
STAGE_ID: <D## or G##>
STAGE_NAME: <human-readable name>
BASELINE_COMMIT: <SHA>
REPORT_GENERATED_AT: <ISO-8601 UTC timestamp>
REPORT_GENERATED_BY: <agent or session identifier>
REPORT_VERSION: 1.0
```

## Delivery Status

```text
FINAL_RESULT: PASS | FAIL | PARTIAL
```

## Changed Files

```text
CHANGED_FILES=
  <relative-path> (<new|modified|deleted> — <short description>)
  ...
```

## Backup Path

```text
BACKUP_PATH=<absolute path to stage recovery package>
```

## Test Result

```text
TEST_RESULT=
  Targeted: <PASS|FAIL — brief summary>
  Full suite: <PASS|FAIL — test count and duration>
  Lint: <PASS|FAIL>
  Diff check: <PASS|FAIL>
  Secret scan: <PASS|FAIL>
```

## Services

```text
CURRENT_SERVICES=
  <service-name>: <status> — <brief note>
  ...
```

## Three-Plane Verification

```text
LOCAL_HEAD=<SHA>
ORIGIN_MAIN_HEAD=<SHA>
SPARK_HEAD=<SHA>
THREE_PLANE_STATUS: EQUAL | MISMATCH
WORKTREE_STATUS: CLEAN | DIRTY
```

## Recovery Package Contents

```text
RECOVERY_PACKAGE_CONTENTS=
  cofounder-os.bundle — complete Git bundle
  source.tar.gz — git archive of source tree
  SHA256SUMS — integrity checksums
  manifest.env — stage metadata
  changed-files.txt — list of changed files with descriptions
  test-summary.txt — test execution results
  git-log.txt — recent commit history
  stage-report.txt — this report
```

## Secrets and Runtime Data Review

```text
SECRETS_REVIEW: CLEAN | FINDINGS
RUNTIME_DATA_REVIEW: CLEAN | FINDINGS
FINDINGS_DETAIL: <if FINDINGS, list each with file path and remediation>
```

## Next Action

```text
NEXT_ACTION=<next stage identifier and brief description>
```

## Independent Review

```text
REVIEW_STATUS: PENDING | ACCEPTED | REJECTED
REVIEW_NOTES: <reviewer comments or empty if pending>
```

## Corrective History Entry

If this is a corrective commit for a previous partial or rejected stage:

```text
CORRECTIVE_FOR: <previous commit SHA>
CORRECTIVE_REASON: <brief description of what was missing or incorrect>
```

## Full Delivery Report Block

The following block must appear verbatim at the end of every stage report:

```
FINAL_RESULT=<PASS|FAIL>
CHANGED_FILES=...
BACKUP_PATH=...
TEST_RESULT=...
CURRENT_SERVICES=...
NEXT_ACTION=...
```

## Usage

Copy this template into `<stage-id>-stage-report.md` in the stage recovery
package and in the commit message body when the stage is accepted.
