# Product API

The D11 Product API is the stable boundary consumed by the D12 Founder
Mission Control UI. It is mounted under `/api` in the existing FastAPI
application.

## Workflow

```text
POST /api/runs
  -> Executive Orchestrator plan and materialization
  -> Workflow Controller
     -> completed
     -> waiting_approval
     -> failed
```

Approval and retry requests return to the Workflow Controller. HTTP handlers
never edit JSON or JSONL state directly.

## Configuration

- `COFOUNDER_GATEWAY_URL`: optional Gateway override; use
  `http://127.0.0.1:19000` on Mac. On DGX Spark, an omitted value safely
  defaults to `http://127.0.0.1:<GATEWAY_PORT>`.
- `COFOUNDER_GATEWAY_API_KEY`: optional Gateway credential.
- `PRODUCT_DATA_DIR`: shared state and artifact root; defaults to `data`.
- `PRODUCT_MAX_ARTIFACT_BYTES`: maximum text content returned inline;
  defaults to 1 MiB and is bounded to 10 MiB.

The Product API health check validates local composition without making a
model call.

## Errors

Product API errors use:

```json
{
  "error": "not_found",
  "detail": "The requested Run, approval, or record was not found.",
  "request_id": "req-..."
}
```

Validation errors use FastAPI's standard `422` response. Internal paths,
model output, and raw exception text are not returned.
