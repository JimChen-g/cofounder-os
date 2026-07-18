#!/bin/zsh
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ---------------------------------------------------------------------------
# Test suite for G01 governance scripts
#
# Tests:
#   1. Isolated temporary-directory backup (happy path)
#   2. Dirty-worktree failure
#   3. Incorrect-commit failure
#   4. Simulated three-plane mismatch (dependency injection)
#   5. Missing local cofounderctl is a failure
#   6. Worktree state reported correctly in preflight
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
echo "=== Test 1: Isolated backup with COFOUNDER_BACKUP_ROOT ==="
TMPDIR="$(/usr/bin/mktemp -d)"
cd "$REPO"
BACKUP_LOG="$TMPDIR/backup.log"
COFOUNDER_BACKUP_ROOT="$TMPDIR/backups" "$BACKUP_SCRIPT" "G01-TEST" "$(git rev-parse HEAD)" > "$BACKUP_LOG" 2>&1
BACKUP_RC=$?
if [[ $BACKUP_RC -eq 0 ]] && [[ -f "$BACKUP_LOG" ]] && grep -q "BACKUP_DIR=" "$BACKUP_LOG"; then
  # Verify it went to the temp directory, not the real stage-backups
  BACKUP_DIR_LINE="$(grep "BACKUP_DIR=" "$BACKUP_LOG" | /usr/bin/tail -1)"
  if echo "$BACKUP_DIR_LINE" | /usr/usr/bin/grep -q "$TMPDIR"; then
    pass "isolated backup creates package in temp directory"
  else
    fail "isolated backup — package not in temp directory: $BACKUP_DIR_LINE"
  fi
  # Verify manifest has all required keys
  BACKUP_DIR="$(echo "$BACKUP_DIR_LINE" | /usr/usr/bin/sed 's/BACKUP_DIR=//')"
  if [[ -f "$BACKUP_DIR/manifest.env" ]]; then
    missing=""
    for key in STAGE_ID BASELINE_COMMIT ACCEPTED_COMMIT LOCAL_HEAD DEPLOYED_HEAD DEPLOYED_AT DEPLOYMENT_RESULT FINAL_RESULT TEST_RESULT SECRETS_REVIEW RUNTIME_DATA_REVIEW; do
      if ! /usr/bin/grep -q "^${key}=" "$BACKUP_DIR/manifest.env"; then
        missing="$missing $key"
      fi
    done
    if [[ -z "$missing" ]]; then
      pass "manifest.env has all required keys"
    else
      fail "manifest.env missing required keys:$missing"
    fi
    # Verify FINAL_RESULT is not PENDING
    if /usr/bin/grep -q "^FINAL_RESULT=PASS" "$BACKUP_DIR/manifest.env"; then
      pass "manifest.env FINAL_RESULT is PASS (not PENDING)"
    else
      fail "manifest.env FINAL_RESULT is not PASS"
    fi
  else
    fail "manifest.env not created"
  fi
  # Verify changed-files.txt covers the range (not just parent)
  if [[ -f "$BACKUP_DIR/changed-files.txt" ]]; then
    if /usr/bin/grep -q "baseline.*accepted" "$BACKUP_DIR/changed-files.txt"; then
      pass "changed-files.txt covers baseline..accepted range"
    else
      fail "changed-files.txt missing range annotation"
    fi
  fi
else
  fail "isolated backup — script exited rc=$BACKUP_RC"
fi
/bin/rm -rf "$TMPDIR"

# Test 2: Dirty-worktree failure
echo "=== Test 2: Dirty worktree failure ==="
TMPDIR="$(/usr/bin/mktemp -d)"
cd "$REPO"
touch "$REPO/.dirty-test-file"
set +e
COFOUNDER_BACKUP_ROOT="$TMPDIR/backups" "$BACKUP_SCRIPT" "G01-TEST" "$(git rev-parse HEAD)" > "$TMPDIR/dirty.log" 2>&1
BACKUP_RC=$?
set -e
/bin/rm -f "$REPO/.dirty-test-file"
if [[ $BACKUP_RC -ne 0 ]] && grep -qi "not clean" "$TMPDIR/dirty.log"; then
  pass "dirty worktree rejected"
else
  fail "dirty worktree — expected rejection, got rc=$BACKUP_RC"
fi
/bin/rm -rf "$TMPDIR"

# Test 3: Incorrect-commit failure
echo "=== Test 3: Incorrect commit failure ==="
TMPDIR="$(/usr/bin/mktemp -d)"
cd "$REPO"
set +e
COFOUNDER_BACKUP_ROOT="$TMPDIR/backups" "$BACKUP_SCRIPT" "G01-TEST" "0000000000000000000000000000000000000000" > "$TMPDIR/badcommit.log" 2>&1
COMMIT_RC=$?
set -e
if [[ $COMMIT_RC -ne 0 ]] && grep -qi "not found\|invalid" "$TMPDIR/badcommit.log"; then
  pass "invalid commit SHA rejected"
