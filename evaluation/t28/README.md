# T28 bounded Agent paired evaluation

This directory freezes 10 synthetic routing-contract tasks and the project Agent
harness. Both arms have the same Qwen host, exact-request tool capability,
resource limits and explicit JSON protocol; the with-skill arm alone appends
`skills/spark-decide/SKILL.md`. Each task runs twice per arm (40 trials), at most
two model turns and one actual portable client invocation per trial. No shell,
downstream route execution, cloud fallback, model training or heldout reuse.

Run with the existing authorized loopback Gateway references:

```
python evaluation/t28/run_pairs.py --execute --output /private/path/new-results
```

`COFOUNDER_GATEWAY_URL`, `COFOUNDER_GATEWAY_API_KEY`, `SPARK_DECIDE_URL`, and
`SPARK_DECIDE_TOKEN` come from a trusted local environment, never from inputs or
published logs. `T28_STOP_FILE` stops subsequent trials when an external resource
monitor creates it. A production run requires an idle service/resource preflight
and serial resource monitoring; the CLI alone does not establish that window.

`freeze.json` locks the tasks, harness, guide and client. Each raw record contains
actual host output, tool request/exit/receipt, final answer, correctness and tool
constraints, time, identity and available usage. Judge calls are zero. Failure
client exit 1 is scored as refusal, without asserting its exact HTTP cause.
Do not rerun failed records or tune the prompt against this batch. Interrupted
batches can resume only missing trial IDs through the identical frozen `trial`
function, preserving prior files and a separate continuation manifest.

This is a bounded, two-step JSON Agent experiment, not an official NVIDIA
SkillEvaluator trial, native tool-calling benchmark, or Tier 3 PASS. The common
protocol is deliberately strong and tasks narrow; results do not establish
open-ended generalization. See `docs/t28-t29.md` for the actual results and limits.
