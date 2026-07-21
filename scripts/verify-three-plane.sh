#!/bin/zsh
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ---------------------------------------------------------------------------
# verify-three-plane.sh — verify local, Spark, and origin/main HEAD equality
#
# Requires:
#   - Clean local worktree
#   - Local HEAD == origin/main HEAD == Spark HEAD
#   - Status, health, and smoke checks pass on both local and Spark
#
# Environment overrides (for testing):
#   COFOUNDER_TEST_LOCAL_HEAD    — override local HEAD for testing
#   COFOUNDER_TEST_ORIGIN_HEAD   — override origin HEAD for testing
#   COFOUNDER_TEST_SPARK_HEAD    — override Spark HEAD for testing
#
# Outputs:
#   Machine-readable PASS/FAIL block
#   Clipboard copy of summary
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_USER="${COFOUNDER_REMOTE_USER:?Set COFOUNDER_REMOTE_USER}"
REMOTE_HOST="${COFOUNDER_REMOTE_HOST:?Set COFOUNDER_REMOTE_HOST}"
REMOTE_PORT="${COFOUNDER_REMOTE_PORT:-22}"
REMOTE_HOME="${COFOUNDER_REMOTE_HOME:-/home/$REMOTE_USER}"
SSH_KEY="${COFOUNDER_SSH_KEY:?Set COFOUNDER_SSH_KEY}"
REMOTE_REPO="${COFOUNDER_REMOTE_REPO:-$REMOTE_HOME/cofounder-os}"
REMOTE_CTL="${COFOUNDER_REMOTE_CTL:-$REMOTE_HOME/.local/bin/cofounderctl}"

SSH_ARGS=(
  -i "$SSH_KEY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=2
  -p "$REMOTE_PORT"
  "$REMOTE_USER@$REMOTE_HOST"
)

cd "$REPO"

FINAL_RESULT="PASS"
WORKTREE_STATE="CLEAN"
VALIDATION_STATE="PASS"
HEAD_EQUAL="yes"

fail() {
  local msg="$1"
  echo "FAIL: $msg" >&2
  FINAL_RESULT="FAIL"
}

section() {
  echo "=== $1 ==="
}

# 1. Local worktree must be clean
section "WORKTREE"
STATUS="$(/usr/bin/git status --porcelain --untracked-files=all 2>/dev/null || true)"
if [[ -n "$STATUS" ]]; then
  fail "Local working tree is not clean"
  echo "$STATUS" | /usr/bin/sed 's/^/  /' >&2
  WORKTREE_STATE="DIRTY"
else
  echo "WORKTREE_STATUS=CLEAN"
fi

# 2. Local HEAD
section "LOCAL_HEAD"
LOCAL_HEAD="${COFOUNDER_TEST_LOCAL_HEAD:-$(/usr/bin/git rev-parse HEAD 2>/dev/null || echo "unknown")}"
echo "LOCAL_HEAD=$LOCAL_HEAD"
if [[ "$LOCAL_HEAD" == "unknown" ]]; then
  fail "Cannot resolve local HEAD"
fi

# 3. origin/main HEAD
section "ORIGIN_MAIN"
ORIGIN_HEAD="${COFOUNDER_TEST_ORIGIN_HEAD:-$(/usr/bin/git ls-remote origin main 2>/dev/null | /usr/bin/awk '{print $1}' || echo "unknown")}"
echo "ORIGIN_MAIN_HEAD=$ORIGIN_HEAD"
if [[ "$ORIGIN_HEAD" == "unknown" ]]; then
  fail "Cannot resolve origin/main HEAD"
fi

# 4. Spark HEAD
section "SPARK_HEAD"
SPARK_HEAD="${COFOUNDER_TEST_SPARK_HEAD:-$(ssh "${SSH_ARGS[@]}" \
  "git -C '$REMOTE_REPO' rev-parse HEAD 2>/dev/null || echo 'unknown'" || echo "unknown")}"
echo "SPARK_HEAD=$SPARK_HEAD"
if [[ "$SPARK_HEAD" == "unknown" ]]; then
  fail "Cannot resolve Spark HEAD"
