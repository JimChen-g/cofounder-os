#!/bin/zsh
set -euo pipefail

REMOTE_USER="${COFOUNDER_REMOTE_USER:-cofounder}"
REMOTE_HOST="${COFOUNDER_REMOTE_HOST:-spark.example.invalid}"
REMOTE_PORT="${COFOUNDER_REMOTE_PORT:-22}"
SSH_KEY="${COFOUNDER_SSH_KEY:-$HOME/.ssh/id_ed25519}"

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
  '/home/cofounder/.local/bin/cofounderctl status'

echo
echo "=== REMOTE HEALTH ==="
ssh "${SSH_ARGS[@]}" \
  '/home/cofounder/.local/bin/cofounderctl health'

echo
echo "=== REMOTE SMOKE ==="
ssh "${SSH_ARGS[@]}" \
  '/home/cofounder/.local/bin/cofounderctl smoke'

echo
echo "FINAL_RESULT=PASS"
echo "INFRASTRUCTURE_CHANGED=NO"
