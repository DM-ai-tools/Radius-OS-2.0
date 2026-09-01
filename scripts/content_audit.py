#!/usr/bin/env python3
"""Content audit — disposition existing URLs from GSC + crawl.

Examples:
  python scripts/content_audit.py fetch sc-domain:example.com --days 90 --out ./gsc
  python scripts/content_audit.py audit \\
      --current ./gsc/pages_current.csv \\
      --prior   ./gsc/pages_prior.csv \\
      --queries ./gsc/page_query_current.csv \\
      --crawl   crawl.jl --out ./audit
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


# Defaults from existing-content-audit skill
DEFAULTS = {
    "decay_threshold": -0.30,
    "min_prior_clicks": 10,
    "thin_words": 300,
    "striking_lo": 8.0,
    "striking_hi": 20.0,
    "cannibal_impressions": 50,
    "keep_clicks": 50,
    "retitle_pos": 5.0,
    "retitle_impr": 100,
    "retitle_ctr": 0.02,
    "optimise_zero_click_impr": 100,
    "weak_clicks": 5,
}

GSC_ROW_CAP = 25_000
GSC_DAY_CAP = 50_000


def _norm_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    if "://" not in u:
        return u if u.startswith("/") else f"/{u}"
    p = urlparse(u)
    path = p.path or "/"
    return f"{p.scheme}://{p.netloc}{path}".rstrip("/") or u


def _path_only(u: str) -> str:
    if "://" in u:
        return urlparse(u).path or "/"
    return u if u.startswith("/") else f"/{u}"


def _f(row: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except (TypeError, ValueError):
                continue
    return default


def _i(row: dict[str, Any], *keys: str, default: int = 0) -> int:
    return int(_f(row, *keys, default=float(default)))


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"Missing file: {path}")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _read_crawl(path: Path | None) -> dict[str, dict[str, Any]]:
    """Map URL/path → {words, title, status, depth} from .jl/.jsonl or CSV."""
    out: dict[str, dict[str, Any]] = {}
    if not path or not path.is_file():
        return out
    if path.suffix.lower() in {".jl", ".jsonl"}:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                url = str(row.get("url") or row.get("address") or "")
                if not url:
                    continue
                meta = {
                    "words": _i(row, "words", "word_count", "wordcount"),
                    "title": row.get("title") or "",
                    "status": row.get("status") or row.get("status_code"),
                    "depth": row.get("crawl_depth") or row.get("depth"),
                }
                out[_norm_url(url)] = meta
                out[_path_only(url)] = meta
        return out
    for row in _read_csv(path):
        url = str(row.get("url") or row.get("Address") or row.get("address") or "")
        if not url:
            continue
        meta = {
            "words": _i(row, "words", "word_count", "Word Count", "wordcount"),
            "title": row.get("title") or row.get("Title") or "",
            "status": row.get("status") or row.get("Status Code"),
            "depth": row.get("crawl_depth") or row.get("Crawl Depth"),
        }
        out[_norm_url(url)] = meta
        out[_path_only(url)] = meta
    return out


def _index_pages(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = str(row.get("page") or row.get("url") or row.get("Page") or "")
        if not url:
            continue
        key = _norm_url(url)
        indexed[key] = {
            "url": key,
            "clicks": _f(row, "clicks", "Clicks"),
            "impressions": _f(row, "impressions", "Impressions"),
            "ctr": _f(row, "ctr", "CTR"),
            "position": _f(row, "position", "Position"),
        }
        # If CTR given as percent string
        ctr_raw = row.get("ctr") or row.get("CTR")
        if isinstance(ctr_raw, str) and "%" in ctr_raw:
            try:
                indexed[key]["ctr"] = float(ctr_raw.replace("%", "").strip()) / 100.0
            except ValueError:
                pass
        if indexed[key]["ctr"] > 1.0:
            indexed[key]["ctr"] = indexed[key]["ctr"] / 100.0
    return indexed


def detect_cannibalisation(
    query_rows: list[dict[str, Any]],
    *,
    floor: int,
) -> list[dict[str, Any]]:
    by_query: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in query_rows:
        q = str(row.get("query") or row.get("Query") or "").strip().lower()
        page = _norm_url(str(row.get("page") or row.get("url") or row.get("Page") or ""))
        if not q or not page:
            continue
        impr = _f(row, "impressions", "Impressions")
        clicks = _f(row, "clicks", "Clicks")
        by_query[q].append({"page": page, "impressions": impr, "clicks": clicks})

    out: list[dict[str, Any]] = []
    for query, pages in by_query.items():
        # Aggregate same page
        agg: dict[str, dict[str, float]] = {}
        for p in pages:
            a = agg.setdefault(p["page"], {"impressions": 0.0, "clicks": 0.0})
            a["impressions"] += p["impressions"]
            a["clicks"] += p["clicks"]
        if len(agg) < 2:
            continue
        ranked = sorted(agg.items(), key=lambda x: (-x[1]["clicks"], -x[1]["impressions"]))
        total_impr = sum(v["impressions"] for _, v in ranked)
        if total_impr < floor:
            continue
        keep, merge = ranked[0][0], ranked[1][0]
        out.append(
            {
                "query": query,
                "keep": keep,
                "merge_in": merge,
                "impressions_at_risk": int(total_impr - ranked[0][1]["impressions"]),
                "urls": [u for u, _ in ranked[:6]],
            }
        )
    out.sort(key=lambda r: -int(r["impressions_at_risk"]))
    return out


def assign_disposition(
    url: str,
    current: dict[str, Any] | None,
    prior: dict[str, Any] | None,
    crawl: dict[str, Any] | None,
    *,
    cannibal_urls: set[str],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    cur = current or {}
    pri = prior or {}
    cr = crawl or {}
    clicks = float(cur.get("clicks") or 0)
    impr = float(cur.get("impressions") or 0)
    pos = float(cur.get("position") or 0)
    ctr = float(cur.get("ctr") or 0)
    if ctr == 0 and impr > 0:
        ctr = clicks / impr
    prior_clicks = float(pri.get("clicks") or 0)
    words = int(cr.get("words") or 0)
    change = None
    if prior_clicks > 0:
        change = (clicks - prior_clicks) / prior_clicks

    effort = "Medium"
    reason = ""
    disposition = "KEEP"

    # Ordered rules — first match wins
    if url in cannibal_urls or _path_only(url) in cannibal_urls:
        disposition, effort, reason = (
            "CONSOLIDATE",
            "High",
            "Competing with another URL on shared queries",
        )
    elif clicks >= cfg["keep_clicks"] and (change is None or change > cfg["decay_threshold"]):
        disposition, effort, reason = "KEEP", "None", "≥50 clicks, no material decline"
    elif (
        prior_clicks >= cfg["min_prior_clicks"]
        and change is not None
        and change <= cfg["decay_threshold"]
    ):
        disposition, effort, reason = (
            "REFRESH",
            "Medium",
            f"Decayed {change:.0%} from {prior_clicks:.0f} → {clicks:.0f} clicks",
        )
    elif (
        pos
        and pos <= cfg["retitle_pos"]
        and impr >= cfg["retitle_impr"]
        and ctr < cfg["retitle_ctr"]
    ):
        disposition, effort, reason = (
            "RETITLE",
            "Low",
            f"Position {pos:.1f}, CTR {ctr:.1%} — snippet problem",
        )
    elif pos and cfg["striking_lo"] <= pos <= cfg["striking_hi"] and impr >= 50:
        disposition, effort, reason = (
            "OPTIMISE",
            "Medium",
            f"Striking distance position {pos:.1f}",
        )
    elif impr >= cfg["optimise_zero_click_impr"] and clicks == 0:
        disposition, effort, reason = (
            "OPTIMISE",
            "Medium",
            "≥100 impressions, zero clicks — intent mismatch?",
        )
    elif clicks == 0 and impr == 0 and words and words < cfg["thin_words"]:
        disposition, effort, reason = (
            "DELETE_CANDIDATE",
            "Low",
            f"Zero search demand, thin ({words} words) — review only",
        )
    elif clicks == 0 and impr == 0 and (not words or words >= cfg["thin_words"]):
        disposition, effort, reason = (
            "NOINDEX",
            "Low",
            "Zero search demand but substantial — reversible middle ground",
        )
    elif impr > 0 and clicks < cfg["weak_clicks"]:
        disposition, effort, reason = (
            "CONSOLIDATE",
            "High",
            f"Some impressions, <{cfg['weak_clicks']} clicks — too weak alone",
        )
    else:
        disposition, effort, reason = "KEEP", "None", "No stronger rule matched"

    return {
        "url": url,
        "disposition": disposition,
        "reason": reason,
        "effort": effort,
        "clicks": clicks,
        "impressions": impr,
        "position": pos or "",
        "ctr": round(ctr, 4) if ctr else "",
        "prior_clicks": prior_clicks,
        "change": round(change, 4) if change is not None else "",
        "words": words or "",
        "title": cr.get("title") or "",
        "status": cr.get("status") or "",
    }


def run_audit(
    *,
    current_path: Path,
    prior_path: Path | None,
    queries_path: Path | None,
    crawl_path: Path | None,
    out_dir: Path,
    cfg: dict[str, Any],
) -> None:
    current = _index_pages(_read_csv(current_path))
    prior = _index_pages(_read_csv(prior_path)) if prior_path and prior_path.is_file() else {}
    queries = _read_csv(queries_path) if queries_path and queries_path.is_file() else []
    crawl = _read_crawl(crawl_path)

    if not prior:
        print(
            "WARNING: No prior-period pages CSV — decay cannot be assessed; "
            "REFRESH rules will not fire. This is a qualitative / current-only pass.",
            file=sys.stderr,
        )

    cannibal = detect_cannibalisation(queries, floor=int(cfg["cannibal_impressions"]))
    cannibal_urls: set[str] = set()
    for row in cannibal:
        for u in row.get("urls") or []:
            cannibal_urls.add(u)
            cannibal_urls.add(_path_only(u))

    # Union of URLs from current, prior, crawl
    all_urls = set(current) | set(prior)
    for k in list(crawl):
        if "://" in k:
            all_urls.add(k)

    dispositions: list[dict[str, Any]] = []
    for url in sorted(all_urls):
        if not url or url.startswith("/") and url not in current and url not in prior:
            # path-only crawl keys without metrics — skip duplicates of full URLs
            if any(_path_only(u) == url for u in all_urls if "://" in u):
                continue
        dispositions.append(
            assign_disposition(
                url,
                current.get(url),
                prior.get(url),
                crawl.get(url) or crawl.get(_path_only(url)),
                cannibal_urls=cannibal_urls,
                cfg=cfg,
            )
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "dispositions.csv", dispositions)
    _write_csv(out_dir / "cannibalisation.csv", cannibal)

    by_disp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in dispositions:
        by_disp[str(row["disposition"]).lower()].append(row)
    for name, rows in by_disp.items():
        _write_csv(out_dir / f"action_{name}.csv", rows)

    summary = {
        "urls": len(dispositions),
        "counts": {k: len(v) for k, v in sorted(by_disp.items())},
        "cannibalisation_clusters": len(cannibal),
        "thresholds": cfg,
        "could_not_assess": [
            x
            for x in [
                "decay (no prior period)" if not prior else None,
                "cannibalisation (no page+query CSV)" if not queries else None,
                "word counts / thin (no crawl)" if not crawl else None,
                "backlinks (not provided)",
                "seasonality (confirm YoY if niche is seasonal)",
            ]
            if x
        ],
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {out_dir}/dispositions.csv and action_*.csv")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def cmd_fetch(args: argparse.Namespace) -> None:
    """Pull two comparable GSC periods. Requires google-api-python-client + credentials."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        print(
            "Install: pip install google-api-python-client google-auth\n"
            "Or export CSVs from GSC UI and run `audit` offline.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    creds_path = args.credentials or Path("gsc_credentials.json")
    if not Path(creds_path).is_file():
        print(
            f"Missing credentials at {creds_path}. "
            "Pass --credentials or place service-account JSON there.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    scopes = ["https://www.googleapis.com/auth/webmasters.readonly"]
    creds = service_account.Credentials.from_service_account_file(str(creds_path), scopes=scopes)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)

    days = int(args.days)
    end = date.today() - timedelta(days=3)  # GSC lag
    cur_start = end - timedelta(days=days - 1)
    prior_end = cur_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=days - 1)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    def pull(start: date, end_d: date, dimensions: list[str], label: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        start_row = 0
        while True:
            body = {
                "startDate": start.isoformat(),
                "endDate": end_d.isoformat(),
                "dimensions": dimensions,
                "rowLimit": GSC_ROW_CAP,
                "startRow": start_row,
            }
            resp = (
                service.searchanalytics()
                .query(siteUrl=args.site, body=body)
                .execute()
            )
            batch = resp.get("rows") or []
            if not batch:
                break
            for r in batch:
                keys = r.get("keys") or []
                entry: dict[str, Any] = {
                    "clicks": r.get("clicks"),
                    "impressions": r.get("impressions"),
                    "ctr": r.get("ctr"),
                    "position": r.get("position"),
                }
                if len(dimensions) == 1:
                    entry["page" if dimensions[0] == "page" else "query"] = keys[0] if keys else ""
                else:
                    entry["query"] = keys[0] if len(keys) > 0 else ""
                    entry["page"] = keys[1] if len(keys) > 1 else ""
                rows.append(entry)
            start_row += len(batch)
            if len(batch) < GSC_ROW_CAP:
                break
            if start_row >= GSC_DAY_CAP:
                print(
                    f"WARNING: Hit ~{GSC_DAY_CAP} row ceiling for {label}; truncated.",
                    file=sys.stderr,
                )
                break
        return rows

    pages_cur = pull(cur_start, end, ["page"], "pages_current")
    pages_pri = pull(prior_start, prior_end, ["page"], "pages_prior")
    page_query = pull(cur_start, end, ["query", "page"], "page_query_current")

    _write_csv(out / "pages_current.csv", pages_cur)
    _write_csv(out / "pages_prior.csv", pages_pri)
    _write_csv(out / "page_query_current.csv", page_query)
    meta = {
        "site": args.site,
        "current": {"start": cur_start.isoformat(), "end": end.isoformat()},
        "prior": {"start": prior_start.isoformat(), "end": prior_end.isoformat()},
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "rows": {
            "pages_current": len(pages_cur),
            "pages_prior": len(pages_pri),
            "page_query_current": len(page_query),
        },
    }
    (out / "fetch_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SearchFit content audit (GSC + crawl)")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="Pull GSC page + query CSVs for two periods")
    f.add_argument("site", help="GSC property, e.g. sc-domain:example.com or https://example.com/")
    f.add_argument("--days", type=int, default=90)
    f.add_argument("--out", default="./gsc")
    f.add_argument("--credentials", default=None, help="Service account JSON path")
    f.set_defaults(func=cmd_fetch)

    a = sub.add_parser("audit", help="Assign dispositions from CSV exports")
    a.add_argument("--current", required=True, help="pages_current.csv")
    a.add_argument("--prior", default=None, help="pages_prior.csv")
    a.add_argument("--queries", default=None, help="page_query_current.csv")
    a.add_argument("--crawl", default=None, help="crawl.jl / crawl.csv")
    a.add_argument("--out", default="./audit")
    a.add_argument("--decay", type=float, default=DEFAULTS["decay_threshold"])
    a.add_argument("--min-prior-clicks", type=int, default=DEFAULTS["min_prior_clicks"])
    a.add_argument("--thin-words", type=int, default=DEFAULTS["thin_words"])
    a.add_argument("--cannibal-impressions", type=int, default=DEFAULTS["cannibal_impressions"])

    def _audit(args: argparse.Namespace) -> None:
        cfg = dict(DEFAULTS)
        cfg["decay_threshold"] = args.decay
        cfg["min_prior_clicks"] = args.min_prior_clicks
        cfg["thin_words"] = args.thin_words
        cfg["cannibal_impressions"] = args.cannibal_impressions
        run_audit(
            current_path=Path(args.current),
            prior_path=Path(args.prior) if args.prior else None,
            queries_path=Path(args.queries) if args.queries else None,
            crawl_path=Path(args.crawl) if args.crawl else None,
            out_dir=Path(args.out),
            cfg=cfg,
        )

    a.set_defaults(func=_audit)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
