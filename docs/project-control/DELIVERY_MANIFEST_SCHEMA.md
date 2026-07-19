# Delivery Manifest Schema

## Purpose

This document defines the schema for all manifest files produced during
stage backup creation and deployment. All manifest files must conform to
these schemas. Parsers must reject non-conforming manifests. A manifest
must not violate its own schema — all required keys must carry real values
before the package is considered accepted.

## manifest.env

Key-value pairs in shell-compatible format. Used by backup and deployment
scripts to record stage metadata.

### Required Keys

| Key | Format | Description |
|-----|--------|-------------|
| `STAGE_ID` | `[A-Z0-9]+(-[A-Z0-9]+)?` | Stage identifier (e.g., `D06-A`, `G01`) |
| `STAGE_NAME` | string | Human-readable stage name |
| `BASELINE_COMMIT` | 40-character hex SHA | First commit in the stage range |
| `ACCEPTED_COMMIT` | 40-character hex SHA | Final accepted commit for the stage |
| `LOCAL_HEAD` | 40-character hex SHA | Local HEAD when backup was created |
| `DEPLOYED_HEAD` | 40-character hex SHA | HEAD after deployment |
| `DEPLOYED_AT` | ISO-8601 UTC | Deployment timestamp |
| `DEPLOYMENT_RESULT` | `PASS\|FAIL` | Final deployment outcome |
| `FINAL_RESULT` | `PASS\|FAIL` | Overall stage result |
| `TEST_RESULT` | string | Test execution summary |
| `SECRETS_REVIEW` | `CLEAN\|FINDINGS` | Secret scan result |
| `RUNTIME_DATA_REVIEW` | `CLEAN\|FINDINGS` | Runtime data scan result |

### Optional Keys

| Key | Format | Description |
|-----|--------|-------------|
| `PREVIOUS_HEAD` | 40-character hex SHA | HEAD before deployment |
| `CORRECTIVE_FOR` | 40-character hex SHA | If corrective, the commit being corrected |
| `CORRECTIVE_REASON` | string | Reason for corrective commit |
| `RECOVERY_PACKAGE_PATH` | absolute path | Path to recovery package |
| `THREE_PLANE_STATUS` | `EQUAL\|MISMATCH` | Three-plane verification result |
| `ACCEPTED_TAG` | `g##-accepted` | Annotated tag marking stage acceptance |
| `CHANGED_FILES_COUNT` | integer | Number of changed files in the stage |

### Validation Rules

- All required keys must be present and non-empty.
- SHA values must be exactly 40 lowercase hexadecimal characters.
- `DEPLOYED_AT` must match `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`.
- `DEPLOYMENT_RESULT` and `FINAL_RESULT` must be `PASS` or `FAIL`.
- `SECRETS_REVIEW` and `RUNTIME_DATA_REVIEW` must be `CLEAN` or `FINDINGS`.
- No value may contain embedded newlines.
- An accepted package (FINAL_RESULT=PASS) must have DEPLOYED_HEAD,
  DEPLOYED_AT, DEPLOYMENT_RESULT, TEST_RESULT, SECRETS_REVIEW, and
  RUNTIME_DATA_REVIEW all populated — not empty.
- **DEPLOYMENT_RESULT=PENDING and FINAL_RESULT=PASS is a schema violation**.
  A successful backup must have DEPLOYMENT_RESULT=PASS.
- The manifest must be validated against all rules before the backup script
  returns success. Any validation failure must abort the backup.

### Explicitly Rejected Content

A manifest must not contain entries for:
- Tracked secrets, private keys, or API credentials
- Model weight files or binary model artifacts
- Virtual environment directories (`.venv/`, `venv/`, `env/`)
- Log files and runtime data (`*.log`, runtime caches)
- Generated artifacts not tracked in Git

## SHA256SUMS

Standard BSD-style checksum file. One line per file with format:

```
<sha256-hex>  <filename>
```

### Validation Rules

- Every file in the recovery package (except `SHA256SUMS` itself) must
  have a corresponding entry.
- No extra entries for files not present in the package.
- Checksums must be verified with `shasum -a 256 -c`.

## stage-report.txt

Free-form text report following the structure defined in
`STAGE_REPORT_TEMPLATE.md`. Must contain the mandatory delivery report
block at the end:

```
FINAL_RESULT=<PASS|FAIL>
CHANGED_FILES=...
BACKUP_PATH=...
TEST_RESULT=...
CURRENT_SERVICES=...
NEXT_ACTION=...
```

An accepted stage report must have `FINAL_RESULT=PASS` and populated
TEST_RESULT and BACKUP_PATH fields.

## changed-files.txt

One changed file per line. Format:

```
<status> <relative-path> — <short description>
```

Where `<status>` is one of: `new`, `modified`, `deleted`.

For multi-commit stages, this file covers the full range
`baseline_sha..accepted_sha`.

## test-summary.txt

Free-form text containing:
- Targeted test command and result (exact command used)
- Full test suite command, result, pass/fail counts, and duration
- Lint command and result
- Diff check command and result
- Secret scan command and result

For accepted packages, this file must contain real results, not `PENDING`
or `SKIPPED` placeholders.

## git-log.txt

Output of:

```text
git log --oneline --decorate -n 20 <accepted_commit>
```

For the stage commit range. Must show the accepted commit at the top.
