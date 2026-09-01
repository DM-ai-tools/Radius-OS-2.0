#!/usr/bin/env python3
"""Raw-vs-rendered and mobile-vs-desktop HTML diffs for the rendering-audit skill.

Examples:
  python scripts/render_diff.py render https://example.com/page
  python scripts/render_diff.py render --file-raw raw.html --file-rendered rendered.html
  python scripts/render_diff.py parity https://example.com/page
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Install deps: pip install beautifulsoup4 requests", file=sys.stderr)
    raise SystemExit(2)

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _words(soup: BeautifulSoup) -> list[str]:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return [w for w in re.split(r"\s+", text) if w]


def _links(soup: BeautifulSoup, base: str | None = None) -> set[str]:
    out: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        if base:
            href = urljoin(base, href)
        out.add(href.split("#")[0])
    return out


def _title(soup: BeautifulSoup) -> str:
    t = soup.find("title")
    return (t.get_text(strip=True) if t else "") or ""


def _h1(soup: BeautifulSoup) -> str:
    h = soup.find("h1")
    return (h.get_text(strip=True) if h else "") or ""


def _canonical(soup: BeautifulSoup) -> str:
    link = soup.find("link", rel=lambda v: v and "canonical" in str(v).lower())
    return (link.get("href") or "").strip() if link else ""


def _robots(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": re.compile(r"^robots$", re.I)})
    return (meta.get("content") or "").strip() if meta else ""


def _json_ld_types(soup: BeautifulSoup) -> set[str]:
    types: set[str] = set()
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict) and item.get("@type"):
                t = item["@type"]
                if isinstance(t, list):
                    types.update(str(x) for x in t)
                else:
                    types.add(str(t))
    return types


def analyze_pair(
    baseline_html: str,
    reference_html: str,
    *,
    base_url: str | None = None,
    mode: str = "render",
) -> dict[str, Any]:
    """baseline = raw or mobile; reference = rendered or desktop."""
    base = _soup(baseline_html)
    ref = _soup(reference_html)
    bw, rw = _words(base), _words(ref)
    bl, rl = _links(base, base_url), _links(ref, base_url)
    ratio = (len(bw) / len(rw)) if rw else (1.0 if not bw else 0.0)
    only_after = sorted(rl - bl)
    only_before = sorted(bl - rl)
    findings: list[dict[str, str]] = []

    def add(sev: str, msg: str) -> None:
        findings.append({"severity": sev, "finding": msg})

    if len(rw) > 20 and ratio < 0.10:
        add("CRITICAL", f"Near-zero baseline content (ratio={ratio:.2f}) — empty shell risk")
    if not bl and rl:
        add("CRITICAL", "Zero links in baseline; links appear only after render/desktop")
    bt, rt = _title(base), _title(ref)
    if rt and not bt:
        add("CRITICAL", "Title only present after render/desktop")
    if rw and ratio < 0.70:
        add("HIGH", f"30%+ text missing from baseline (ratio={ratio:.2f})")
    if rl and len(only_after) >= max(3, int(0.5 * len(rl))):
        add("HIGH", f"Majority of links only after render/desktop ({len(only_after)} links)")
    bc, rc = _canonical(base), _canonical(ref)
    if rc and not bc:
        add("HIGH", "Canonical missing from baseline HTML")
    elif bc and rc and bc.split("#")[0] != rc.split("#")[0]:
        add("HIGH", f"Canonical conflict: baseline={bc!r} vs reference={rc!r}")
    br, rr = _robots(base), _robots(ref)
    if br.lower() != rr.lower() and (br or rr):
        add("HIGH", f"Meta robots differs: baseline={br!r} vs reference={rr!r}")
    if rt and bt and bt != rt:
        add("MEDIUM", "Title differs between baseline and reference")
    if _h1(ref) and not _h1(base):
        add("MEDIUM", "H1 only present after render/desktop")
    jt, jr = _json_ld_types(base), _json_ld_types(ref)
    if jr - jt:
        add("MEDIUM", f"Structured data types only after render: {sorted(jr - jt)}")
    if only_before and not only_after:
        add("LOW", f"{len(only_before)} links in baseline missing from reference")

    severities = {f["severity"] for f in findings}
    if "CRITICAL" in severities:
        verdict = "FAIL"
    elif findings:
        verdict = "WARN"
    else:
        verdict = "PASS"

    return {
        "mode": mode,
        "content_visible_ratio": round(ratio, 4),
        "baseline_words": len(bw),
        "reference_words": len(rw),
        "baseline_links": len(bl),
        "reference_links": len(rl),
        "links_only_after": only_after[:50],
        "links_only_before": only_before[:50],
        "title_baseline": bt,
        "title_reference": rt,
        "findings": findings,
        "verdict": verdict,
    }


def fetch_raw(url: str, ua: str = DESKTOP_UA) -> str:
    r = requests.get(url, headers={"User-Agent": ua}, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_rendered(url: str, ua: str = DESKTOP_UA) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright not installed. Use --file-raw/--file-rendered or: "
            "pip install playwright && playwright install chromium"
        ) from e
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=ua)
        page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()
    return html


def cmd_render(args: argparse.Namespace) -> int:
    if args.file_raw and args.file_rendered:
        raw = Path(args.file_raw).read_text(encoding="utf-8", errors="replace")
        rendered = Path(args.file_rendered).read_text(encoding="utf-8", errors="replace")
        base_url = args.url or None
        method = "manual capture"
    elif args.url:
        raw = fetch_raw(args.url)
        rendered = fetch_rendered(args.url)
        base_url = args.url
        method = "Playwright"
    else:
        print("Provide a URL or both --file-raw and --file-rendered", file=sys.stderr)
        return 2
    result = analyze_pair(raw, rendered, base_url=base_url, mode="render")
    result["method"] = method
    result["url"] = args.url or ""
    print(json.dumps(result, indent=2))
    return 1 if result["verdict"] == "FAIL" else 0


def cmd_parity(args: argparse.Namespace) -> int:
    if not args.url:
        print("parity requires a URL", file=sys.stderr)
        return 2
    try:
        mobile = fetch_rendered(args.url, ua=MOBILE_UA)
        desktop = fetch_rendered(args.url, ua=DESKTOP_UA)
        method = "Playwright"
    except RuntimeError:
        mobile = fetch_raw(args.url, ua=MOBILE_UA)
        desktop = fetch_raw(args.url, ua=DESKTOP_UA)
        method = "requests (no Playwright — approximate)"
    # baseline=mobile, reference=desktop (content missing on mobile is the risk)
    result = analyze_pair(mobile, desktop, base_url=args.url, mode="parity")
    result["method"] = method
    result["url"] = args.url
    print(json.dumps(result, indent=2))
    return 1 if result["verdict"] == "FAIL" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Rendering / parity HTML diff")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_render = sub.add_parser("render", help="Raw HTML vs post-JS DOM")
    p_render.add_argument("url", nargs="?", help="Page URL")
    p_render.add_argument("--file-raw", help="Path to raw View Source HTML")
    p_render.add_argument("--file-rendered", help="Path to rendered DOM HTML")
    p_render.set_defaults(func=cmd_render)

    p_parity = sub.add_parser("parity", help="Mobile vs desktop rendering")
    p_parity.add_argument("url", help="Page URL")
    p_parity.set_defaults(func=cmd_parity)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
