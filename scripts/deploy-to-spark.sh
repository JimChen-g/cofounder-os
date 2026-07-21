#!/bin/zsh
set -euo pipefail

DRY_RUN="NO"

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN="YES"
elif [[ -n "${1:-}" ]]; then
  echo "Usage: $0 [--dry-run]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"

REMOTE_USER="${COFOUNDER_REMOTE_USER:?Set COFOUNDER_REMOTE_USER}"
REMOTE_HOST="${COFOUNDER_REMOTE_HOST:?Set COFOUNDER_REMOTE_HOST}"
REMOTE_PORT="${COFOUNDER_REMOTE_PORT:-22}"
REMOTE_HOME="${COFOUNDER_REMOTE_HOME:-/home/$REMOTE_USER}"
REMOTE_REPO="${COFOUNDER_REMOTE_REPO:-$REMOTE_HOME/cofounder-os}"
SSH_KEY="${COFOUNDER_SSH_KEY:?Set COFOUNDER_SSH_KEY}"
REMOTE_CTL="${COFOUNDER_REMOTE_CTL:-$REMOTE_HOME/.local/bin/cofounderctl}"

STAMP="$(date '+%Y%m%d-%H%M%S')"
LOCAL_DEPLOY_ROOT="$HOME/Library/Application Support/CoFounderOS/deployments"
LOCAL_DEPLOY_DIR="$LOCAL_DEPLOY_ROOT/$STAMP"
LOCAL_BUNDLE="$LOCAL_DEPLOY_DIR/cofounder-os.bundle"
LOG_DIR="$HOME/Library/Logs/CoFounderOS/dev"
LOG="$LOG_DIR/deploy-to-spark-$STAMP.log"
REMOTE_DEPLOY_DIR="$REMOTE_HOME/.config/cofounder-os/deployments/$STAMP"
REMOTE_BUNDLE="$REMOTE_DEPLOY_DIR/cofounder-os.bundle"

mkdir -p "$LOCAL_DEPLOY_DIR" "$LOG_DIR"
chmod 700 "$LOCAL_DEPLOY_ROOT" "$LOCAL_DEPLOY_DIR" "$LOG_DIR"

exec > >(tee "$LOG") 2>&1

SSH_BASE=(
  -i "$SSH_KEY"
  -o IdentitiesOnly=yes
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=5
  -o ServerAliveCountMax=2
  -p "$REMOTE_PORT"
)

SSH_TARGET="$REMOTE_USER@$REMOTE_HOST"
RSYNC_SSH="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -p $REMOTE_PORT"

remote_run() {
  ssh "${SSH_BASE[@]}" "$SSH_TARGET" "$@"
}

rollback_remote() {
  local previous_head="$1"

  echo
  echo "=== AUTOMATIC REMOTE ROLLBACK ==="

  remote_run \
    "git -C '$REMOTE_REPO' reset --hard '$previous_head' &&
     '$REMOTE_CTL' status &&
     '$REMOTE_CTL' smoke" ||
    true
}

cd "$REPO"

[[ "$(git branch --show-current)" == "main" ]] ||
  { echo "BLOCK: local branch must be main"; exit 1; }

[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  { echo "BLOCK: local working tree is not clean"; exit 1; }

LOCAL_HEAD="$(git rev-parse HEAD)"
PREVIOUS_REMOTE_HEAD="$(
  remote_run "git -C '$REMOTE_REPO' rev-parse HEAD"
)"

echo "DRY_RUN=$DRY_RUN"
echo "LOCAL_HEAD=$LOCAL_HEAD"
echo "PREVIOUS_REMOTE_HEAD=$PREVIOUS_REMOTE_HEAD"
echo "REMOTE_REPO=$REMOTE_REPO"
echo "LOCAL_DEPLOY_DIR=$LOCAL_DEPLOY_DIR"
echo "REMOTE_DEPLOY_DIR=$REMOTE_DEPLOY_DIR"
echo "LOG_FILE=$LOG"

REMOTE_STATUS="$(
  remote_run \
    "git -C '$REMOTE_REPO' status --porcelain --untracked-files=all \
     -- . ':(exclude)data/.locks/**'"
)"

if [[ -n "$REMOTE_STATUS" ]]; then
  printf '%s\n' "$REMOTE_STATUS"
  echo "BLOCK: remote working tree is not clean"
  exit 1
fi

