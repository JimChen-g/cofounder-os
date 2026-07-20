# Co-founder OS Gateway v0.1

Unified API gateway for Co-founder OS AI providers.  Exposes a single
OpenAI-compatible Chat Completions endpoint (`POST /v1/chat/completions`)
that routes requests to the best available upstream provider with automatic
fallback.

---

## Architecture

```
┌─────────────┐     HTTP      ┌──────────────────┐     HTTP      ┌──────────────┐
│   Client    │ ────────────► │  FastAPI Gateway │ ────────────► │   Provider   │
│ (SDK, CLI)  │ ◄──────────── │  app/main.py     │ ◄──────────── │ (Qwen/Step)  │
└─────────────┘              └────────────────┘              └──────────────┘
```

**Request flow:**

1. Client sends `POST /v1/chat/completions` with an optional `provider`
   field (`cofounder-qwen`, `cofounder-step`, or omit for `cofounder-auto`).
2. `app/router/selector.py` looks up the preferred provider in the
   `ProviderRegistry`.
3. If the preferred provider is unavailable, the router falls back to the next
   registered provider automatically.
4. The upstream provider responds with a normalised `ChatResponse`.
5. `AuditLogger` appends a JSON Lines record to today's UTC audit file
   (`data/audit/YYYY-MM-DD.jsonl`).  Records never contain prompt text,
   message content, or credentials.

---

## Virtual Models

| Virtual model | Enum value | Description |
|---|---|---|
| `cofounder-auto` | _(default — no provider specified)_ | Routes to Qwen or Step based on deterministic policy. |
| `cofounder-qwen` | `cofounder-qwen` | Qwen (DashScope compatible-mode). |
| `cofounder-step` | `cofounder-step` | StepFun (`step_plan/v1`). |

---

## Deterministic Routing Rules

1. **Explicit provider wins.**  If `provider` is set in the request body, the
   gateway attempts that provider first.
2. **Fallback order:** preferred provider → Step (for Qwen) or Qwen (for Step).
3. **All providers exhausted:** returns HTTP 500 with
   `{"error": "provider_error", "detail": "…"}`.
4. **Health endpoint** (`GET /health`) reports `degraded` if any
   registered provider is `unavailable`; otherwise `healthy`.

---

## Environment Configuration

Copy `.env.example` to `.env` and fill in real values:

```bash
cp .env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `QWEN_BASE_URL` | no | `http://127.0.0.1:8000/v1` | Qwen compatible-mode base URL. |
| `QWEN_API_KEY` | no | _(none)_ | Qwen API key. |
| `QWEN_MODEL` | no | `replace-with-vllm-model-id` | Qwen model identifier. |
| `STEP_BASE_URL` | no | `https://api.stepfun.com/step_plan/v1` | StepFun base URL. |
| `STEP_API_KEY` | no | _(none)_ | StepFun API key. |
| `STEP_MODEL` | no | `step-3.7-flash` | StepFun model identifier. |
| `GATEWAY_HOST` | no | `127.0.0.1` | Bind address. |
| `GATEWAY_PORT` | no | `9000` | Listen port. |
| `REQUEST_TIMEOUT_SECONDS` | no | `300` | Upstream request timeout. |
| `AUDIT_DIR` | no | `data/audit` | Directory for audit JSONL files. |

> **Never commit real `.env` files.**  `.env` is gitignored; `.env.example`
> contains placeholders only.

---

## Virtual Environment Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[dev]"
```

---

## Local Startup

```bash
source .venv/bin/activate
bash scripts/run_gateway.sh
```

The gateway listens on `http://127.0.0.1:9000` by default.

### Founder Mission Control

D12 is served by the same FastAPI process and consumes the stable D11 Product
API over same-origin requests:

```text
http://127.0.0.1:9000/ui
```

The UI starts founder missions, shows the three-task dependency graph and agent
evidence, resolves approvals, renders integrity-checked artifacts, and exposes
bounded retry/recovery plus the append-only audit trace. It has no separate
frontend runtime or mock backend.

`scripts/run_gateway.sh`:
- enables `set -euo pipefail`
- sources `.env` if present
- exits if `.venv` is missing
- runs `python -m uvicorn app.main:app --host "${GATEWAY_HOST:-127.0.0.1}" --port "${GATEWAY_PORT:-9000}"`

---

## Smoke-Test Procedure

```bash
export QWEN_API_KEY=sk-...
export STEP_API_KEY=sk-...
export GATEWAY_AUDIT_TOKEN=test-token
bash scripts/smoke_test.sh
```

The smoke test exercises:

1. `GET /health`
2. `GET /v1/models`
3. `POST /v1/chat/completions` — `cofounder-qwen`
4. `POST /v1/chat/completions` — `cofounder-step`
5. `POST /v1/chat/completions` — `cofounder-auto` (default provider)
6. `GET /audit/recent` (authenticated)

All values come from environment variables; no credentials are hard-coded in
the script.

---

## Audit Log Behavior

- One file per UTC day: `data/audit/YYYY-MM-DD.jsonl`.
- Append-only JSON Lines format.
- Each record contains: `ts`, `ts_iso`, `event`, `request_id`, `provider`,
  `model`, token counts, latency, status, error (if any), user-agent.
- **Records never contain:** complete prompt text, message content,
  authorization headers, or API keys.

### Reading recent records

```bash
curl -H "X-Audit-Token: $GATEWAY_AUDIT_TOKEN" \
     http://127.0.0.1:9000/audit/recent
```

Returns the last 200 records from today's file.

---

## Authentication

| Endpoint | Auth |
|---|---|
| `GET /health` | None |
| `GET /v1/models` | None |
| `POST /v1/chat/completions` | None (API keys passed upstream) |
| `GET /audit/recent` | `X-Audit-Token` header must match `GATEWAY_AUDIT_TOKEN` |

---

## Current v0.1 Limitations

- Providers are registered at startup only; dynamic addition/removal requires
  a restart.
- Audit logs are daily UTC files with no rotation or size cap beyond the
  day boundary.
- No request-level authentication on chat completions; upstream API keys are
  configured at the gateway level.
- Health checks perform a synchronous network call to each provider's models
  endpoint.
- Single-process, single-worker deployment only.

---

## DGX Deployment Assumptions

- The gateway runs behind a reverse proxy (e.g., NGINX) that terminates TLS.
- Provider API keys are injected via environment variables or a secrets
  manager (never stored in the image).
- `GATEWAY_HOST=0.0.0.0` and the container port maps to the host.
- Audit volume (`AUDIT_DIR`) is a mounted directory or persistent volume so
  logs survive container restarts.
- For multi-worker deployments, audit writes should be coordinated (e.g.,
  sidecar collector) because the current JSONL writer is not process-safe.
