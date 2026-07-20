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
DEPLOY_SCRIPT="$REPO/scripts/deploy-to-spark.sh"

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
TEST_TMPDIR="$(/usr/bin/mktemp -d /tmp/cofounder-gov.XXXXXX)"
cd "$REPO"
BACKUP_LOG="$TEST_TMPDIR/backup.log"
COFOUNDER_BACKUP_ROOT="$TEST_TMPDIR/backups" \
  COFOUNDER_REVIEW_STATUS=PASS \
  "$BACKUP_SCRIPT" "G01-TEST" "$(git rev-parse HEAD)" > "$BACKUP_LOG" 2>&1
BACKUP_RC=$?
if [[ $BACKUP_RC -eq 0 ]] && [[ -f "$BACKUP_LOG" ]] && grep -q "BACKUP_DIR=" "$BACKUP_LOG"; then
  # Verify it went to the temp directory, not the real stage-backups
  BACKUP_DIR_LINE="$(grep "BACKUP_DIR=" "$BACKUP_LOG" | /usr/bin/tail -1)"
  if echo "$BACKUP_DIR_LINE" | /usr/bin/grep -Fq "$TEST_TMPDIR"; then
    pass "isolated backup creates package in temp directory"
  else
    fail "isolated backup — package not in temp directory: $BACKUP_DIR_LINE"
  fi
  # Verify manifest has all required keys
  BACKUP_DIR="$(echo "$BACKUP_DIR_LINE" | /usr/bin/sed 's/BACKUP_DIR=//')"
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
  if /usr/bin/grep -q '^REVIEW_STATUS=PASS$' \
    "$BACKUP_DIR/stage-report.txt"; then
    pass "stage report records the supplied independent review result"
  else
    fail "stage report does not record REVIEW_STATUS=PASS"
  fi
else
  fail "isolated backup — script exited rc=$BACKUP_RC"
fi
/bin/rm -rf "$TEST_TMPDIR"

# Test 2: Dirty-worktree failure
echo "=== Test 2: Dirty worktree failure ==="
TEST_TMPDIR="$(/usr/bin/mktemp -d /tmp/cofounder-gov.XXXXXX)"
cd "$REPO"
touch "$REPO/.dirty-test-file"
set +e
COFOUNDER_BACKUP_ROOT="$TEST_TMPDIR/backups" "$BACKUP_SCRIPT" "G01-TEST" "$(git rev-parse HEAD)" > "$TEST_TMPDIR/dirty.log" 2>&1
BACKUP_RC=$?
set -e
/bin/rm -f "$REPO/.dirty-test-file"
if [[ $BACKUP_RC -ne 0 ]] && grep -qi "not clean" "$TEST_TMPDIR/dirty.log"; then
  pass "dirty worktree rejected"
else
  fail "dirty worktree — expected rejection, got rc=$BACKUP_RC"
fi
/bin/rm -rf "$TEST_TMPDIR"

# Test 3: Incorrect-commit failure
echo "=== Test 3: Incorrect commit failure ==="
TEST_TMPDIR="$(/usr/bin/mktemp -d /tmp/cofounder-gov.XXXXXX)"
cd "$REPO"
set +e
COFOUNDER_BACKUP_ROOT="$TEST_TMPDIR/backups" "$BACKUP_SCRIPT" "G01-TEST" "0000000000000000000000000000000000000000" > "$TEST_TMPDIR/badcommit.log" 2>&1
COMMIT_RC=$?
set -e
if [[ $COMMIT_RC -ne 0 ]] && grep -qi "not found\|invalid" "$TEST_TMPDIR/badcommit.log"; then
  pass "invalid commit SHA rejected"
else
  fail "invalid commit — expected rejection, got rc=$COMMIT_RC"