git bundle create "$LOCAL_BUNDLE" --all
git bundle verify "$LOCAL_BUNDLE"
shasum -a 256 "$LOCAL_BUNDLE" > "$LOCAL_DEPLOY_DIR/SHA256SUMS"

remote_run \
  "mkdir -p '$REMOTE_DEPLOY_DIR' &&
   chmod 700 '$REMOTE_DEPLOY_DIR'"

rsync \
  -a \
  -e "$RSYNC_SSH" \
  "$LOCAL_BUNDLE" \
  "$SSH_TARGET:$REMOTE_BUNDLE"

if [[ "$DRY_RUN" == "YES" ]]; then
  remote_run \
    "git bundle list-heads '$REMOTE_BUNDLE' &&
     rm -f '$REMOTE_BUNDLE' &&
     rmdir '$REMOTE_DEPLOY_DIR' 2>/dev/null || true"

  echo
  echo "FINAL_RESULT=PASS"
  echo "DEPLOYMENT_MODE=DRY_RUN"
  echo "LOCAL_HEAD=$LOCAL_HEAD"
  echo "REMOTE_HEAD=$PREVIOUS_REMOTE_HEAD"
  echo "REMOTE_REPOSITORY_MODIFIED=NO"
  echo "INFRASTRUCTURE_CHANGED=NO"
  exit 0
fi

remote_run "bash -s -- '$REMOTE_REPO' '$REMOTE_DEPLOY_DIR' '$PREVIOUS_REMOTE_HEAD'" <<'REMOTE_BACKUP'
set -euo pipefail

REMOTE_REPO="$1"
DEPLOY_DIR="$2"
PREVIOUS_HEAD="$3"

git -C "$REMOTE_REPO" bundle create \
  "$DEPLOY_DIR/remote-before.bundle" \
  --all

git -C "$REMOTE_REPO" status \
  --porcelain=v1 \
  --untracked-files=all \
  > "$DEPLOY_DIR/remote-status-before.txt"

git -C "$REMOTE_REPO" log \
  --oneline \
  --decorate \
  -n 20 \
  > "$DEPLOY_DIR/remote-log-before.txt"

printf 'PREVIOUS_HEAD=%s\n' "$PREVIOUS_HEAD" \
  > "$DEPLOY_DIR/manifest.env"

sha256sum \
  "$DEPLOY_DIR/remote-before.bundle" \
  "$DEPLOY_DIR/cofounder-os.bundle" \
  > "$DEPLOY_DIR/SHA256SUMS"

chmod 600 "$DEPLOY_DIR"/*
REMOTE_BACKUP

set +e
remote_run \
  "cd '$REMOTE_REPO' &&
   git fetch '$REMOTE_BUNDLE' refs/heads/main &&
   git reset --hard FETCH_HEAD &&
   test \"\$(git rev-parse HEAD)\" = '$LOCAL_HEAD' &&
   '$REMOTE_CTL' status &&
   '$REMOTE_CTL' health &&
   '$REMOTE_CTL' smoke"
DEPLOY_RC=$?
set -e

if [[ "$DEPLOY_RC" -ne 0 ]]; then
  rollback_remote "$PREVIOUS_REMOTE_HEAD"
  echo "FINAL_RESULT=FAIL"
  echo "ROLLBACK_RESULT=ATTEMPTED"
  exit 1
fi

REMOTE_HEAD="$(
  remote_run "git -C '$REMOTE_REPO' rev-parse HEAD"
)"

if [[ "$REMOTE_HEAD" != "$LOCAL_HEAD" ]]; then
  rollback_remote "$PREVIOUS_REMOTE_HEAD"
  echo "BLOCK: remote HEAD does not match local HEAD"
  exit 1
fi

remote_run \
  "printf '%s\n' \
   'DEPLOYED_HEAD=$LOCAL_HEAD' \
   'DEPLOYED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')' \
   'DEPLOYMENT_RESULT=PASS' \
   >> '$REMOTE_DEPLOY_DIR/manifest.env' &&
   chmod 600 '$REMOTE_DEPLOY_DIR/manifest.env'"

echo
echo "FINAL_RESULT=PASS"
echo "DEPLOYMENT_MODE=APPLY"
echo "LOCAL_HEAD=$LOCAL_HEAD"
echo "PREVIOUS_REMOTE_HEAD=$PREVIOUS_REMOTE_HEAD"
echo "REMOTE_HEAD=$REMOTE_HEAD"
echo "REMOTE_BACKUP_PATH=$REMOTE_DEPLOY_DIR"
echo "INFRASTRUCTURE_CHANGED=NO"
