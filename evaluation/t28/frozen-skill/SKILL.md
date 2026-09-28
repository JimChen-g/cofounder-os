---
name: spark-decide
description: Choose a bounded local, authorized cloud, or human route through a configured Spark decision endpoint. Use for explicit routing decisions among supplied candidates; ordinary writing, factual questions, and actions without a routing choice do not need this skill.
---

# Spark Decide

Get one short decision from the existing local Qwen endpoint.
This skill recommends a route; it does not execute the recommended action.
It is an enum-decoding interface, not a trained or calibrated routing policy.

## Scope

Use when a workflow needs to choose among an explicit finite set of routes.
Valid actions are `local`, `step`, `human`, and `refuse`.
Supply only the candidates that make sense for this task.
Keep the caller's authorization, privacy, and budgets intact.
A recommendation does not create cloud permission or delivery approval.
For ordinary writing or questions with no routing choice, work directly.
Do not call this endpoint just because a task mentions Spark or models.

## Runtime

Requires Python 3.10+ and network access to an authorized endpoint.
The client uses only the Python standard library.
Set `SPARK_DECIDE_URL` to the full `/v1/spark-decide` endpoint.
Set `SPARK_DECIDE_TOKEN` to its existing gateway bearer token.
Keep the token in the environment; do not include it in request files or logs.
Use HTTPS outside loopback, or an authorized local tunnel.
Do not install model, training, or project dependencies to use this skill.
The server owns model deployment and policy enforcement.

## Request

Read [request.schema.json](references/request.schema.json) when constructing inputs.
Read [examples.json](references/examples.json) for accepted and refused examples.
`task` is a concise description of the routing need, limited to 4,000 characters.
`candidates` contains one to four distinct action labels.
`policy` carries privacy, provider grants, invocation permission, and budgets.
Omitting policy uses restricted privacy and local-only invocation.
`evidence_ids` links existing evidence; never put secrets into an identifier.
Use the smallest task context sufficient for the choice.
Do not silently downgrade restricted information to public.

Save one JSON request in a caller-controlled temporary directory.
Resolve this skill's directory from its actual installation location.
Run the script from that location; it does not import the host project.

```sh
python /path/to/spark-decide/scripts/decide.py /path/to/request.json
```

The script also accepts `-` to read a request from standard input.
Call this short interface for each decision; do not resend this long guide.
Do not retry automatically; return the failure or ask for a permitted alternative.
The server makes at most one local inference attempt per call.
It does not send a failed local request to Step.

## Interpret the result

Read [response.schema.json](references/response.schema.json) for exact fields.
`legal_candidates` is the subset surviving provider constraints.
`action` is one legal label or the terminal `refuse` outcome.
`refusal_reason` identifies policy, budget, provider, or decoding failure.
A `human` result asks for human judgment without fabricating that judgment.
A `step` result still requires the downstream caller to enforce its policy.
The chosen downstream provider has not been invoked by this decision.
Preserve `decision_id`, evidence IDs, request hash, and output hash for traceability.
The hashes identify content; they are not signatures or approval receipts.
Record `model_version`, `policy_version`, `latency_ms`, and usage with the decision.

## Scores and limits

This version returns `scores: null` and `score_kind: unavailable`.
Unavailable does not mean zero confidence, certainty, or failure probability.
There is no raw logprob score or calibrated probability in this version.
Do not turn an enum label, masked probability, or latency into a quality score.
Do not assert tokenizer, thinking, or logprob support from a successful request.
Service capability probes belong in separate measured evidence.
No training or calibration is performed by this package.
No competition certification or final release signature is claimed.

## Failure and demonstration

Treat malformed labels, truncated output, exhausted budget, and transport failure as refusal.
Keep the task within its original privacy boundary after every failure.
Do not execute arbitrary shell commands returned by a model.
Do not use this decision to bypass artifact checks or human approval gates.
For a project-independent wiring check, run:

```sh
python /path/to/spark-decide/scripts/demo.py
```

The demo uses a clearly labeled local fixture, not a live model or quality evaluation.
For live evidence, use the configured real endpoint and retain the complete response.
Keep private inputs and credentials out of published evidence.