fi
/bin/rm -rf "$TEST_TMPDIR"

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
TEST_HOME="$(/usr/bin/mktemp -d /tmp/cofounder-home.XXXXXX)"
set +e
HOME="$TEST_HOME" COFOUNDER_TEST_LOCAL_HEAD="$(git rev-parse HEAD)" \
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
/bin/rm -rf "$TEST_HOME"

# Test 6: Worktree state in preflight output
echo "=== Test 6: Preflight worktree state reporting ==="
cd "$REPO"
touch "$REPO/.preflight-dirty-test"
PREFLIGHT_OUTPUT="$(/usr/bin/mktemp /tmp/cofounder-preflight.XXXXXX)"
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

# Test 7: PASS package cannot contain DEPLOYMENT_RESULT=PENDING
echo "=== Test 7: No PASS+PENDING in manifest ==="
TEST_TMPDIR="$(/usr/bin/mktemp -d /tmp/cofounder-gov.XXXXXX)"
cd "$REPO"
BACKUP_LOG="$TEST_TMPDIR/integrity.log"
if COFOUNDER_BACKUP_ROOT="$TEST_TMPDIR/backups" "$BACKUP_SCRIPT" "G01-INT" "$(git rev-parse HEAD)" > "$BACKUP_LOG" 2>&1; then
  BACKUP_DIR_LINE="$(grep "BACKUP_DIR=" "$BACKUP_LOG" | /usr/bin/tail -1)"
  BACKUP_DIR="$(echo "$BACKUP_DIR_LINE" | /usr/bin/sed 's/BACKUP_DIR=//')"
  if [[ -f "$BACKUP_DIR/manifest.env" ]]; then
    deploy_result="$(/usr/bin/grep '^DEPLOYMENT_RESULT=' "$BACKUP_DIR/manifest.env" | /usr/bin/cut -d= -f2)"
    final_result="$(/usr/bin/grep '^FINAL_RESULT=' "$BACKUP_DIR/manifest.env" | /usr/bin/cut -d= -f2)"
    if [[ "$final_result" == "PASS" ]] && [[ "$deploy_result" == "PENDING" ]]; then
      fail "manifest has FINAL_RESULT=PASS with DEPLOYMENT_RESULT=PENDING"
    else
      pass "manifest DEPLOYMENT_RESULT is consistent with FINAL_RESULT"
    fi
  else
    fail "manifest.env not found for integrity check"
  fi
else
  fail "backup script failed — cannot check integrity"
fi
/bin/rm -rf "$TEST_TMPDIR"

# Test 8: No failure-swallowing || true in critical paths
echo "=== Test 8: No || true in critical test/validation paths ==="
FAIL_SWALLOW="$(/usr/bin/grep -n '|| true' "$BACKUP_SCRIPT" | grep -E 'pytest|ruff|diff --check|secret|bundle verify|shasum -c' || true)"
if [[ -z "$FAIL_SWALLOW" ]]; then
  pass "no || true in critical validation paths"
else
  fail "|| true found in critical paths: $FAIL_SWALLOW"
fi

# Test 9: Accepted package manifest conforms to schema
echo "=== Test 9: Manifest schema validation ==="
TEST_TMPDIR="$(/usr/bin/mktemp -d /tmp/cofounder-gov.XXXXXX)"
cd "$REPO"
BACKUP_LOG="$TEST_TMPDIR/schema.log"
if COFOUNDER_BACKUP_ROOT="$TEST_TMPDIR/backups" "$BACKUP_SCRIPT" "G01-SCH" "$(git rev-parse HEAD)" > "$BACKUP_LOG" 2>&1; then
  BACKUP_DIR_LINE="$(grep "BACKUP_DIR=" "$BACKUP_LOG" | /usr/bin/tail -1)"
  BACKUP_DIR="$(echo "$BACKUP_DIR_LINE" | /usr/bin/sed 's/BACKUP_DIR=//')"
  if [[ -f "$BACKUP_DIR/manifest.env" ]]; then
    schema_errors=0
    for key in STAGE_ID STAGE_NAME BASELINE_COMMIT ACCEPTED_COMMIT LOCAL_HEAD DEPLOYED_HEAD DEPLOYED_AT DEPLOYMENT_RESULT FINAL_RESULT TEST_RESULT SECRETS_REVIEW RUNTIME_DATA_REVIEW; do
      if ! /usr/bin/grep -q "^${key}=" "$BACKUP_DIR/manifest.env"; then
        schema_errors=$((schema_errors + 1))
      fi
    done
    if [[ $schema_errors -eq 0 ]]; then
      pass "manifest conforms to schema (all required keys present)"
    else
      fail "manifest missing $schema_errors required keys"
    fi
  else
    fail "manifest.env not found for schema validation"
  fi
