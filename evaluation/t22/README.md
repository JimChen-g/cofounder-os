# T22 project paired chain validation

This is a **project-owned bounded JSON-tool protocol**, not official NVIDIA Tier3, a general autonomous agent, or a production quality benchmark. It is a supplement when the official harness cannot use the existing Chat Completions-only Gateway as a full tool-capable Codex Responses backend. Official validation, actual compatibility errors/skips, and this supplement must be reported separately.

Three frozen synthetic cases cover a legal local choice, a restricted sole-Step-candidate refusal, and ordinary arithmetic that must not trigger the Skill. They are independent wiring fixtures and do not access T21 test data. `freeze.json` records their hash before live execution. The scorer version is `t22-exact-action-and-tool-v1`; the run records the exact harness and Skill hashes.

Both arms receive the same model (`cofounder-auto`, local-only policy), temperature 0, 512-token output cap, tool description, task, requested JSON format, maximum two model steps, and one allowed tool invocation. The sole arm difference is appending the actual `skills/spark-decide/SKILL.md` to the system message. Both arms can invoke the same portable `scripts/decide.py` against the same real endpoint. The no-Skill baseline is not deprived of the tool. Prompt length is a measured consequence of enabling the Skill, while the total budget ceiling stays equal. Case order and alternating arm order are fixed, not selected after seeing results.

The model chooses whether to issue a tool request or final answer. The harness never bypasses model inference for the negative case. It accepts only the exact frozen routing request, invokes a fixed executable with structured stdin and no shell, never executes model output as code, and never invokes the recommended downstream provider. The endpoint owns provider-policy enforcement. A correct positive answer without a real successful receipt containing a decision ID fails. A negative answer passes only with exact output and zero tool calls. Model self-evaluation never determines the score.

The common execution boundary is the calling host's process restrictions plus a fixed portable client; this does not claim a fresh OS/container sandbox for each trial. Candidate models have no general shell, filesystem or arbitrary network tool. Any stronger host sandbox must apply equally to both arms. Existing endpoint credentials are read from environment references, never included in prompts or report config.

```sh
python evaluation/t22/run_pairs.py
python evaluation/t22/run_pairs.py --execute --output /path/to/private/new-results
```

Use the existing approved `COFOUNDER_GATEWAY_URL`, `COFOUNDER_GATEWAY_API_KEY`, `SPARK_DECIDE_URL`, and `SPARK_DECIDE_TOKEN` environment references. The interpreter needs the project's existing Gateway client dependencies. No dependency installation or remote work occurs in dry-run. Run `test_harness.py` for four CPU fixture checks; those fixture outputs are explicitly not live trial evidence.

The complete batch has 6 trials, at most 12 agent-model calls plus 6 decision-tool calls (the refusal path may use no decision inference), concurrency 1, retries 0. Each trial grants at most 32,768 conservatively reserved agent tokens, at most 150 seconds overall with model/tool calls individually bounded to 65 seconds; the decision request separately permits at most 4,096 reserved tokens and one inference. A started call can complete near the outer deadline, but no additional call begins after it. The application policy can stop a trial earlier. All final failures remain recorded; no automatic rerun drops failures. Cloud candidate calls are prohibited in these trials.

Each trial records complete system/task prompts, raw model outputs, request IDs, selected model/provider, fallback status, usage, actual tool stdout/receipt, elapsed time, final JSON and deterministic checks. Tool stderr content is omitted because diagnostics might contain endpoint details. Individual first-step/receipt evidence is persisted before the next inference. `summary.json` retains usage for agent and decision calls separately; unknown costs stay null. A changed/missing model identity across trials invalidates the common-model comparison even if individual cases passed. Endpoint model/policy revisions, source commit, and host environment provenance must also accompany the stage's real run evidence.

A three-case result establishes only small-sample wiring. It does not establish general Skill Lift, probability calibration, official Tier3 PASS, complete security scanning, final signature, or T28 completion.
