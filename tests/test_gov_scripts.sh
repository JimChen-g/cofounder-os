#!/bin/zsh
set -euo pipefail

# ---------------------------------------------------------------------------
# Test suite for G01 governance scripts
#
# Tests:
#   1. Isolated temporary-directory backup (happy path)
#   2. Dirty-worktree failure
#   3. Incorrect-commit failure
#   4. Three-plane mismatch failure
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKUP_SCRIPT="$REPO/scripts/create-stage-backup.sh"
PREFLIGHT_SCRIPT="$REPO/scripts/project-preflight.sh"
VERIFY_SCRIPT="$REPO/scripts/verify-three-plane.sh"

PASS_COUNT=0
FAIL_COUNT=0

pass() {
  echo "PASS: $1"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  echo "FAIL: $1" >&2
  FAIL_COUNT=$((FAIL_COUNT + 1))
}

# Test 1: Isolated temporary-directory backup (happy path)
echo "=== Test 1: Isolated backup (happy path) ==="
TMPDIR="$(mktemp -d)"
cd "$REPO"
if "$BACKUP_SCRIPT" "G01" "$(git rev-parse HEAD)" > "$TMPDIR/backup.log" 2>&1; then
  if [[ -f "$TMPDIR/backup.log" ]] && grep -q "BACKUP_DIR=" "$TMPDIR/backup.log"; then
    pass "isolated backup creates package"
  else
    fail "isolated backup — output missing BACKUP_DIR"
  fi
else
  fail "isolated backup — script exited non-zero"
fi
rm -rf "$TMPDIR"

# Test 2: Dirty-worktree failure
echo "=== Test 2: Dirty worktree failure ==="
TMPDIR="$(mktemp -d)"
cd "$REPO"
touch "$REPO/.dirty-test-file"
set +e
"$BACKUP_SCRIPT" "G01" "$(git rev-parse HEAD)" > "$TMPDIR/dirty.log" 2>&1
BACKUP_RC=$?
set -e
rm -f "$REPO/.dirty-test-file"
if [[ $BACKUP_RC -ne 0 ]] && grep -qi "not clean" "$TMPDIR/dirty.log"; then
  pass "dirty worktree rejected"
else
  fail "dirty worktree — expected rejection, got rc=$BACKUP_RC"
fi
rm -rf "$TMPDIR"

# Test 3: Incorrect-commit failure
echo "=== Test 3: Incorrect commit failure ==="
TMPDIR="$(mktemp -d)"
cd "$REPO"
set +e
"$BACKUP_SCRIPT" "G01" "0000000000000000000000000000000000000000" > "$TMPDIR/badcommit.log" 2>&1
COMMIT_RC=$?
set -e
if [[ $COMMIT_RC -ne 0 ]] && grep -qi "not found\|invalid" "$TMPDIR/badcommit.log"; then
  pass "invalid commit SHA rejected"
else
  fail "invalid commit — expected rejection, got rc=$COMMIT_RC"
fi
rm -rf "$TMPDIR"

# Test 4: Three-plane mismatch failure (simulated)
echo "=== Test 4: Three-plane mismatch (simulated) ==="
# We cannot easily simulate a real mismatch without modifying remote state,
# but we can verify the script parses correctly and produces structured output
cd "$REPO"
set +e
"$VERIFY_SCRIPT" > /tmp/verify-output.txt 2>&1
VERIFY_RC=$?
set -e
if [[ -f /tmp/verify-output.txt ]]; then
  if grep -q "THREE_PLANE_VERIFICATION" /tmp/verify-output.txt && \
     grep -q "LOCAL_HEAD=" /tmp/verify-output.txt && \
     grep -q "SPARK_HEAD=" /tmp/verify-output.txt; then
    pass "verify-three-plane produces structured output"
  else
    fail "verify-three-plane — missing expected output fields"
  fi
else
  fail "verify-three-plane — no output produced"
fi
rm -f /tmp/verify-output.txt

echo
echo "=== Test Summary ==="
echo "Passed: $PASS_COUNT"
echo "Failed: $FAIL_COUNT"

if [[ $FAIL_COUNT -gt 0 ]]; then
  exit 1
fi