else
  fail "invalid commit — expected rejection, got rc=$COMMIT_RC"
fi
/bin/rm -rf "$TMPDIR"

# Test 4: Simulated three-plane mismatch (dependency injection)
echo "=== Test 4: Simulated three-plane mismatch ==="
cd "$REPO"
LOCAL_H="$(git rev-parse HEAD)"
set +e
COFOUNDER_TEST_LOCAL_HEAD="$LOCAL_H" \
  COFOUNDER_TEST_ORIGIN_HEAD="$LOCAL_H" \
  COFOUNDER_TEST_SPARK_HEAD="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
  "$VERIFY_SCRIPT" > /tmp/verify-mismatch.txt 2>&1
MISMATCH_RC=$?
set -e
if [[ $MISMATCH_RC -ne 0 ]]; then
  if grep -q "THREE_PLANE_STATUS=MISMATCH" /tmp/verify-mismatch.txt && \
     grep -q "FINAL_RESULT=FAIL" /tmp/verify-mismatch.txt; then
    pass "simulated mismatch returns FAIL with MISMATCH status"
  else
    fail "mismatch — wrong output"
    /bin/cat /tmp/verify-mismatch.txt >&2
  fi
else
  fail "mismatch — expected non-zero exit, got rc=0"
fi
/bin/rm -f /tmp/verify-mismatch.txt

# Test 4b: Simulated three-plane equality (all three match)
echo "=== Test 4b: Simulated three-plane equality ==="
cd "$REPO"
LOCAL_H="$(git rev-parse HEAD)"
set +e
COFOUNDER_TEST_LOCAL_HEAD="$LOCAL_H" \
  COFOUNDER_TEST_ORIGIN_HEAD="$LOCAL_H" \
  COFOUNDER_TEST_SPARK_HEAD="$LOCAL_H" \
  "$VERIFY_SCRIPT" > /tmp/verify-equal.txt 2>&1
EQUAL_RC=$?
set -e
# This may still fail due to service checks, but THREE_PLANE_STATUS should be EQUAL
if grep -q "THREE_PLANE_STATUS=EQUAL" /tmp/verify-equal.txt 2>/dev/null; then
  pass "simulated equality shows THREE_PLANE_STATUS=EQUAL"
else
  fail "equality — THREE_PLANE_STATUS not EQUAL in output"
  /bin/cat /tmp/verify-equal.txt >&2
fi
/bin/rm -f /tmp/verify-equal.txt

# Test 5: Missing local cofounderctl is a failure (not silent skip)
echo "=== Test 5: Missing local cofounderctl ==="
cd "$REPO"
FAKE_COFOUNDERCTL="$(/usr/bin/mktemp -d)/fake-cofounderctl"
set +e
HOME="$(/usr/bin/mktemp -d)" COFOUNDER_TEST_LOCAL_HEAD="$(git rev-parse HEAD)" \
  COFOUNDER_TEST_ORIGIN_HEAD="$(git rev-parse HEAD)" \
  COFOUNDER_TEST_SPARK_HEAD="$(git rev-parse HEAD)" \
  "$VERIFY_SCRIPT" > /tmp/verify-nococtl.txt 2>&1
NOCOCTL_RC=$?
set -e
if [[ $NOCOCTL_RC -ne 0 ]] && grep -qi "not found\|cannot proceed" /tmp/verify-nococtl.txt; then
  pass "missing cofounderctl is a failure"
else
  fail "missing cofounderctl — expected failure, got rc=$NOCOCTL_RC"
  /bin/cat /tmp/verify-nococtl.txt >&2
fi
/bin/rm -f /tmp/verify-nococtl.txt
/bin/rm -rf "$(dirname "$FAKE_COFOUNDERCTL")"

# Test 6: Worktree state in preflight output
echo "=== Test 6: Preflight worktree state reporting ==="
cd "$REPO"
touch "$REPO/.preflight-dirty-test"
PREFLIGHT_OUTPUT="$(/usr/bin/mktemp)"
set +e
"$PREFLIGHT_SCRIPT" > "$PREFLIGHT_OUTPUT" 2>&1
PREFLIGHT_RC=$?
set -e
/bin/rm -f "$REPO/.preflight-dirty-test"
if grep -q "WORKTREE_STATUS=DIRTY" "$PREFLIGHT_OUTPUT" && \
   grep -q "WORKTREE=DIRTY" "$PREFLIGHT_OUTPUT" && \
   [[ $PREFLIGHT_RC -ne 0 ]]; then
  pass "preflight reports dirty worktree correctly"
else
  fail "preflight worktree reporting incorrect"
  /bin/cat "$PREFLIGHT_OUTPUT" >&2
fi
/bin/rm -f "$PREFLIGHT_OUTPUT"

echo
echo "=== Test Summary ==="
echo "Passed: $PASS_COUNT"
echo "Failed: $FAIL_COUNT"

if [[ $FAIL_COUNT -gt 0 ]]; then
  exit 1
fi
