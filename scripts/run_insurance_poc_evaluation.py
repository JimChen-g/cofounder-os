#!/usr/bin/env python3
"""Run and save the six-sample insurance POC demo evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.insurance_poc.evaluation import InsurancePOCDemoEvaluator  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "tmp" / "insurance-poc-demo-evaluation",
        help="Persisted evaluation Run and Artifact root.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "examples" / "insurance-poc" / "demo-evaluation-results.json",
        help="Evaluation result JSON path.",
    )
    args = parser.parse_args()
    result = InsurancePOCDemoEvaluator(
        fixture_dir=ROOT / "examples" / "insurance-poc",
        data_dir=args.data_dir,
    ).run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"demo evaluation: {result.sample_size} samples; "
        f"baseline completion={result.baseline.task_completion_rate:.1%}; "
        f"CoFounder OS completion={result.cofounder_os.task_completion_rate:.1%}; "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
