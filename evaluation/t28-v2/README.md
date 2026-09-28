# Corrected protocol evaluation, v2 (not yet run on a model)

This is a new edition, not a correction of frozen T28 results. The 10 cases are
unchanged: **0/10 require a choice between multiple legal candidates**. This
edition tests protocol compliance and deterministic policy behavior, not a Skill
quality improvement. No additional GPU evaluation was run for this repair.
The original T28 cases, harness, results and freeze file remain unchanged.
Original guide/client bytes are archived in `../t28/frozen-skill/`; run historical
T28 only from its original commit and original runtime environment.

The v2 client records `error_class` and `http_status`. The invalid-request case
passes only when the exact authorized request receives HTTP 422. HTTP 401, HTTP
500, connection failures and malformed receipts are failures, even if an Agent
ends with `refuse`. No exception text or HTTP body is published by the client.

Dry run (does not call the model):

```sh
python evaluation/t28-v2/run_pairs.py
```

A separately authorized, idle-resource evaluation can use `--execute --output
/private/new-v2-results`. Credentials are environment references:
`COFOUNDER_GATEWAY_URL`, `COFOUNDER_GATEWAY_API_KEY`, `SPARK_DECIDE_URL`, and the
**dedicated** `SPARK_DECIDE_API_KEY`. Do not supply the gateway master key as the
Skill key. The original resource preflight and serial monitoring remain required.

Continuation is public and defaults to inspection only:

```sh
python evaluation/t28-v2/continue_pairs.py --output /private/new-v2-results
# Only after resource preflight and explicit authorization to make model calls:
python evaluation/t28-v2/continue_pairs.py --output /private/new-v2-results --execute
```

Only absent trial IDs may run. Completed failures are retained; partial/reserved
or malformed records block continuation for manual inspection. A trial file is
exclusively reserved before any call. A separate manifest records the source
config hash, retained file hashes and planned missing IDs before calls begin;
it is intent, not evidence that calls completed. No automatic retry or overwrite
is permitted. No historical manifest is fabricated: the locally recovered T28
`resume_eval.py` implements missing-ID continuation but does not write a manifest,
and none was found in the original published evidence directory. Consequently
historical interruption provenance remains incomplete.

The historical differences (13/20 vs 11/20 complete; 20/20 vs 18/20 conditional)
are descriptive protocol outcomes. The repeated temperature-zero trials are not
independent samples, and the difference comes from one case repeated twice.
This repair does not reinterpret those results as decision-quality evidence.
