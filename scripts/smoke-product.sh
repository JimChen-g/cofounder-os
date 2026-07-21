#!/bin/zsh
set -euo pipefail

REMOTE_USER="${COFOUNDER_REMOTE_USER:?Set COFOUNDER_REMOTE_USER}"
REMOTE_HOST="${COFOUNDER_REMOTE_HOST:?Set COFOUNDER_REMOTE_HOST}"
REMOTE_PORT="${COFOUNDER_REMOTE_PORT:-22}"
REMOTE_HOME="${COFOUNDER_REMOTE_HOME:-/home/$REMOTE_USER}"
SSH_KEY="${COFOUNDER_SSH_KEY:?Set COFOUNDER_SSH_KEY}"
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

echo "=== LOCAL STATUS ==="
"$HOME/.local/bin/cofounderctl" status

echo
echo "=== LOCAL HEALTH ==="
"$HOME/.local/bin/cofounderctl" health

echo
echo "=== LOCAL SMOKE ==="
"$HOME/.local/bin/cofounderctl" smoke

echo
echo "=== REMOTE STATUS ==="
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' status"

echo
echo "=== REMOTE HEALTH ==="
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' health"

echo
echo "=== REMOTE SMOKE ==="
ssh "${SSH_ARGS[@]}" \
  "'$REMOTE_CTL' smoke"

echo
echo "FINAL_RESULT=PASS"
echo "INFRASTRUCTURE_CHANGED=NO"