fi

# 5. Compare all three — HEAD equality only, independent of service health
section "COMPARISON"
echo "LOCAL_HEAD=$LOCAL_HEAD"
echo "ORIGIN_MAIN_HEAD=$ORIGIN_HEAD"
echo "SPARK_HEAD=$SPARK_HEAD"
if [[ "$LOCAL_HEAD" == "$ORIGIN_HEAD" && "$LOCAL_HEAD" == "$SPARK_HEAD" ]]; then
  echo "THREE_PLANE_STATUS=EQUAL"
  HEAD_EQUAL="yes"
else
  fail "HEAD mismatch: local=$LOCAL_HEAD origin=$ORIGIN_HEAD spark=$SPARK_HEAD"
  echo "THREE_PLANE_STATUS=MISMATCH"
  HEAD_EQUAL="no"
fi

# 6. Local status, health, smoke
section "LOCAL_VALIDATION"
LOCAL_COFOUNDERCTL="$HOME/.local/bin/cofounderctl"
if [[ -x "$LOCAL_COFOUNDERCTL" ]]; then
  echo "--- Local Status ---"
  "$LOCAL_COFOUNDERCTL" status 2>/dev/null || { fail "Local status failed"; VALIDATION_STATE="FAIL"; }
  echo "--- Local Health ---"
  "$LOCAL_COFOUNDERCTL" health 2>/dev/null || { fail "Local health failed"; VALIDATION_STATE="FAIL"; }
  echo "--- Local Smoke ---"
  "$LOCAL_COFOUNDERCTL" smoke 2>/dev/null || { fail "Local smoke failed"; VALIDATION_STATE="FAIL"; }
else
  echo "LOCAL_COFOUNDERCTL=not_found at $LOCAL_COFOUNDERCTL"
  fail "Local cofounderctl not found — local validation cannot proceed"
  VALIDATION_STATE="FAIL"
fi

# 7. Spark status, health, smoke
section "SPARK_VALIDATION"
echo "--- Remote Status ---"
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' status 2>/dev/null" || { fail "Remote status failed"; VALIDATION_STATE="FAIL"; }
echo "--- Remote Health ---"
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' health 2>/dev/null" || { fail "Remote health failed"; VALIDATION_STATE="FAIL"; }
echo "--- Remote Smoke ---"
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' smoke 2>/dev/null" || { fail "Remote smoke failed"; VALIDATION_STATE="FAIL"; }

# 8. Build machine-readable result block
section "RESULT"
THREE_PLANE_EQ="EQUAL"
if [[ "$HEAD_EQUAL" != "yes" ]]; then
  THREE_PLANE_EQ="MISMATCH"
fi

/bin/cat > /tmp/three-plane-result.txt <<EOF
THREE_PLANE_VERIFICATION
FINAL_RESULT=$FINAL_RESULT
LOCAL_HEAD=$LOCAL_HEAD
ORIGIN_MAIN_HEAD=$ORIGIN_HEAD
SPARK_HEAD=$SPARK_HEAD
THREE_PLANE_STATUS=$THREE_PLANE_EQ
WORKTREE_STATUS=$WORKTREE_STATE
VALIDATION=$VALIDATION_STATE
EOF

/bin/cat /tmp/three-plane-result.txt

# 9. Clipboard
CLIPBOARD_TEXT="THREE_PLANE $FINAL_RESULT | heads=$THREE_PLANE_EQ worktree=$WORKTREE_STATE validation=$VALIDATION_STATE | local=$LOCAL_HEAD spark=$SPARK_HEAD origin=$ORIGIN_HEAD"
echo "$CLIPBOARD_TEXT" | pbcopy 2>/dev/null && echo "Copied to clipboard" || echo "pbcopy unavailable — summary follows:"
echo "$CLIPBOARD_TEXT"

/bin/rm -f /tmp/three-plane-result.txt

if [[ "$FINAL_RESULT" != "PASS" ]]; then
  exit 1
fi
