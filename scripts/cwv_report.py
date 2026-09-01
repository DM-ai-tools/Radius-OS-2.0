#!/usr/bin/env python3
"""Core Web Vitals reporting via PageSpeed Insights / CrUX field data.

Examples:
  export PSI_API_KEY=...
  python scripts/cwv_report.py origin https://example.com --desktop
  python scripts/cwv_report.py urls urls.txt --lab --out ./cwv

urls.txt format (tab-separated):
  https://example.com/          homepage
  https://example.com/product/x product
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("Install deps: pip install requests pandas", file=sys.stderr)
    raise SystemExit(2)

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


def _band(metric: str, value: float | None) -> str:
    if value is None:
        return "no_data"
    # thresholds at p75
    if metric == "LCP":  # seconds
        if value <= 2.5:
            return "good"
        if value <= 4.0:
            return "needs_improvement"
        return "poor"
    if metric == "INP":  # ms
        if value <= 200:
            return "good"
        if value <= 500:
            return "needs_improvement"
        return "poor"
    if metric == "CLS":
        if value <= 0.10:
            return "good"
        if value <= 0.25:
            return "needs_improvement"
        return "poor"
    if metric == "TTFB":  # ms
        if value <= 800:
            return "good"
        if value <= 1800:
            return "needs_improvement"
        return "poor"
    return "unknown"


def _ms(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _crux_p75(metrics: dict[str, Any], key: str, *, unit: str = "ms") -> float | None:
    block = metrics.get(key) or {}
    p75 = (block.get("percentile") if isinstance(block, dict) else None)
    if p75 is None and isinstance(block, dict):
        p75 = (block.get("percentiles") or {}).get("p75")
    val = _ms(p75)
    if val is None:
        return None
    # PSI often returns LCP in ms
    if unit == "s" and val > 50:
        return round(val / 1000.0, 3)
    return val


def fetch_psi(url: str, *, strategy: str, api_key: str, categories: list[str]) -> dict[str, Any]:
    params = {
        "url": url,
        "strategy": strategy,
        "key": api_key,
    }
    for cat in categories:
        params.setdefault("category", [])
        # requests encodes repeated keys if we pass a list via params carefully
    # Build manually for repeated category
    q = [("url", url), ("strategy", strategy), ("key", api_key)]
    for cat in categories:
        q.append(("category", cat))
    r = requests.get(PSI_ENDPOINT, params=q, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"PSI {r.status_code} for {url}: {r.text[:300]}")
    return r.json()


def extract_field(payload: dict[str, Any]) -> dict[str, Any]:
    loading = (payload.get("loadingExperience") or {})
    metrics = loading.get("metrics") or {}
    overall = loading.get("overall_category")
    if not metrics:
        return {
            "has_field_data": False,
            "overall": None,
            "lcp_p75_s": None,
            "inp_p75_ms": None,
            "cls_p75": None,
            "ttfb_p75_ms": None,
            "lcp_band": "no_data",
            "inp_band": "no_data",
            "cls_band": "no_data",
            "ttfb_band": "no_data",
        }
    lcp = _crux_p75(metrics, "LARGEST_CONTENTFUL_PAINT_MS", unit="s")
    inp = _crux_p75(metrics, "INTERACTION_TO_NEXT_PAINT", unit="ms")
    if inp is None:
        inp = _crux_p75(metrics, "EXPERIMENTAL_INTERACTION_TO_NEXT_PAINT", unit="ms")
    cls = _crux_p75(metrics, "CUMULATIVE_LAYOUT_SHIFT_SCORE", unit="cls")
    # CLS percentile is often ×100 in PSI (e.g. 10 = 0.10)
    if cls is not None and cls > 1:
        cls = round(cls / 100.0, 3)
    ttfb = _crux_p75(metrics, "EXPERIMENTAL_TIME_TO_FIRST_BYTE", unit="ms")
    return {
        "has_field_data": True,
        "overall": overall,
        "lcp_p75_s": lcp,
        "inp_p75_ms": inp,
        "cls_p75": cls,
        "ttfb_p75_ms": ttfb,
        "lcp_band": _band("LCP", lcp),
        "inp_band": _band("INP", inp),
        "cls_band": _band("CLS", cls),
        "ttfb_band": _band("TTFB", ttfb),
    }


def extract_lab(payload: dict[str, Any]) -> dict[str, Any]:
    lh = payload.get("lighthouseResult") or {}
    cats = (lh.get("categories") or {}).get("performance") or {}
    audits = lh.get("audits") or {}

    def audit_numeric(aid: str) -> float | None:
        a = audits.get(aid) or {}
        return _ms(a.get("numericValue"))

    lcp_ms = audit_numeric("largest-contentful-paint")
    tbt_ms = audit_numeric("total-blocking-time")
    cls = audit_numeric("cumulative-layout-shift")
    ttfb_ms = audit_numeric("server-response-time")
    opportunities = []
    for aid, a in audits.items():
        if not isinstance(a, dict):
            continue
        details = a.get("details") or {}
        if details.get("type") != "opportunity":
            continue
        savings = details.get("overallSavingsMs")
        if savings:
            opportunities.append(
                {
                    "id": aid,
                    "title": a.get("title"),
                    "savings_ms": savings,
                }
            )
    opportunities.sort(key=lambda x: -(x.get("savings_ms") or 0))
    return {
        "performance_score": cats.get("score"),
        "lab_lcp_s": round(lcp_ms / 1000.0, 3) if lcp_ms is not None else None,
        "lab_tbt_ms": tbt_ms,
        "lab_cls": cls,
        "lab_ttfb_ms": ttfb_ms,
        "opportunities": opportunities[:15],
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for k in row:
            if k not in keys and k != "opportunities":
                keys.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k) for k in keys})


def median(vals: list[float]) -> float | None:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return float(statistics.median(clean))


def load_url_list(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            url, template = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            url = parts[0]
            template = parts[1] if len(parts) > 1 else "unspecified"
        rows.append((url.strip(), template.strip() or "unspecified"))
    return rows


def cmd_origin(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get("PSI_API_KEY") or os.environ.get("PAGESPEED_API_KEY")
    if not api_key:
        print("Set PSI_API_KEY or pass --api-key", file=sys.stderr)
        return 2
    strategy = "desktop" if args.desktop else "mobile"
    out = Path(args.out or ".")
    categories = ["performance"]
    try:
        payload = fetch_psi(args.url, strategy=strategy, api_key=api_key, categories=categories)
    except Exception as e:  # noqa: BLE001
        print(f"PSI error: {e}", file=sys.stderr)
        return 1
    field = extract_field(payload)
    # Origin experience if present
    origin = payload.get("originLoadingExperience") or {}
    origin_metrics = origin.get("metrics") or {}
    origin_row = {
        "url": args.url,
        "strategy": strategy,
        "overall": origin.get("overall_category") or field.get("overall"),
        "has_field_data": bool(origin_metrics) or field["has_field_data"],
    }
    if origin_metrics:
        lcp = _crux_p75(origin_metrics, "LARGEST_CONTENTFUL_PAINT_MS", unit="s")
        inp = _crux_p75(origin_metrics, "INTERACTION_TO_NEXT_PAINT", unit="ms")
        if inp is None:
            inp = _crux_p75(origin_metrics, "EXPERIMENTAL_INTERACTION_TO_NEXT_PAINT", unit="ms")
        cls = _crux_p75(origin_metrics, "CUMULATIVE_LAYOUT_SHIFT_SCORE", unit="cls")
        if cls is not None and cls > 1:
            cls = round(cls / 100.0, 3)
        ttfb = _crux_p75(origin_metrics, "EXPERIMENTAL_TIME_TO_FIRST_BYTE", unit="ms")
        origin_row.update(
            {
                "lcp_p75_s": lcp,
                "inp_p75_ms": inp,
                "cls_p75": cls,
                "ttfb_p75_ms": ttfb,
                "lcp_band": _band("LCP", lcp),
                "inp_band": _band("INP", inp),
                "cls_band": _band("CLS", cls),
                "ttfb_band": _band("TTFB", ttfb),
            }
        )
    else:
        origin_row.update(field)
    write_csv(out / "field_origin.csv", [origin_row])
    print(json.dumps(origin_row, indent=2))
    if not origin_row.get("has_field_data"):
        print(
            "Note: No CrUX field data for this origin/URL. "
            "Do not present lab scores as real-user performance.",
            file=sys.stderr,
        )
    return 0


def cmd_urls(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get("PSI_API_KEY") or os.environ.get("PAGESPEED_API_KEY")
    if not api_key:
        print("Set PSI_API_KEY or pass --api-key", file=sys.stderr)
        return 2
    strategy = "desktop" if args.desktop else "mobile"
    out = Path(args.out or "./cwv")
    pairs = load_url_list(Path(args.urls_file))
    if not pairs:
        print("No URLs found in file", file=sys.stderr)
        return 2

    field_rows: list[dict[str, Any]] = []
    lab_rows: list[dict[str, Any]] = []
    opportunities: dict[str, list] = {}

    for i, (url, template) in enumerate(pairs):
        cats = ["performance"] if args.lab else ["performance"]
        try:
            payload = fetch_psi(url, strategy=strategy, api_key=api_key, categories=cats)
        except Exception as e:  # noqa: BLE001
            field_rows.append(
                {
                    "url": url,
                    "template": template,
                    "strategy": strategy,
                    "has_field_data": False,
                    "error": str(e)[:200],
                    "lcp_band": "no_data",
                    "inp_band": "no_data",
                    "cls_band": "no_data",
                }
            )
            time.sleep(1)
            continue
        field = extract_field(payload)
        field_rows.append({"url": url, "template": template, "strategy": strategy, **field})
        if args.lab:
            lab = extract_lab(payload)
            lab_rows.append(
                {
                    "url": url,
                    "template": template,
                    "strategy": strategy,
                    "performance_score": lab.get("performance_score"),
                    "lab_lcp_s": lab.get("lab_lcp_s"),
                    "lab_tbt_ms": lab.get("lab_tbt_ms"),
                    "lab_cls": lab.get("lab_cls"),
                    "lab_ttfb_ms": lab.get("lab_ttfb_ms"),
                }
            )
            opportunities[url] = lab.get("opportunities") or []
        # polite rate limit
        if i < len(pairs) - 1:
            time.sleep(1.2)

    # Template medians
    by_t: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in field_rows:
        by_t[str(row.get("template") or "unspecified")].append(row)

    template_rows: list[dict[str, Any]] = []
    for template, rows in sorted(by_t.items()):
        with_data = [r for r in rows if r.get("has_field_data")]
        lcp = median([r["lcp_p75_s"] for r in with_data if r.get("lcp_p75_s") is not None])
        inp = median([r["inp_p75_ms"] for r in with_data if r.get("inp_p75_ms") is not None])
        cls = median([r["cls_p75"] for r in with_data if r.get("cls_p75") is not None])
        ttfb = median([r["ttfb_p75_ms"] for r in with_data if r.get("ttfb_p75_ms") is not None])
        bands = [_band("LCP", lcp), _band("INP", inp), _band("CLS", cls)]
        if "poor" in bands:
            verdict = "poor"
        elif "needs_improvement" in bands:
            verdict = "needs_improvement"
        elif with_data:
            verdict = "good"
        else:
            verdict = "no_field_data"
        template_rows.append(
            {
                "template": template,
                "urls_sampled": len(rows),
                "urls_with_field_data": len(with_data),
                "lcp_p75_s": lcp,
                "inp_p75_ms": inp,
                "cls_p75": cls,
                "ttfb_p75_ms": ttfb,
                "lcp_band": _band("LCP", lcp),
                "inp_band": _band("INP", inp),
                "cls_band": _band("CLS", cls),
                "ttfb_band": _band("TTFB", ttfb),
                "verdict": verdict,
            }
        )

    write_csv(out / "field_by_url.csv", field_rows)
    write_csv(out / "field_by_template.csv", template_rows)
    if args.lab:
        write_csv(out / "lab_by_url.csv", lab_rows)
        (out / "lab_opportunities.json").write_text(
            json.dumps(opportunities, indent=2), encoding="utf-8"
        )

    summary = {
        "strategy": strategy,
        "urls": len(pairs),
        "templates": len(template_rows),
        "field_by_template": template_rows,
        "note": (
            "Lead with field data. Lab scores are explanatory only — "
            "never present them as real-user performance."
        ),
    }
    print(json.dumps(summary, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="CWV field/lab reporting via PSI")
    parser.add_argument("--api-key", help="PageSpeed Insights API key (or PSI_API_KEY env)")
    parser.add_argument("--desktop", action="store_true", help="Use desktop strategy (default mobile)")
    parser.add_argument("--out", default=".", help="Output directory")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_origin = sub.add_parser("origin", help="Whole-origin CrUX baseline")
    p_origin.add_argument("url", help="Origin or page URL")
    p_origin.set_defaults(func=cmd_origin)

    p_urls = sub.add_parser("urls", help="Per-URL / per-template measurements")
    p_urls.add_argument("urls_file", help="Tab-separated URL\\ttemplate file")
    p_urls.add_argument("--lab", action="store_true", help="Also pull Lighthouse lab metrics")
    p_urls.set_defaults(func=cmd_urls)

    args = parser.parse_args()
    # Normalize out default for urls
    if args.cmd == "urls" and args.out == ".":
        args.out = "./cwv"
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
