#!/bin/zsh
set -euo pipefail

REMOTE_USER="${COFOUNDER_REMOTE_USER:?Set COFOUNDER_REMOTE_USER}"
REMOTE_HOST="${COFOUNDER_REMOTE_HOST:?Set COFOUNDER_REMOTE_HOST}"
REMOTE_PORT="${COFOUNDER_REMOTE_PORT:-22}"
REMOTE_HOME="${COFOUNDER_REMOTE_HOME:-/home/$REMOTE_USER}"
REMOTE_REPO="${COFOUNDER_REMOTE_REPO:-$REMOTE_HOME/cofounder-os}"
SSH_KEY="${COFOUNDER_SSH_KEY:?Set COFOUNDER_SSH_KEY}"
REMOTE_DEPLOY_ROOT="${COFOUNDER_REMOTE_DEPLOY_ROOT:-$REMOTE_HOME/.config/cofounder-os/deployments}"
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

TARGET_HEAD="${1:-}"

if [[ -z "$TARGET_HEAD" ]]; then
  TARGET_HEAD="$(
    ssh "${SSH_ARGS[@]}" \
      "latest=\$(find '$REMOTE_DEPLOY_ROOT' \\
        -mindepth 2 -maxdepth 2 -name manifest.env \
        -type f 2>/dev/null | sort | tail -1);
       test -n \"\$latest\";
       sed -n \"s/^PREVIOUS_HEAD=//p\" \"\$latest\" | tail -1"
  )"
fi

[[ -n "$TARGET_HEAD" ]] || {
  echo "BLOCK: rollback target is unavailable"
  exit 1
}

CURRENT_HEAD="$(
  ssh "${SSH_ARGS[@]}" \
    "git -C '$REMOTE_REPO' rev-parse HEAD"
)"

echo "CURRENT_HEAD=$CURRENT_HEAD"
echo "TARGET_HEAD=$TARGET_HEAD"

REMOTE_STATUS="$(
  ssh "${SSH_ARGS[@]}" \
    "git -C '$REMOTE_REPO' status --porcelain --untracked-files=all"
)"

if [[ -n "$REMOTE_STATUS" ]]; then
  printf '%s\n' "$REMOTE_STATUS"
  echo "BLOCK: remote working tree is not clean"
  exit 1
fi

set +e
ssh "${SSH_ARGS[@]}" \
  "git -C '$REMOTE_REPO' reset --hard '$TARGET_HEAD' &&
   '$REMOTE_CTL' status &&
   '$REMOTE_CTL' health &&
   '$REMOTE_CTL' smoke"
ROLLBACK_RC=$?
set -e

if [[ "$ROLLBACK_RC" -ne 0 ]]; then
  ssh "${SSH_ARGS[@]}" \
    "git -C '$REMOTE_REPO' reset --hard '$CURRENT_HEAD'" ||
    true

  echo "FINAL_RESULT=FAIL"
  echo "RESTORE_HEAD=$CURRENT_HEAD"
  exit 1
fi

echo
echo "FINAL_RESULT=PASS"
echo "PREVIOUS_HEAD=$CURRENT_HEAD"
echo "REMOTE_HEAD=$TARGET_HEAD"
echo "INFRASTRUCTURE_CHANGED=NO"
