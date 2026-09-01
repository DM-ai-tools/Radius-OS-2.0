#!/usr/bin/env python3
"""Merge strategy + architecture + audit into a locked content_planning_report.

Usage (from backend/):
  python scripts/plan_roadmap.py build --strategy s.json --architecture a.json --audit u.json --out ./roadmap
  python scripts/plan_roadmap.py check ./roadmap/content_planning_report.json
  python scripts/plan_roadmap.py check ./roadmap/content_planning_report.json --strict
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.content_planning import build_roadmap, validate_report  # noqa: E402


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"{path} must be a JSON object")
    return data


def cmd_build(args: argparse.Namespace) -> int:
    strategy = _load(Path(args.strategy))
    architecture = _load(Path(args.architecture))
    audit = _load(Path(args.audit)) if args.audit else {}
    report = build_roadmap(strategy, architecture, audit, strict=bool(args.strict))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "content_planning_report.json"
    dest.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(dest)
    print(
        f"locked={report.get('locked')} merged={report['summary']['merged']} "
        f"excluded={report['summary']['excluded']} briefable={report['summary']['briefable']}"
    )
    if not report.get("locked"):
        print(f"lock_reason: {report.get('lock_reason')}", file=sys.stderr)
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    report = _load(Path(args.report))
    result = validate_report(report, strict=bool(args.strict))
    print(json.dumps(result, indent=2))
    if not result.get("ok"):
        return 1
    if report.get("locked") is False:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Lock a content planning roadmap")
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Join three packs and write content_planning_report.json")
    b.add_argument("--strategy", required=True)
    b.add_argument("--architecture", required=True)
    b.add_argument("--audit", default=None)
    b.add_argument("--out", required=True)
    b.add_argument("--strict", action="store_true")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("check", help="Validate an existing report")
    c.add_argument("report")
    c.add_argument("--strict", action="store_true")
    c.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
