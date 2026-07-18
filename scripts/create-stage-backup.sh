#!/bin/zsh
set -euo pipefail

# ---------------------------------------------------------------------------
# create-stage-backup.sh — create a complete stage recovery package
#
# Usage: create-stage-backup.sh <stage-id> <commit-sha>
#
# Creates a dated recovery package under:
#   /Users/jimcheng/Documents/CoFounderOS/stage-backups/<STAGE-ID>/<UTC timestamp>/
#
# The package contains:
#   cofounder-os.bundle      — complete Git bundle
#   source.tar.gz            — git archive of source tree
#   SHA256SUMS               — SHA-256 checksums
#   manifest.env             — stage metadata
#   changed-files.txt        — list of changed files
#   test-summary.txt         — test execution results
#   git-log.txt              — recent commit history
#   stage-report.txt         — stage report
# ---------------------------------------------------------------------------

usage() {
  cat >&2 <<EOF
Usage: $0 <stage-id> <commit-sha>

  stage-id    Stage identifier (e.g., D06-A, G01)
  commit-sha  Commit SHA the stage is based on
EOF
  exit 2
}

[[ $# -eq 2 ]] || usage

STAGE_ID="$1"
COMMIT_SHA="$2"
TIMESTAMP="$(date -u '+%Y%m%d-%H%M%SZ')"
BACKUP_ROOT="/Users/jimcheng/Documents/CoFounderOS/stage-backups"
BACKUP_DIR="$BACKUP_ROOT/$STAGE_ID/$TIMESTAMP"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

# Validate stage ID format
if [[ ! "$STAGE_ID" =~ ^[A-Z0-9]+(-[A-Z0-9]+)?$ ]]; then
  echo "ERROR: Invalid stage ID format: $STAGE_ID" >&2
  exit 1
fi

# Validate commit SHA format
if [[ ! "$COMMIT_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: Invalid commit SHA format: $COMMIT_SHA" >&2
  exit 1
fi

cd "$REPO"

# Require clean worktree
STATUS="$(git status --porcelain --untracked-files=all 2>/dev/null || true)"
if [[ -n "$STATUS" ]]; then
  echo "ERROR: Working tree is not clean — stage backup aborted" >&2
  echo "$STATUS" | sed 's/^/  /' >&2
  exit 1
fi

# Verify the commit exists
if ! git cat-file -e "$COMMIT_SHA^{commit}" 2>/dev/null; then
  echo "ERROR: Commit not found: $COMMIT_SHA" >&2
  exit 1
fi

LOCAL_HEAD="$(git rev-parse HEAD)"

# Create backup directory
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "=== Stage Backup ==="
echo "STAGE_ID=$STAGE_ID"
echo "COMMIT_SHA=$COMMIT_SHA"
echo "LOCAL_HEAD=$LOCAL_HEAD"
echo "BACKUP_DIR=$BACKUP_DIR"
echo

# 1. Complete Git bundle
echo "[1/9] Creating Git bundle..."
BUNDLE="$BACKUP_DIR/cofounder-os.bundle"
git bundle create "$BUNDLE" --all
git bundle verify "$BUNDLE" >/dev/null 2>&1 || {
  echo "ERROR: Bundle verification failed" >&2
  rm -rf "$BACKUP_DIR"
  exit 1
}
echo "  OK: $BUNDLE"

# 2. Source archive
echo "[2/9] Creating source archive..."
ARCHIVE="$BACKUP_DIR/source.tar.gz"
git -c tar.tarformat=oldgnu archive --format=tar.gz --prefix=cofounder-os/ "$COMMIT_SHA" > "$ARCHIVE"
echo "  OK: $ARCHIVE"

# 3. Generate changed-files.txt from the commit range
echo "[3/9] Generating changed-files.txt..."
CHANGED_FILES="$BACKUP_DIR/changed-files.txt"
PARENT="$(git rev-parse "$COMMIT_SHA^1" 2>/dev/null || echo "")"
if [[ -n "$PARENT" ]]; then
  git diff --name-status "$PARENT" "$COMMIT_SHA" > "$CHANGED_FILES" 2>/dev/null || true
else
  # Root commit — all files are new
  git diff-tree --no-commit-id --name-status -r "$COMMIT_SHA" > "$CHANGED_FILES" 2>/dev/null || true
fi
# Add descriptions for each changed file
{
  echo "# Changed files in $STAGE_ID (relative to $COMMIT_SHA)"
  echo "# Format: <status> <path> — <description>"
  echo
  while IFS= read -r line; do
    file_status="$(echo "$line" | awk '{print $1}')"
    path="$(echo "$line" | awk '{print $2}')"
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
mv "${CHANGED_FILES}.tmp" "$CHANGED_FILES"
echo "  OK: $CHANGED_FILES"

# 4. Git log
echo "[4/9] Generating git-log.txt..."
GIT_LOG="$BACKUP_DIR/git-log.txt"
git log --oneline --decorate -n 20 "$COMMIT_SHA" > "$GIT_LOG"
echo "  OK: $GIT_LOG"

# 5. Test summary (best-effort)
echo "[5/9] Generating test-summary.txt..."
TEST_SUMMARY="$BACKUP_DIR/test-summary.txt"
{
  echo "# Test Summary for $STAGE_ID"
  echo "# Generated at: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo
  echo "## Targeted Tests"
  echo "Command: pytest tests/ -x -q"
  if [[ -d "$REPO/tests" ]]; then
    if pytest tests/ -x -q 2>&1; then
      echo "Result: PASS"
    else
      echo "Result: FAIL (see pytest output above)"
    fi
  else
    echo "Result: SKIPPED (no tests directory)"
  fi
  echo
  echo "## Lint"
  echo "Command: ruff check app/ tests/"
  if command -v ruff >/dev/null 2>&1; then
    if ruff check app/ tests/ 2>&1; then
      echo "Result: PASS"
    else
      echo "Result: FAIL (see ruff output above)"
    fi
  else
    echo "Result: SKIPPED (ruff not installed)"
  fi
} > "$TEST_SUMMARY" 2>&1 || true
echo "  OK: $TEST_SUMMARY"

# 6. manifest.env
echo "[6/9] Generating manifest.env..."
MANIFEST="$BACKUP_DIR/manifest.env"
cat > "$MANIFEST" <<EOF
STAGE_ID=$STAGE_ID
STAGE_NAME=$STAGE_ID
BASELINE_COMMIT=$COMMIT_SHA
LOCAL_HEAD=$LOCAL_HEAD
DEPLOYED_HEAD=
DEPLOYED_AT=
DEPLOYMENT_RESULT=PENDING
EOF
chmod 600 "$MANIFEST"
echo "  OK: $MANIFEST"

# 7. Stage report
echo "[7/9] Generating stage-report.txt..."
STAGE_REPORT="$BACKUP_DIR/stage-report.txt"
cat > "$STAGE_REPORT" <<EOF
STAGE_ID: $STAGE_ID
STAGE_NAME: $STAGE_ID
BASELINE_COMMIT: $COMMIT_SHA
REPORT_GENERATED_AT: $(date -u '+%Y-%m-%dT%H:%M:%SZ')
REPORT_GENERATED_BY: create-stage-backup.sh
REPORT_VERSION: 1.0

FINAL_RESULT=PENDING
CHANGED_FILES=see changed-files.txt
BACKUP_PATH=$BACKUP_DIR
TEST_RESULT=see test-summary.txt
CURRENT_SERVICES=not yet validated
NEXT_ACTION=pending
EOF
echo "  OK: $STAGE_REPORT"

# 8. SHA-256 checksums
echo "[8/9] Generating SHA256SUMS..."
cd "$BACKUP_DIR"
shasum -a 256 cofounder-os.bundle source.tar.gz manifest.env changed-files.txt test-summary.txt git-log.txt stage-report.txt > SHA256SUMS
chmod 600 SHA256SUMS
echo "  OK: SHA256SUMS"

# 9. Verify bundle and checksums
echo "[9/9] Verifying..."
cd "$REPO"
git bundle verify "$BUNDLE" >/dev/null 2>&1 || {
  echo "ERROR: Bundle verification failed" >&2
  exit 1
}
cd "$BACKUP_DIR"
shasum -a 256 -c SHA256SUMS >/dev/null 2>&1 || {
  echo "ERROR: Checksum verification failed" >&2
  exit 1
}
echo "  OK: bundle and checksums verified"

echo
echo "=== Stage Backup Complete ==="
echo "BACKUP_DIR=$BACKUP_DIR"
echo
echo "RECOVERY_PACKAGE_CONTENTS="
ls -la "$BACKUP_DIR" | awk '{print "  " $9 " (" $5 " bytes)"}'
