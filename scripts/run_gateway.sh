#!/usr/bin/env bash
set -euo pipefail

# Load .env if present; variables already set in the environment take precedence
if [[ -f .env ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
        [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
        key="${BASH_REMATCH[2]}"
        value="${BASH_REMATCH[3]}"
        [[ -n "${!key+x}" ]] && continue
        if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
            value="${BASH_REMATCH[1]}"
        fi
        export "$key=$value"
    done < .env
fi

# Require virtual environment
if [[ ! -d .venv ]]; then
    echo "ERROR: .venv not found." >&2
    echo "Run: python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'" >&2
    exit 1
fi

source .venv/bin/activate

exec python -m uvicorn app.main:app \
    --host "${GATEWAY_HOST:-127.0.0.1}" \
    --port "${GATEWAY_PORT:-9000}"
