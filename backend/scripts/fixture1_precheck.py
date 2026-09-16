"""Fixture 1 (NO DEMAND) precheck — volume-check hyper-narrow industrial clusters.

The fixture is defined by the topic, not the company: we need a cluster whose
ENTIRE addressable volume is under ~500/mo, otherwise the fixture proves nothing.

Tries Ahrefs first (as instructed), then DataForSEO, and reports which source
produced each number. Never merges the two into one figure.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CLUSTERS: dict[str, list[str]] = {
    "ultrasonic level transmitter calibration": [
        "ultrasonic level transmitter calibration",
        "ultrasonic level transmitter",
        "ultrasonic level sensor calibration",
        "level transmitter calibration procedure",
        "calibrate ultrasonic level sensor",
    ],
    "EMI shielding gasket selection": [
        "emi shielding gasket selection",
        "emi shielding gasket",
        "emi gasket material",
        "emi shielding gasket types",
        "rf shielding gasket selection guide",
    ],
    "cleanroom tacky mats": [
        "cleanroom tacky mats",
        "tacky mat",
        "sticky mat cleanroom",
        "cleanroom floor mat",
        "adhesive mat cleanroom",
    ],
    "borosilicate sight glasses": [
        "borosilicate sight glass",
        "sight glass borosilicate",
        "industrial sight glass",
        "sight glass for tanks",
        "borosilicate viewing window",
    ],
    "vacuum feedthroughs": [
        "vacuum feedthrough",
        "electrical vacuum feedthrough",
        "high vacuum feedthrough",
        "vacuum feedthrough connector",
        "cf flange feedthrough",
    ],
}


async def main() -> int:
    from app.integrations import ahrefs, dataforseo

    out: dict = {"clusters": {}, "sources": {}}
    print(f"{'cluster':<44}{'source':<12}{'total/mo':>10}  keywords with volume")
    print("-" * 100)

    for label, keywords in CLUSTERS.items():
        rows: list[dict] = []
        source = None
        errors: list[str] = []

        a_rows, a_err = await ahrefs.keyword_overview(keywords, country="us")
        if a_rows:
            rows, source = a_rows, "ahrefs"
        else:
            errors.extend(a_err or ["ahrefs_no_rows"])
            # NOTE: dataforseo.search_volume() is broken — _task_items expects the
            # Labs shape result[0].items[], but google_ads/search_volume returns
            # rows directly in result[], so it silently returns ([], []). Ledgered
            # as P0-4. Parse the raw response here rather than fix pipeline code
            # before the finding is ledgered.
            body = await dataforseo._post(
                "/keywords_data/google_ads/search_volume/live",
                [{"keywords": [k for k in keywords],
                  "location_code": dataforseo.DEFAULT_LOCATION,
                  "language_code": dataforseo.DEFAULT_LANGUAGE}],
            )
            result = ((body or {}).get("tasks") or [{}])[0].get("result") or []
            d_rows = [{"keyword": r.get("keyword"), "volume": r.get("search_volume"),
                       "competition": r.get("competition"), "cpc": r.get("cpc")}
                      for r in result if isinstance(r, dict)]
            if d_rows:
                rows, source = d_rows, "dataforseo(raw)"
            else:
                errors.append("dataforseo_empty_result")

        vols = {}
        for r in rows:
            kw = str(r.get("keyword") or "")
            v = r.get("volume")
            if kw:
                vols[kw] = v
        total = sum(int(v) for v in vols.values() if isinstance(v, (int, float)))
        withvol = sum(1 for v in vols.values() if isinstance(v, (int, float)) and v)
        out["clusters"][label] = {
            "source": source, "total_monthly_volume": total if source else None,
            "keywords": vols, "errors": errors,
        }
        src = source or "UNAVAILABLE"
        tot = f"{total}" if source else "-"
        print(f"{label:<44}{src:<12}{tot:>10}  {withvol}/{len(keywords)}")
        for kw, v in vols.items():
            print(f"    {str(v):>8}  {kw}")
        if errors:
            print(f"    errors: {errors[:2]}")

    Path(ROOT.parent / "validation").mkdir(exist_ok=True)
    (ROOT.parent / "validation" / "fixture1-precheck.json").write_text(
        json.dumps(out, indent=1, default=str), encoding="utf-8")
    print("\nwritten: validation/fixture1-precheck.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
