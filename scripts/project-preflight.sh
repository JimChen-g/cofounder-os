#!/bin/zsh
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

# ---------------------------------------------------------------------------
# project-preflight.sh — read-only project diagnostics
#
# Reports the authoritative state of the CoFounder OS project without
# modifying anything. Intended to be run before any implementation session
# or deployment.
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

# Track overall result
FINAL_RESULT="PASS"
WORKTREE_STATE="CLEAN"

fail() {
  echo "FAIL: $1" >&2
  FINAL_RESULT="FAIL"
}

section() {
  echo "=== $1 ==="
}

# 1. Authoritative repository
section "REPOSITORY"
echo "MAC_REPO=$REPO"
if [[ ! -d "$REPO/.git" ]]; then
  fail "Not a git repository: $REPO"
fi

# 2. Branch and clean status
section "BRANCH"
BRANCH="$(/usr/bin/git branch --show-current 2>/dev/null || echo "unknown")"
echo "BRANCH=$BRANCH"
if [[ "$BRANCH" != "main" ]]; then
  fail "Local branch is '$BRANCH', expected 'main'"
fi

STATUS="$(/usr/bin/git status --porcelain --untracked-files=all 2>/dev/null || true)"
if [[ -n "$STATUS" ]]; then
  fail "Local working tree is not clean"
  echo "STATUS_LINES:"
  echo "$STATUS" | /usr/bin/sed 's/^/  /'
  echo "WORKTREE_STATUS=DIRTY"
  WORKTREE_STATE="DIRTY"
else
  echo "WORKTREE_STATUS=CLEAN"
fi

# 3. Local HEAD
section "LOCAL_HEAD"
LOCAL_HEAD="$(/usr/bin/git rev-parse HEAD 2>/dev/null || echo "unknown")"
echo "LOCAL_HEAD=$LOCAL_HEAD"
if [[ "$LOCAL_HEAD" == "unknown" ]]; then
  fail "Cannot resolve local HEAD"
fi

# 4. origin/main HEAD
section "ORIGIN_MAIN"
ORIGIN_HEAD="$(/usr/bin/git ls-remote origin main 2>/dev/null | /usr/bin/awk '{print $1}' || echo "unknown")"
echo "ORIGIN_MAIN_HEAD=$ORIGIN_HEAD"
if [[ "$ORIGIN_HEAD" == "unknown" ]]; then
  fail "Cannot resolve origin/main HEAD"
fi

if [[ "$LOCAL_HEAD" != "$ORIGIN_HEAD" ]]; then
  fail "Local HEAD does not match origin/main"
fi

# 5. Spark HEAD
section "SPARK_HEAD"
SPARK_HEAD="$(ssh "${SSH_ARGS[@]}" \
  "git -C '$REMOTE_REPO' rev-parse HEAD 2>/dev/null || echo 'unknown'" || echo "unknown")"
echo "SPARK_HEAD=$SPARK_HEAD"
if [[ "$SPARK_HEAD" == "unknown" ]]; then
  fail "Cannot resolve Spark HEAD (SSH or remote git failed)"
fi

# 6. Active stage — parse PROJECT_STATE.md
section "ACTIVE_STAGE"
ACTIVE_STAGE=""
STAGE_LINE=""
if [[ -f "$REPO/docs/project-control/PROJECT_STATE.md" ]]; then
  # Try Current governance stage first, then Current accepted HEAD line
  STAGE_LINE="$(/usr/bin/grep -E 'Current (governance )?stage\*\*[[:space:]]*:' "$REPO/docs/project-control/PROJECT_STATE.md" 2>/dev/null | /usr/bin/head -1 || echo "")"
  if [[ -n "$STAGE_LINE" ]]; then
    ACTIVE_STAGE="$(echo "$STAGE_LINE" | /usr/bin/sed 's/.*: //')"
  fi
fi
echo "ACTIVE_STAGE=$ACTIVE_STAGE"
if [[ -z "$ACTIVE_STAGE" ]]; then
  fail "Cannot parse active stage from PROJECT_STATE.md"
fi

# 7. Service status and health
section "SERVICES"
LOCAL_COFOUNDERCTL="$HOME/.local/bin/cofounderctl"
if [[ -x "$LOCAL_COFOUNDERCTL" ]]; then
  echo "--- Local Status ---"
  "$LOCAL_COFOUNDERCTL" status 2>/dev/null || fail "Local cofounderctl status failed"
  echo "--- Local Health ---"
  "$LOCAL_COFOUNDERCTL" health 2>/dev/null || fail "Local cofounderctl health failed"
else
  echo "LOCAL_COFOUNDERCTL=not_found"
  fail "Local cofounderctl not found at $LOCAL_COFOUNDERCTL"
fi

echo "--- Remote Status ---"
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' status 2>/dev/null" || fail "Remote cofounderctl status failed"
echo "--- Remote Health ---"
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' health 2>/dev/null" || fail "Remote cofounderctl health failed"

# 8. Clipboard summary — report real worktree state
section "CLIPBOARD"
CLIPBOARD_LINES=(
  "PREFLIGHT $FINAL_RESULT"
  "BRANCH=$BRANCH"
  "LOCAL_HEAD=$LOCAL_HEAD"
  "ORIGIN_MAIN_HEAD=$ORIGIN_HEAD"
  "SPARK_HEAD=$SPARK_HEAD"
  "ACTIVE_STAGE=$ACTIVE_STAGE"
  "WORKTREE=$WORKTREE_STATE"
)
CLIPBOARD_TEXT="$(printf '%s\n' "${CLIPBOARD_LINES[@]}")"
echo "$CLIPBOARD_TEXT"
echo "$CLIPBOARD_TEXT" | pbcopy 2>/dev/null && echo "Copied to clipboard" || echo "pbcopy unavailable — summary follows:"

echo
echo "FINAL_RESULT=$FINAL_RESULT"

if [[ "$FINAL_RESULT" != "PASS" ]]; then
  exit 1
fi
