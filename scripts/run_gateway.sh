#!/usr/bin/env bash
set -euo pipefail

# Load .env if present (skip exported values that are already set)
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
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
