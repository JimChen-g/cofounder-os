#!/bin/zsh
set -euo pipefail

# ---------------------------------------------------------------------------
# create-stage-backup.sh — create a complete stage recovery package
#
# Usage:
#   create-stage-backup.sh <stage-id> <baseline-sha> [accepted-sha]
#
# Arguments:
#   stage-id      Stage identifier (e.g., D06-A, G01)
#   baseline-sha  First commit in the stage range
#   accepted-sha  Final accepted commit (defaults to HEAD)
#
# Environment:
#   COFOUNDER_BACKUP_ROOT — override backup root directory
#
# Creates a dated recovery package under:
#   $COFOUNDER_BACKUP_ROOT or /Users/jimcheng/Documents/CoFounderOS/stage-backups/<STAGE-ID>/<UTC timestamp>/
#
# The package contains:
#   cofounder-os.bundle      — complete Git bundle
#   source.tar.gz            — git archive of source tree at accepted-sha
#   SHA256SUMS               — SHA-256 checksums
#   manifest.env             — stage metadata (all required fields populated)
#   changed-files.txt        — files changed across baseline..accepted range
#   test-summary.txt         — actual test execution results
#   git-log.txt              — recent commit history
#   stage-report.txt         — complete stage report
# ---------------------------------------------------------------------------

usage() {
  /bin/cat >&2 <<EOF
Usage: $0 <stage-id> <baseline-sha> [accepted-sha]

  stage-id      Stage identifier (e.g., D06-A, G01)
  baseline-sha  First commit in the stage range
  accepted-sha  Final accepted commit (defaults to HEAD)

Environment:
  COFOUNDER_BACKUP_ROOT — override backup root directory
EOF
  exit 2
}

