# T21 frozen atomic decision collection

Version `t21-atomic-v1` freezes **24 synthetic instances / 8 families**: train 12 (four families), calibration 6 (two), final test 6 (two). The target is 150; the shortfall is **126**. This is a small collection starting point, not a 150-item corpus, measured quality advantage, trained router, or calibrated probability model. No business-real instances were invented. Each family's cases exercise substantive value/type/boundary differences; no paraphrase expansion was used.

`manifest.json` freezes membership and hashes before new candidate calls. `development-cases.json` contains train/calibration prompts and exact reference outputs. Final-test prompts and answers are in the operator's private directory outside the repository; only commitments are published. Do not copy them into the Skill, agent workspace, examples, fixtures, prompts, or implementation-agent context. The freeze author necessarily handled the answer key; the implementation agent and candidate models receive no final-test answers. An evaluator-only release in the final evaluation phase must verify the recorded digest. Any test-based model/prompt/threshold selection invalidates its holdout status and requires a new untouched family split.

All variants from one family stay together. The two earlier T15 materials-manifest examples and their four actual candidate outputs remain `smoke`; they cannot be moved into test after their results were seen. The 24 engineering debugging attempts / 29 model responses remain excluded. None of those counts are added to the 24-instance freeze.

Scoring `json-exact-types-v1`: parse a single JSON value; require exact recursively matched field sets, list order/length, values and JSON-decoded types. Extra prose/keys, wrong booleans, or malformed JSON fail. `collect.py` is the executable scorer. Model self-rating, routing decisions, approval, latency and inferred cost never supply ground-truth labels. Actual outputs plus scorer reconstruct `local`, `step`, `both`, or `neither`; incomplete pairs stay `incomplete`. Unknown cost stays null. No labels are preassigned for future provider outcomes.

Run from the repository using its Python environment:

```sh
python evaluation/t21/collect.py --limit 3
python evaluation/t21/collect.py --execute --limit 3 --output /private/operator/chosen/new-output-dir
```

The runner inherits existing `COFOUNDER_GATEWAY_URL` / `COFOUNDER_GATEWAY_API_KEY` references; never put values in committed files. It calls the existing authenticated Gateway, narrows every call to exactly one allowed provider, forbids fallback, never executes candidate output, and uses only public synthetic prompts. Use the approved Step Plan behind that Gateway, not a direct ordinary billing endpoint. The operator schedules the GPU window before `--execute`.

Per batch: at most 12 train instances × 2 candidates, 12 Step calls, concurrency 1, retries 0, 512 output tokens/request, 8,192 conservative reserved tokens/pair, 90-second pair budget, 600-second batch start cutoff, and 196,608 reported-token cutoff. An in-flight request may finish after the batch cutoff but remains bounded by its pair timeout. Each result is saved immediately, errors are sanitized, and existing output directories are rejected. Calibration is frozen but intentionally not collected or used for selection in T21. Final test is not addressable by this runner. The target of 150 does not authorize automatic data expansion.

T23 training and T24 calibration remain future work. Candidate model/policy/environment versions must be recorded with each real batch's deployment manifest; freeze metadata cannot substitute for runtime provenance.
