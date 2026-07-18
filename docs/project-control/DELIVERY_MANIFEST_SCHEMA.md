# Delivery Manifest Schema

## Purpose

This document defines the schema for all manifest files produced during
stage backup creation and deployment. All manifest files must conform to
these schemas. Parsers must reject non-conforming manifests.

## manifest.env

Key-value pairs in shell-compatible format. Used by backup and deployment
scripts to record stage metadata.

### Required Keys

| Key | Format | Description |
|-----|--------|-------------|
| `STAGE_ID` | `[A-Z0-9]+-[A-Z0-9]+` | Stage identifier (e.g., `D06-A`, `G01`) |
| `STAGE_NAME` | string | Human-readable stage name |
| `BASELINE_COMMIT` | 40-character hex SHA | Commit SHA the stage builds from |
| `DEPLOYED_HEAD` | 40-character hex SHA | HEAD after deployment |
| `DEPLOYED_AT` | ISO-8601 UTC | Deployment timestamp |
| `DEPLOYMENT_RESULT` | `PASS\|FAIL` | Final deployment outcome |

### Optional Keys

| Key | Format | Description |
|-----|--------|-------------|
| `PREVIOUS_HEAD` | 40-character hex SHA | HEAD before deployment |
| `CORRECTIVE_FOR` | 40-character hex SHA | If corrective, the commit being corrected |
| `CORRECTIVE_REASON` | string | Reason for corrective commit |
| `RECOVERY_PACKAGE_PATH` | absolute path | Path to recovery package |
| `THREE_PLANE_STATUS` | `EQUAL\|MISMATCH` | Three-plane verification result |
| `TEST_RESULT` | string | Test execution summary |
| `SECRETS_REVIEW` | `CLEAN\|FINDINGS` | Secret scan result |
| `RUNTIME_DATA_REVIEW` | `CLEAN\|FINDINGS` | Runtime data scan result |

### Validation Rules

- All required keys must be present.
- SHA values must be exactly 40 lowercase hexadecimal characters.
- `DEPLOYED_AT` must match `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`.
- `DEPLOYMENT_RESULT` must be `PASS` or `FAIL`.
- No value may contain embedded newlines.

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

## changed-files.txt

One changed file per line. Format:

```
<status> <relative-path> — <short description>
```

Where `<status>` is one of: `new`, `modified`, `deleted`.

## test-summary.txt

Free-form text containing:

- Targeted test command and result
- Full test suite command, result, pass/fail counts, and duration
- Lint command and result
- Diff check command and result
- Secret scan command and result

## git-log.txt

Output of:

```text
git log --oneline --decorate -n 20
```

For the stage commit range.