else
  fail "backup script failed — cannot validate schema"
fi
/bin/rm -rf "$TEST_TMPDIR"

# Test 10: Full baseline..accepted commit range recorded
echo "=== Test 10: changed-files.txt records baseline..accepted range ==="
TEST_TMPDIR="$(/usr/bin/mktemp -d /tmp/cofounder-gov.XXXXXX)"
cd "$REPO"
# Skip if worktree is not clean (other tests may leave temp files)
STATUS="$(/usr/bin/git status --porcelain --untracked-files=all 2>/dev/null || true)"
if [[ -n "$STATUS" ]]; then
  echo "  SKIP: worktree not clean — cannot run backup script"
else
  BASELINE_H="$(git rev-parse HEAD)"
  PARENT_H="$(git rev-parse HEAD^1 2>/dev/null || echo "")"
  if [[ -n "$PARENT_H" ]]; then
    BACKUP_LOG="$TEST_TMPDIR/range.log"
    if COFOUNDER_BACKUP_ROOT="$TEST_TMPDIR/backups" "$BACKUP_SCRIPT" "G01-RNG" "$PARENT_H" "$BASELINE_H" > "$BACKUP_LOG" 2>&1; then
      BACKUP_DIR_LINE="$(grep "BACKUP_DIR=" "$BACKUP_LOG" | /usr/bin/tail -1)"
      BACKUP_DIR="$(echo "$BACKUP_DIR_LINE" | /usr/bin/sed 's/BACKUP_DIR=//')"
      if [[ -f "$BACKUP_DIR/changed-files.txt" ]]; then
        if /usr/bin/grep -q "baseline.*accepted" "$BACKUP_DIR/changed-files.txt" && \
           /usr/bin/grep -q "$PARENT_H" "$BACKUP_DIR/changed-files.txt" && \
           /usr/bin/grep -q "$BASELINE_H" "$BACKUP_DIR/changed-files.txt"; then
          pass "changed-files.txt records full baseline..accepted range"
        else
          fail "changed-files.txt missing baseline or accepted SHA"
        fi
      else
        fail "changed-files.txt not found"
      fi
    else
      fail "backup script failed — cannot check range"
    fi
  else
    echo "  SKIP: no parent commit (root commit)"
  fi
fi
/bin/rm -rf "$TEST_TMPDIR"

# Test 11: PROJECT_STATE contains no stale fixed accepted SHA
echo "=== Test 11: PROJECT_STATE has no stale hard-coded HEAD ==="
cd "$REPO"
STATE_FILE="$REPO/docs/project-control/PROJECT_STATE.md"
if [[ -f "$STATE_FILE" ]]; then
  # Look for 40-char hex strings in the Current State section that look like SHAs
  stale_shas="$(/usr/bin/grep -E '^\*\*Current accepted HEAD\*\*:' "$STATE_FILE" 2>/dev/null | /usr/bin/grep -oE '[0-9a-f]{40}' || true)"
  if [[ -z "$stale_shas" ]]; then
    pass "PROJECT_STATE has no stale hard-coded accepted HEAD SHA"
  else
    fail "PROJECT_STATE contains stale hard-coded SHA: $stale_shas"
  fi
else
  fail "PROJECT_STATE.md not found"
