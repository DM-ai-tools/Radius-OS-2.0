#!/usr/bin/env python3
"""CDD checklist — validate APSA Client Discovery Document (no LLM).

Usage (from backend/):
  python scripts/cdd_checklist.py path/to/cdd.xlsx
  python scripts/cdd_checklist.py path/to/cdd.json
  python scripts/cdd_checklist.py --template > cdd_blank.json
  python scripts/cdd_checklist.py cdd.json --out report.json

Exit codes: 0 = ready/partial, 2 = blocked (P0 missing).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python scripts/cdd_checklist.py` from backend/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.cdd_checklist import (
    analyse_cdd,
    empty_template,
    format_report,
    load_cdd,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate CDD and split Discovery sub-phase load")
    parser.add_argument("cdd", nargs="?", help="Path to CDD .json / .xlsx / .csv")
    parser.add_argument("--template", action="store_true", help="Print blank JSON template")
    parser.add_argument("--out", help="Write full JSON report to this path")
    parser.add_argument("--quiet", action="store_true", help="JSON only on stdout")
    args = parser.parse_args()

    if args.template:
        print(json.dumps(empty_template(), indent=2))
        return 0

    if not args.cdd:
        parser.error("Provide a CDD path, or use --template")

    fields = load_cdd(args.cdd)
    report = analyse_cdd(fields)
    report["source"] = str(Path(args.cdd).resolve())

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    if args.quiet:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
        if args.out:
            print(f"\nWrote JSON report → {args.out}")

    return 2 if report.get("verdict") == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
