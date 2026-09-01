#!/usr/bin/env python3
"""Site architecture audit — click depth, phantoms, orphans, URL conventions.

Usage (from backend/):
  python scripts/architecture_audit.py crawl https://example.com crawl.jl --page-cap 500
  python scripts/architecture_audit.py audit crawl.jl --domain example.com --out ./ia

Prefers advertools when installed; otherwise uses a lightweight httpx BFS crawler.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse


def _norm_url(url: str, *, keep_query: bool = False) -> str:
    p = urlparse(url)
    scheme = (p.scheme or "https").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") + "/"  # keep trailing slash if present; normalize later separately
    path = re.sub(r"/{2,}", "/", path)
    query = p.query if keep_query else ""
    return urlunparse((scheme, netloc, path, "", query, ""))


def _same_host(a: str, b: str) -> bool:
    return urlparse(a).netloc.lower().removeprefix("www.") == urlparse(b).netloc.lower().removeprefix(
        "www."
    )


def _dir_depth(url: str) -> int:
    path = urlparse(url).path.strip("/")
    if not path:
        return 0
    return len([s for s in path.split("/") if s])


def crawl_light(seed: str, out_jl: Path, *, page_cap: int = 500, delay: float = 0.25) -> None:
    try:
        import httpx
        from html.parser import HTMLParser
    except ImportError as exc:
        raise SystemExit("httpx required for light crawl") from exc

    class LinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.hrefs: list[str] = []
            self.canonical = ""
            self.title = ""
            self._in_title = False
            self._buf = ""

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            ad = {k: (v or "") for k, v in attrs}
            if tag == "a" and ad.get("href"):
                self.hrefs.append(ad["href"])
            elif tag == "link" and "canonical" in (ad.get("rel") or "").lower():
                self.canonical = ad.get("href", "")
            elif tag == "title":
                self._in_title = True
                self._buf = ""

        def handle_data(self, data: str) -> None:
            if self._in_title:
                self._buf += data

        def handle_endtag(self, tag: str) -> None:
            if tag == "title" and self._in_title:
                self._in_title = False
                self.title = self._buf.strip()

    seed = seed.rstrip("/") + "/" if not urlparse(seed).path else seed
    host = urlparse(seed).netloc
    seen: set[str] = set()
    q: deque[tuple[str, int]] = deque([(seed, 0)])
    rows: list[dict[str, Any]] = []

    with httpx.Client(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "RadiusOS-ArchitectureAudit/1.0"},
    ) as client:
        while q and len(rows) < page_cap:
            url, depth = q.popleft()
            key = _norm_url(url)
            if key in seen:
                continue
            seen.add(key)
            try:
                resp = client.get(url)
            except Exception as exc:  # noqa: BLE001
                rows.append(
                    {
                        "url": url,
                        "status": 0,
                        "depth": depth,
                        "error": str(exc)[:200],
                        "links_url": [],
                        "links_nofollow": [],
                    }
                )
                continue
            final = str(resp.url)
            parser = LinkParser()
            try:
                parser.feed(resp.text or "")
            except Exception:  # noqa: BLE001
                pass
            abs_links = []
            for href in parser.hrefs:
                abs_u = urljoin(final, href)
                if abs_u.startswith(("mailto:", "tel:", "javascript:")):
                    continue
                if _same_host(seed, abs_u):
                    abs_links.append(_norm_url(abs_u.split("#")[0], keep_query=True))
            rows.append(
                {
                    "url": final,
                    "status": resp.status_code,
                    "depth": depth,
                    "title": parser.title,
                    "canonical": urljoin(final, parser.canonical) if parser.canonical else "",
                    "links_url": abs_links,
                    "links_nofollow": [False] * len(abs_links),
                    "dir_depth": _dir_depth(final),
                }
            )
            if resp.status_code < 400:
                for link in abs_links:
                    if _norm_url(link) not in seen and len(seen) + len(q) < page_cap * 2:
                        q.append((link, depth + 1))
            time.sleep(delay)

    out_jl.parent.mkdir(parents=True, exist_ok=True)
    with out_jl.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"Wrote {len(rows)} rows to {out_jl} (host={host})")


def crawl_advertools(seed: str, out_jl: Path, *, page_cap: int = 5000, delay: float = 0.25) -> None:
    import advertools as adv

    out_jl.parent.mkdir(parents=True, exist_ok=True)
    adv.crawl(
        seed,
        str(out_jl),
        follow_links=True,
        custom_settings={
            "CLOSESPIDER_PAGECOUNT": page_cap,
            "DOWNLOAD_DELAY": delay,
            "LOG_FILE": str(out_jl.with_suffix(".log")),
            "USER_AGENT": "RadiusOS-ArchitectureAudit/1.0 (+compatible; research)",
        },
    )
    print(f"advertools crawl complete → {out_jl}")


def _load_jl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "links_nofollow" not in row:
                links = row.get("links_url") or row.get("links") or []
                if isinstance(links, str):
                    links = [links]
                row["links_url"] = links
                row["links_nofollow"] = [False] * len(links)
            rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def audit(jl_path: Path, *, domain: str, out_dir: Path) -> dict[str, Any]:
    rows = _load_jl(jl_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    domain = domain.lower().removeprefix("www.")

    pages = []
    for r in rows:
        url = str(r.get("url") or "")
        if not url:
            continue
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if domain and host and host != domain:
            continue
        pages.append(r)

    status_200 = sum(1 for p in pages if int(p.get("status") or 0) == 200)
    depths = [int(p.get("depth") or 0) for p in pages]
    max_depth = max(depths) if depths else 0
    within_3 = sum(1 for d in depths if d <= 3)
    depth_4_plus = [p for p in pages if int(p.get("depth") or 0) >= 4]

    # Link graph
    inbound: dict[str, set[str]] = defaultdict(set)
    outbound: dict[str, set[str]] = defaultdict(set)
    url_set = {_norm_url(str(p.get("url") or "")) for p in pages}
    for p in pages:
        src = _norm_url(str(p.get("url") or ""))
        for link in p.get("links_url") or []:
            dst = _norm_url(str(link).split("#")[0])
            if dst in url_set:
                outbound[src].add(dst)
                inbound[dst].add(src)

    orphans = [
        {"url": p.get("url"), "status": p.get("status"), "depth": p.get("depth")}
        for p in pages
        if _norm_url(str(p.get("url") or "")) not in inbound
        and int(p.get("depth") or 0) > 0
        and int(p.get("status") or 0) == 200
    ]
    dead_ends = [
        {"url": p.get("url"), "status": p.get("status"), "depth": p.get("depth")}
        for p in pages
        if not outbound.get(_norm_url(str(p.get("url") or "")))
        and int(p.get("status") or 0) == 200
    ]

    # Phantom directories: path prefixes that never appear as a page URL
    page_paths = set()
    all_segments: set[str] = set()
    for p in pages:
        path = urlparse(str(p.get("url") or "")).path
        if not path.endswith("/") and "." not in path.rsplit("/", 1)[-1]:
            path = path  # page
        page_paths.add(path.rstrip("/") or "/")
        parts = [x for x in path.strip("/").split("/") if x]
        for i in range(1, len(parts)):
            all_segments.add("/" + "/".join(parts[:i]))

    phantoms = []
    for seg in sorted(all_segments):
        if seg.rstrip("/") not in {p.rstrip("/") for p in page_paths} and seg not in page_paths:
            # skip pure date phantoms lightly flagged
            phantoms.append({"directory": seg, "note": "No page at this path segment"})

    # URL convention violations
    violations = []
    for p in pages:
        url = str(p.get("url") or "")
        path = urlparse(url).path
        issues = []
        if any(c.isupper() for c in path):
            issues.append("uppercase")
        if "_" in path:
            issues.append("underscore")
        if re.search(r"/20\d{2}/", path):
            issues.append("date_folder")
        if path.endswith((".php", ".asp", ".html", ".htm")):
            issues.append("file_extension")
        if "session" in url.lower() or "phpsessid" in url.lower():
            issues.append("session_param")
        if issues:
            violations.append({"url": url, "issues": ",".join(issues)})

    # Directory inventory
    dir_counts: Counter[str] = Counter()
    for p in pages:
        parts = [x for x in urlparse(str(p.get("url") or "")).path.strip("/").split("/") if x]
        if parts:
            dir_counts[parts[0]] += 1
        else:
            dir_counts["(root)"] += 1

    depth_dist = Counter(depths)
    summary = {
        "urls_crawled": len(pages),
        "status_200": status_200,
        "max_click_depth": max_depth,
        "within_3_clicks": within_3,
        "depth_4_plus": len(depth_4_plus),
        "orphans": len(orphans),
        "phantom_dirs": len(phantoms),
        "redirect_chains": 0,
        "canonical_conflicts": sum(
            1
            for p in pages
            if p.get("canonical")
            and _norm_url(str(p.get("canonical"))) != _norm_url(str(p.get("url") or ""))
        ),
        "url_convention_violations": len(violations),
    }

    _write_csv(out_dir / "summary.csv", [summary])
    _write_csv(
        out_dir / "click_depth_distribution.csv",
        [{"depth": k, "pages": v} for k, v in sorted(depth_dist.items())],
    )
    _write_csv(
        out_dir / "pages_depth_4_plus.csv",
        [
            {"url": p.get("url"), "depth": p.get("depth"), "status": p.get("status")}
            for p in depth_4_plus
        ],
    )
    _write_csv(
        out_dir / "directory_inventory.csv",
        [{"directory": k, "urls": v} for k, v in dir_counts.most_common()],
    )
    _write_csv(out_dir / "phantom_directories.csv", phantoms)
    _write_csv(out_dir / "url_convention_violations.csv", violations)
    _write_csv(out_dir / "orphan_pages.csv", orphans)
    _write_csv(out_dir / "dead_end_pages.csv", dead_ends)
    _write_csv(
        out_dir / "page_link_stats.csv",
        [
            {
                "url": p.get("url"),
                "in_links": len(inbound.get(_norm_url(str(p.get("url") or "")), ())),
                "out_links": len(outbound.get(_norm_url(str(p.get("url") or "")), ())),
                "depth": p.get("depth"),
                "status": p.get("status"),
            }
            for p in pages
        ],
    )

    print(json.dumps(summary, indent=2))
    print(f"Artifacts written to {out_dir}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Site architecture audit")
    sub = parser.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl")
    c.add_argument("seed")
    c.add_argument("out_jl")
    c.add_argument("--page-cap", type=int, default=500)
    c.add_argument("--delay", type=float, default=0.25)
    c.add_argument("--engine", choices=["auto", "light", "advertools"], default="auto")

    a = sub.add_parser("audit")
    a.add_argument("jl")
    a.add_argument("--domain", required=True)
    a.add_argument("--out", default="./ia")

    args = parser.parse_args()
    if args.cmd == "crawl":
        out = Path(args.out_jl)
        engine = args.engine
        if engine == "auto":
            try:
                import advertools  # noqa: F401

                engine = "advertools"
            except ImportError:
                engine = "light"
        if engine == "advertools":
            crawl_advertools(args.seed, out, page_cap=args.page_cap, delay=args.delay)
        else:
            crawl_light(args.seed, out, page_cap=args.page_cap, delay=args.delay)
    elif args.cmd == "audit":
        audit(Path(args.jl), domain=args.domain, out_dir=Path(args.out))


if __name__ == "__main__":
    main()