fi

# Test 12: D07-D10 backup report uses stage-specific tests and next action
echo "=== Test 12: D07-D10 stage-specific recovery report ==="
stage_routing_errors=0
for test_file in \
  tests/test_finance_agent.py \
  tests/test_policy_gate.py \
  tests/test_artifact_synthesizer.py \
  tests/test_workflow_controller.py; do
  if ! /usr/bin/grep -Fq "$test_file" "$BACKUP_SCRIPT"; then
    fail "D07-D10 recovery routing missing $test_file"
    stage_routing_errors=$((stage_routing_errors + 1))
  fi
done
if ! /usr/bin/grep -Fq 'NEXT_ACTION="D11 Product API"' "$BACKUP_SCRIPT"; then
  fail "D07-D10 recovery routing missing D11 next action"
  stage_routing_errors=$((stage_routing_errors + 1))
fi
if /usr/bin/grep -q 'TARGETED_D06_D_TEST' "$BACKUP_SCRIPT"; then
  fail "stage report still contains D06-D-specific targeted field names"
  stage_routing_errors=$((stage_routing_errors + 1))
fi
if [[ $stage_routing_errors -eq 0 ]]; then
  pass "D07-D10 recovery report uses stage-specific tests and D11 next action"
fi

# Test 13: D11 backup report uses Product API tests and D12 next action
echo "=== Test 13: D11 stage-specific recovery report ==="
d11_routing_errors=0
for test_file in \
  tests/test_product_api.py \
  tests/test_executive_orchestrator.py \
  tests/test_workflow_controller.py; do
  if ! /usr/bin/grep -Fq "$test_file" "$BACKUP_SCRIPT"; then
    fail "D11 recovery routing missing $test_file"
    d11_routing_errors=$((d11_routing_errors + 1))
  fi
done
if ! /usr/bin/grep -Fq \
  'NEXT_ACTION="D12 Founder Mission Control UI"' \
  "$BACKUP_SCRIPT"; then
  fail "D11 recovery routing missing D12 next action"
  d11_routing_errors=$((d11_routing_errors + 1))
fi
if [[ $d11_routing_errors -eq 0 ]]; then
  pass "D11 recovery report uses Product API tests and D12 next action"
fi

# Test 14: D13 backup report uses Evaluation tests and D14 next action
echo "=== Test 14: D13 stage-specific recovery report ==="
d13_routing_errors=0
for test_file in \
  tests/test_evaluation.py \
  tests/test_ui.py \
  tests/test_state_repository.py \
  tests/test_artifacts.py; do
  if ! /usr/bin/grep -Fq "$test_file" "$BACKUP_SCRIPT"; then
    fail "D13 recovery routing missing $test_file"
    d13_routing_errors=$((d13_routing_errors + 1))
  fi
done
if ! /usr/bin/grep -Fq \
  'NEXT_ACTION="D14 Hackathon submission package"' \
  "$BACKUP_SCRIPT"; then
  fail "D13 recovery routing missing D14 next action"
  d13_routing_errors=$((d13_routing_errors + 1))
fi
if [[ $d13_routing_errors -eq 0 ]]; then
  pass "D13 recovery report uses stage-specific tests and D14 next action"
fi

# Test 15: Product runtime locks remain outside source deployment state
echo "=== Test 15: Product runtime lock isolation ==="
if /usr/bin/grep -Fxq 'data/.locks/' "$REPO/.gitignore" && \
   /usr/bin/grep -Fq "':(exclude)data/.locks/**'" "$DEPLOY_SCRIPT"; then
  pass "artifact locks are ignored and excluded from deployment preflight"
else
  fail "artifact lock isolation is missing from Git or deployment preflight"
fi

echo
echo "=== Test Summary ==="
echo "Passed: $PASS_COUNT"
echo "Failed: $FAIL_COUNT"

if [[ $FAIL_COUNT -gt 0 ]]; then
  exit 1
fi