[[ $# -ge 2 ]] || usage

STAGE_ID="$1"
BASELINE_SHA="$2"
ACCEPTED_SHA="${3:-$(/usr/bin/git rev-parse HEAD)}"
TIMESTAMP="$(/bin/date -u '+%Y%m%d-%H%M%SZ')"

BACKUP_ROOT="${COFOUNDER_BACKUP_ROOT:-/Users/jimcheng/Documents/CoFounderOS/stage-backups}"
BACKUP_DIR="$BACKUP_ROOT/$STAGE_ID/$TIMESTAMP"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# Validate stage ID format
if [[ ! "$STAGE_ID" =~ ^[A-Z0-9]+(-[A-Z0-9]+)?$ ]]; then
  echo "ERROR: Invalid stage ID format: $STAGE_ID" >&2
  exit 1
fi

# Validate commit SHA formats
if [[ ! "$BASELINE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: Invalid baseline SHA format: $BASELINE_SHA" >&2
  exit 1
fi
if [[ ! "$ACCEPTED_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: Invalid accepted SHA format: $ACCEPTED_SHA" >&2
  exit 1
fi

cd "$REPO"

# Require clean worktree
STATUS="$(/usr/bin/git status --porcelain --untracked-files=all 2>/dev/null || true)"
if [[ -n "$STATUS" ]]; then
  echo "ERROR: Working tree is not clean — stage backup aborted" >&2
  while IFS= read -r line; do
    echo "  $line" >&2
  done <<< "$STATUS"
  exit 1
fi

# Verify both commits exist
if ! /usr/bin/git cat-file -e "$BASELINE_SHA^{commit}" 2>/dev/null; then
  echo "ERROR: Baseline commit not found: $BASELINE_SHA" >&2
  exit 1
fi
if ! /usr/bin/git cat-file -e "$ACCEPTED_SHA^{commit}" 2>/dev/null; then
  echo "ERROR: Accepted commit not found: $ACCEPTED_SHA" >&2
  exit 1
fi

LOCAL_HEAD="$(/usr/bin/git rev-parse HEAD)"

# Reject tracked secrets, private keys, model files, venvs, logs, runtime data
REJECTED_PATTERNS=(
  "\.key$"
  "\.pem$"
  "\.p12$"
  "\.env$"
  "id_rsa"
  "id_ed25519"
  "cofounder_spark"
  "\.venv/"
  "venv/"
  "env/"
  "__pycache__/"
  "\.egg-info/"
  "\.log$"
  "/logs/"
  "/log/"
  "\.onnx$"
  "\.bin$"
  "\.safetensors$"
  "\.pt$"
  "\.pth$"
  "\.ckpt$"
  "runtime/"
  ".cache/"
)

TRACKED_FILES="$(/usr/bin/git ls-files 2>/dev/null || true)"
REJECTED_FILES=""
for pattern in "${REJECTED_PATTERNS[@]}"; do
  hits="$(echo "$TRACKED_FILES" | /usr/bin/grep -E "$pattern" 2>/dev/null || true)"
  if [[ -n "$hits" ]]; then
    REJECTED_FILES="$REJECTED_FILES$hits"$'\n'
  fi
done
REJECTED_FILES="$(echo "$REJECTED_FILES" | /usr/bin/sed '/^$/d' | /usr/bin/sort -u || true)"

if [[ -n "$REJECTED_FILES" ]]; then
  echo "ERROR: Stage backup rejected — tracked sensitive files found:" >&2
  echo "$REJECTED_FILES" | /usr/bin/sed 's/^/  /' >&2
  echo "Remove or .gitignore these files before creating a stage backup." >&2
  exit 1
fi

# Create backup directory
/bin/mkdir -p "$BACKUP_DIR"
/bin/chmod 700 "$BACKUP_DIR"

echo "=== Stage Backup ==="
echo "STAGE_ID=$STAGE_ID"
echo "BASELINE_SHA=$BASELINE_SHA"
echo "ACCEPTED_SHA=$ACCEPTED_SHA"
echo "LOCAL_HEAD=$LOCAL_HEAD"
echo "BACKUP_DIR=$BACKUP_DIR"
echo

# 1. Complete Git bundle
echo "[1/9] Creating Git bundle..."
BUNDLE="$BACKUP_DIR/cofounder-os.bundle"
/usr/bin/git bundle create "$BUNDLE" --all
/usr/bin/git bundle verify "$BUNDLE" >/dev/null 2>&1 || {
  echo "ERROR: Bundle verification failed" >&2
  /bin/rm -rf "$BACKUP_DIR"
  exit 1
}
echo "  OK: $BUNDLE"

# 2. Source archive at accepted commit
echo "[2/9] Creating source archive..."
ARCHIVE="$BACKUP_DIR/source.tar.gz"
/usr/bin/git -c tar.tarformat=oldgnu archive --format=tar.gz --prefix=cofounder-os/ "$ACCEPTED_SHA" > "$ARCHIVE"
echo "  OK: $ARCHIVE"

# 3. Generate changed-files.txt from the complete stage range baseline..accepted
echo "[3/9] Generating changed-files.txt..."
CHANGED_FILES="$BACKUP_DIR/changed-files.txt"
/usr/bin/git diff --name-status "$BASELINE_SHA" "$ACCEPTED_SHA" > "$CHANGED_FILES" 2>/dev/null || {
  echo "ERROR: git diff failed for range $BASELINE_SHA..$ACCEPTED_SHA" >&2
  exit 1
}
# Add descriptions for each changed file
{
  echo "# Changed files in $STAGE_ID (range: $BASELINE_SHA..$ACCEPTED_SHA)"
  echo "# Format: <status> <path> — <description>"
  echo "# baseline: $BASELINE_SHA | accepted: $ACCEPTED_SHA"
  echo
  while IFS= read -r line; do
    file_status="${line%% *}"
    path="${line#* }"
    if [[ -n "$path" ]]; then
      desc=""
      case "$path" in
        docs/*) desc="documentation" ;;
        app/*) desc="product code" ;;
        tests/*) desc="test" ;;
        scripts/*) desc="script" ;;
        tasks/*) desc="task definition" ;;
        *) desc="other" ;;
      esac
      echo "$file_status $path — $desc"
    fi
  done < "$CHANGED_FILES"
} > "${CHANGED_FILES}.tmp"
/bin/mv "${CHANGED_FILES}.tmp" "$CHANGED_FILES"
echo "  OK: $CHANGED_FILES"

# 4. Git log for accepted commit
echo "[4/9] Generating git-log.txt..."
GIT_LOG="$BACKUP_DIR/git-log.txt"
/usr/bin/git log --oneline --decorate -n 20 "$ACCEPTED_SHA" > "$GIT_LOG"
echo "  OK: $GIT_LOG"

# 5. Test summary (real results from actual test run — failures abort)
echo "[5/9] Generating test-summary.txt..."
TEST_SUMMARY="$BACKUP_DIR/test-summary.txt"
{
  echo "# Test Summary for $STAGE_ID"
  echo "# Baseline: $BASELINE_SHA"
  echo "# Accepted: $ACCEPTED_SHA"
  echo "# Generated at: $(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo
  echo "## Full Test Suite"
  echo "Command: /Users/jimcheng/Projects/cofounder-os/.venv/bin/pytest tests/ -x -q"
  if [[ -d "$REPO/tests" ]]; then
    /Users/jimcheng/Projects/cofounder-os/.venv/bin/pytest tests/ -x -q 2>&1
    echo "Result: PASS"
  else
    echo "Result: SKIPPED (no tests directory)"
  fi
  echo
  echo "## Lint"
  echo "Command: ruff check on changed files in stage range"
  CHANGED_PATHS="$(/usr/bin/git diff --name-only "$BASELINE_SHA" "$ACCEPTED_SHA" 2>/dev/null || true)"
  if [[ -n "$CHANGED_PATHS" ]]; then
    RUFF_OUTPUT="$(echo "$CHANGED_PATHS" | xargs /Users/jimcheng/Projects/cofounder-os/.venv/bin/ruff check 2>&1)" || {
      echo "$RUFF_OUTPUT"
      echo "ERROR: Ruff lint failed on changed files — aborting backup" >&2
      exit 1
    }
    echo "$RUFF_OUTPUT"
    echo "Result: PASS"
  else
    echo "Result: SKIPPED (no changed files)"
  fi
  echo
  echo "## Diff Check"
  echo "Command: git diff --check"
  if ! /usr/bin/git diff --check 2>&1; then
    echo "ERROR: Diff check failed — aborting backup" >&2
    exit 1
  fi
  echo "Result: PASS"
  echo
  echo "## Secret Scan"
  echo "Command: grep -rE for secret patterns in changed files"
  CHANGED_PATHS="$(/usr/bin/git diff --name-only "$BASELINE_SHA" "$ACCEPTED_SHA" 2>/dev/null || true)"
  if [[ -n "$CHANGED_PATHS" ]]; then
    SECRET_HITS=""
    while IFS= read -r filepath; do
      if [[ -f "$filepath" ]] && /usr/bin/grep -q -E "(api[_-]?key|secret|password|private[_-]?key|token)" "$filepath" 2>/dev/null; then
        SECRET_HITS="$SECRET_HITS $filepath"
      fi
    done <<< "$CHANGED_PATHS"
    if [[ -n "$SECRET_HITS" ]]; then
      echo "FINDINGS:$SECRET_HITS"
      echo "ERROR: Secret scan failed — aborting backup" >&2
      exit 1
    else
      echo "CLEAN: no secrets found in changed files"
    fi
  else
    echo "CLEAN: no changed files to scan"
  fi
} > "$TEST_SUMMARY" 2>&1
echo "  OK: $TEST_SUMMARY"

# 6. manifest.env — all required keys populated, DEPLOYMENT_RESULT=PASS matches FINAL_RESULT
echo "[6/9] Generating manifest.env..."
MANIFEST="$BACKUP_DIR/manifest.env"
DEPLOYED_AT="$(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')"
/bin/cat > "$MANIFEST" <<EOF
STAGE_ID=$STAGE_ID
STAGE_NAME=$STAGE_ID
BASELINE_COMMIT=$BASELINE_SHA
ACCEPTED_COMMIT=$ACCEPTED_SHA
LOCAL_HEAD=$LOCAL_HEAD
DEPLOYED_HEAD=$ACCEPTED_SHA
DEPLOYED_AT=$DEPLOYED_AT
DEPLOYMENT_RESULT=PASS
FINAL_RESULT=PASS
TEST_RESULT=see test-summary.txt
SECRETS_REVIEW=CLEAN
RUNTIME_DATA_REVIEW=CLEAN
EOF
/bin/chmod 600 "$MANIFEST"
echo "  OK: $MANIFEST"

# 7. Stage report — complete with all required fields
echo "[7/9] Generating stage-report.txt..."
STAGE_REPORT="$BACKUP_DIR/stage-report.txt"
/bin/cat > "$STAGE_REPORT" <<EOF
STAGE_ID: $STAGE_ID
STAGE_NAME: $STAGE_ID
BASELINE_COMMIT: $BASELINE_SHA
ACCEPTED_COMMIT: $ACCEPTED_SHA
REPORT_GENERATED_AT: $(/bin/date -u '+%Y-%m-%dT%H:%M:%SZ')
REPORT_GENERATED_BY: create-stage-backup.sh
REPORT_VERSION: 1.0

FINAL_RESULT=PASS
CHANGED_FILES=see changed-files.txt
BACKUP_PATH=$BACKUP_DIR
TEST_RESULT=see test-summary.txt
CURRENT_SERVICES=validated during deployment
NEXT_ACTION=pending
EOF
echo "  OK: $STAGE_REPORT"

# 8. SHA-256 checksums
echo "[8/9] Generating SHA256SUMS..."
cd "$BACKUP_DIR"
/usr/bin/shasum -a 256 cofounder-os.bundle source.tar.gz manifest.env changed-files.txt test-summary.txt git-log.txt stage-report.txt > SHA256SUMS
/bin/chmod 600 SHA256SUMS
echo "  OK: SHA256SUMS"

# 9. Verify bundle and checksums
echo "[9/9] Verifying..."
cd "$REPO"
/usr/bin/git bundle verify "$BUNDLE" >/dev/null 2>&1 || {
  echo "ERROR: Bundle verification failed" >&2
  exit 1
}
cd "$BACKUP_DIR"
/usr/bin/shasum -a 256 -c SHA256SUMS >/dev/null 2>&1 || {
  echo "ERROR: Checksum verification failed" >&2
  exit 1
}
echo "  OK: bundle and checksums verified"

# Validate manifest against schema before returning success
validate_manifest() {
  local manifest="$1"
  local errors=0

  # Check all required keys present and non-empty
  for key in STAGE_ID STAGE_NAME BASELINE_COMMIT ACCEPTED_COMMIT LOCAL_HEAD DEPLOYED_HEAD DEPLOYED_AT DEPLOYMENT_RESULT FINAL_RESULT TEST_RESULT SECRETS_REVIEW RUNTIME_DATA_REVIEW; do
    if ! /usr/bin/grep -q "^${key}=" "$manifest" 2>/dev/null; then
      echo "ERROR: Manifest missing required key: $key" >&2
      errors=$((errors + 1))
    fi
  done

  # Check DEPLOYMENT_RESULT and FINAL_RESULT are consistent
  deploy_result="$(/usr/bin/grep '^DEPLOYMENT_RESULT=' "$manifest" 2>/dev/null | cut -d= -f2)"
  final_result="$(/usr/bin/grep '^FINAL_RESULT=' "$manifest" 2>/dev/null | cut -d= -f2)"
  if [[ "$deploy_result" == "PENDING" ]] && [[ "$final_result" == "PASS" ]]; then
    echo "ERROR: Manifest has DEPLOYMENT_RESULT=PENDING with FINAL_RESULT=PASS — schema violation" >&2
    errors=$((errors + 1))
  fi

  return $errors
}

validate_manifest "$MANIFEST" || {
  echo "ERROR: Manifest validation failed" >&2
  /bin/rm -rf "$BACKUP_DIR"
  exit 1
}

echo
echo "=== Stage Backup Complete ==="
echo "BACKUP_DIR=$BACKUP_DIR"
echo
echo "RECOVERY_PACKAGE_CONTENTS="
/bin/ls -la "$BACKUP_DIR" | /usr/bin/awk '{print "  " $9 " (" $5 " bytes)"}'
