#!/usr/bin/env python3
"""Print a skill-format technical SEO audit for a URL."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.integrations.providers import run_technical_seo_audit


def _configure_stdout() -> None:
    """Windows consoles default to cp1252; audit findings use Unicode arrows."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main() -> int:
    _configure_stdout()
    url = sys.argv[1] if len(sys.argv) > 1 else "https://clicktrends.com.au/"
    name = sys.argv[2] if len(sys.argv) > 2 else url
    report = await run_technical_seo_audit(url, display_name=name)
    print("## Technical SEO Audit Report\n")
    print(f"**Site**: {report.get('site')}")
    print(f"**Score**: {report.get('score')}/100\n")
    for section, data in (report.get("sections") or {}).items():
        score = data.get("score")
        label = f"{score}/100" if score is not None else "not measured"
        print(f"### {section.replace('_', ' ').title()}: {label}")
        for finding in data.get("findings") or []:
            print(f"- {finding}")
        print()
    print("### Priority Fixes")
    for i, fix in enumerate(report.get("priority_fixes") or [], 1):
        print(f"{i}. **[{fix.get('priority')}]** {fix.get('issue')} — {fix.get('fix')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
