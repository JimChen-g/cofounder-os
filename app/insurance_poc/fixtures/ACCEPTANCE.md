# Insurance POC golden-demo acceptance

## Stable input check

- `scenario.json` validates as the frozen synthetic Mission, budget, privacy,
  project-status, and expected image-finding source.
- `insurance-poc-requirements.pdf` opens, renders as two legible pages, and
  yields non-empty extracted text.
- `accident-intersection.png` and `accident-damage.png` open as 1400 x 900 PNGs.
- `asset-manifest.json` matches SHA-256 and byte size for all three binaries.

## End-to-end acceptance

1. Launch the Insurance POC path with the fixed Founder Mission, PDF, and both
   images.
2. Evidence Board shows business, accident, technical, financial, and
   compliance/constraint evidence with source, modality, confidence, privacy,
   Agent use, Adapter, and status.
3. Unknown or corrupt input produces an explicit recoverable error; it never
   becomes empty evidence.
4. Product, Finance, and Risk output cite at least one Evidence ID, including
   one PDF-derived and one image-derived fact.
5. Ten routing records show candidate capability, modality, privacy, provider
   health, context, latency, cost, fallback, and validation decisions. An
   unavailable local candidate selects and executes the declared local fallback.
6. Product's requested scope exceeds the CNY 50,000 budget; Finance removes the
   CNY 18,000 optional write-back and preserves CNY 5,000 reserve.
7. Risk or Verifier changes any autonomous/authoritative liability phrasing to
   "model recommendation plus human review" and preserves both versions.
8. Six required files are independently viewable and carry source Agent,
   Evidence, version, and validation metadata.
9. Policy records a real denial for private accident-data upload and separately
   creates a pending Founder approval for the sanitized release receipt.
10. Six CoFounder OS samples are measured from persisted run records. A live
    single-model baseline is marked unavailable unless it actually runs; no fake
    score or comparative delta is shown.
11. Success, fallback/recovery, and policy-block flows pass; the stable demo
    succeeds twice without losing state after reload.
12. One PDF plus one PNG is the minimum accepted contract; the stable fixture
    uses two PNGs. Per-file, total-body, PDF-page, and extracted-text limits fail
    explicitly and recoverably.
13. Founder approval requires the run-specific HttpOnly capability issued when
    the Mission is created; a claimed reviewer name alone is insufficient.

## Honesty boundary

The two image findings are produced by a deterministic SHA-256-bound synthetic
fixture Adapter. The UI and Artifacts must not describe them as live Qwen or
Step inference. Unknown images are unsupported until the formal multimodal
Adapter is configured.
