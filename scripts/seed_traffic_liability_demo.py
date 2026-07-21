#!/usr/bin/env python3
"""Create a video-ready traffic-liability demo in an isolated data root."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a synthetic, non-authoritative traffic-liability Run without "
            "calling a live model."
        )
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get(
            "TRAFFIC_DEMO_DATA_DIR",
            "/tmp/cofounder-os-traffic-demo/data",
        ),
        help="Isolated Product API data directory.",
    )
    parser.add_argument(
        "--case-file",
        default=str(
            REPOSITORY_ROOT / "examples" / "traffic-liability-demo-case.json"
        ),
        help="Synthetic case fixture JSON.",
    )
    parser.add_argument(
        "--force-new",
        action="store_true",
        help="Create another Run even if this case is already present.",
    )
    return parser.parse_args()


def main() -> int:
    from app.demo import TrafficLiabilityDemoError, seed_traffic_liability_demo

    args = _arguments()
    try:
        result = seed_traffic_liability_demo(
            args.data_dir,
            args.case_file,
            force_new=args.force_new,
        )
    except TrafficLiabilityDemoError as exc:
        print("FINAL_RESULT=FAIL")
        print(f"ERROR={exc}")
        return 1

    print("FINAL_RESULT=PASS")
    print(f"RUN_ID={result.run_id}")
    print(f"APPROVAL_ID={result.approval_id}")
    print(f"CASE_ID={result.case_id}")
    print(f"DATA_DIR={result.data_dir}")
    print(f"CREATED={'true' if result.created else 'false'}")
    print("INFERENCE_MODE=deterministic_demo_fixture")
    print("MODEL_CALL_PERFORMED=false")
    print("OPEN_URL=http://127.0.0.1:9100/ui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
