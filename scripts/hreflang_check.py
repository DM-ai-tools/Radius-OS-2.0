#!/usr/bin/env python3
"""Validate hreflang reciprocity and cluster integrity.

Examples:
  python scripts/hreflang_check.py crawl.jl --out ./hreflang
  python scripts/hreflang_check.py annotations.csv --format csv --out ./hreflang

CSV format: url,hreflang,href  (one row per annotation)
advertools crawl.jl: uses columns url, alt_href, alt_hreflang (or similar)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

# ISO 639-1 languages (common set; unknown codes still flagged)
ISO_639_1 = {
    "aa","ab","ae","af","ak","am","an","ar","as","av","ay","az","ba","be","bg","bh","bi","bm","bn",
    "bo","br","bs","ca","ce","ch","co","cr","cs","cu","cv","cy","da","de","dv","dz","ee","el","en",
    "eo","es","et","eu","fa","ff","fi","fj","fo","fr","fy","ga","gd","gl","gn","gu","gv","ha","he",
    "hi","ho","hr","ht","hu","hy","hz","ia","id","ie","ig","ii","ik","io","is","it","iu","ja","jv",
    "ka","kg","ki","kj","kk","kl","km","kn","ko","kr","ks","ku","kv","kw","ky","la","lb","lg","li",
    "ln","lo","lt","lu","lv","mg","mh","mi","mk","ml","mn","mr","ms","mt","my","na","nb","nd","ne",
    "ng","nl","nn","no","nr","nv","ny","oc","oj","om","or","os","pa","pi","pl","ps","pt","qu","rm",
    "rn","ro","ru","rw","sa","sc","sd","se","sg","si","sk","sl","sm","sn","so","sq","sr","ss","st",
    "su","sv","sw","ta","te","tg","th","ti","tk","tl","tn","to","tr","ts","tt","tw","ty","ug","uk",
    "ur","uz","ve","vi","vo","wa","wo","xh","yi","yo","za","zh","zu",
}

# Common ISO 3166-1 alpha-2 regions (not exhaustive; unknown regions flagged)
ISO_3166 = {
    "ad","ae","af","ag","ai","al","am","ao","aq","ar","as","at","au","aw","ax","az","ba","bb","bd",
    "be","bf","bg","bh","bi","bj","bl","bm","bn","bo","bq","br","bs","bt","bv","bw","by","bz","ca",
    "cc","cd","cf","cg","ch","ci","ck","cl","cm","cn","co","cr","cu","cv","cw","cx","cy","cz","de",
    "dj","dk","dm","do","dz","ec","ee","eg","eh","er","es","et","fi","fj","fk","fm","fo","fr","ga",
    "gb","gd","ge","gf","gg","gh","gi","gl","gm","gn","gp","gq","gr","gs","gt","gu","gw","gy","hk",
    "hm","hn","hr","ht","hu","id","ie","il","im","in","io","iq","ir","is","it","je","jm","jo","jp",
    "ke","kg","kh","ki","km","kn","kp","kr","kw","ky","kz","la","lb","lc","li","lk","lr","ls","lt",
    "lu","lv","ly","ma","mc","md","me","mf","mg","mh","mk","ml","mm","mn","mo","mp","mq","mr","ms",
    "mt","mu","mv","mw","mx","my","mz","na","nc","ne","nf","ng","ni","nl","no","np","nr","nu","nz",
    "om","pa","pe","pf","pg","ph","pk","pl","pm","pn","pr","ps","pt","pw","py","qa","re","ro","rs",
    "ru","rw","sa","sb","sc","sd","se","sg","sh","si","sj","sk","sl","sm","sn","so","sr","ss","st",
    "sv","sx","sy","sz","tc","td","tf","tg","th","tj","tk","tl","tm","tn","to","tr","tt","tv","tw",
    "tz","ua","ug","um","us","uy","uz","va","vc","ve","vg","vi","vn","vu","wf","ws","ye","yt","za",
    "zm","zw",
}

# Classic mistakes
CODE_HINTS = {
    "uk": "Use 'gb' for United Kingdom (ISO 3166-1), not 'uk'",
    "eu": "There is no ISO region code 'eu'",
    "en-uk": "Use 'en-gb', not 'en-uk'",
    "en-UK": "Use 'en-gb' (region must be uppercase in BCP47 but we normalize)",
}


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        if x not in self.parent:
            self.parent[x] = x

    def find(self, x: str) -> str:
        self.add(x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra

    def clusters(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            groups[self.find(x)].append(x)
        return dict(groups)


def normalize_url(url: str, base: str | None = None) -> str:
    if not url:
        return ""
    if base:
        url = urljoin(base, url)
    p = urlparse(url.strip())
    # drop fragment; keep scheme/netloc/path/query
    path = p.path or "/"
    if path != "/" and path.endswith("/"):
        # keep trailing slash consistency as-is for identity matching
        pass
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", p.query, ""))


def validate_hreflang_code(code: str) -> str | None:
    """Return problem description or None if OK."""
    raw = (code or "").strip()
    if not raw:
        return "empty hreflang code"
    low = raw.lower()
    if low in CODE_HINTS:
        return CODE_HINTS[low]
    if low == "x-default":
        return None
    # language or language-region
    if "-" in low:
        lang, region = low.split("-", 1)
        if lang not in ISO_639_1:
            return f"invalid language code '{lang}'"
        if len(region) != 2 or region not in ISO_3166:
            return f"invalid region code '{region}' (UK → gb)"
        return None
    if low in ISO_639_1:
        return None
    if low in ISO_3166 and low not in ISO_639_1:
        return f"'{low}' looks like a region code used as a language"
    return f"unrecognized hreflang code '{raw}'"


def load_from_csv(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("url") or "").strip()
            hreflang = (row.get("hreflang") or row.get("alt_hreflang") or "").strip()
            href = (row.get("href") or row.get("alt_href") or "").strip()
            if url and hreflang and href:
                rows.append({"url": url, "hreflang": hreflang, "href": href})
    return rows


def load_from_jl(path: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, Any]]]:
    """Load advertools-style JSON Lines crawl."""
    annotations: list[dict[str, str]] = []
    page_meta: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = normalize_url(str(obj.get("url") or ""))
            if not url:
                continue
            status = obj.get("status") or obj.get("status_code")
            canonical = obj.get("canonical") or obj.get("canonical_url") or ""
            if isinstance(canonical, list):
                canonical = canonical[0] if canonical else ""
            page_meta[url] = {
                "status": status,
                "canonical": normalize_url(str(canonical), url) if canonical else "",
            }
            # advertools often stores @@ separated lists
            alts_h = obj.get("alt_href") or obj.get("hreflang_href") or obj.get("alternate_href")
            alts_l = obj.get("alt_hreflang") or obj.get("hreflang") or obj.get("alternate_hreflang")
            if alts_h is None and isinstance(obj.get("links_alt_href"), list):
                alts_h = obj.get("links_alt_href")
                alts_l = obj.get("links_alt_hreflang")
            hrefs = _split_field(alts_h)
            langs = _split_field(alts_l)
            if hrefs and langs and len(hrefs) == len(langs):
                for lang, href in zip(langs, hrefs):
                    annotations.append(
                        {
                            "url": url,
                            "hreflang": lang.strip(),
                            "href": normalize_url(href.strip(), url),
                        }
                    )
            elif isinstance(obj.get("hreflang"), list):
                for item in obj["hreflang"]:
                    if isinstance(item, dict):
                        annotations.append(
                            {
                                "url": url,
                                "hreflang": str(item.get("lang") or item.get("hreflang") or ""),
                                "href": normalize_url(str(item.get("href") or ""), url),
                            }
                        )
    return annotations, page_meta


def _split_field(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x]
    s = str(val)
    if "@@" in s:
        return [p for p in s.split("@@") if p]
    if "|" in s:
        return [p for p in s.split("|") if p]
    return [s] if s else []


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def run_checks(
    annotations: list[dict[str, str]],
    page_meta: dict[str, dict[str, Any]] | None = None,
    *,
    check_http: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    page_meta = page_meta or {}
    # Map: source_url -> {hreflang: href}
    by_url: dict[str, dict[str, str]] = defaultdict(dict)
    # Map: target_href -> set of source urls that claim it
    claimed_by: dict[str, set[str]] = defaultdict(set)
    code_problems: Counter[str] = Counter()
    code_examples: dict[str, str] = {}

    for row in annotations:
        src = normalize_url(row["url"])
        lang = row["hreflang"].strip()
        href = normalize_url(row["href"], src)
        if not src or not href or not lang:
            continue
        problem = validate_hreflang_code(lang)
        if problem:
            key = lang.lower()
            code_problems[key] += 1
            code_examples[key] = problem
        by_url[src][lang.lower()] = href
        claimed_by[href].add(src)

    missing_returns: list[dict[str, Any]] = []
    missing_self: list[dict[str, Any]] = []
    canonical_conflicts: list[dict[str, Any]] = []
    invalid_codes = [
        {"code": code, "problem": code_examples[code], "occurrences": n}
        for code, n in code_problems.most_common()
    ]

    # Return tags + self-reference
    for src, mapping in by_url.items():
        # self-reference: any annotation where href == src
        if not any(normalize_url(h) == src for h in mapping.values()):
            missing_self.append({"url": src, "issue": "page does not list itself in hreflang"})

        for lang, target in mapping.items():
            if lang == "x-default":
                # still needs reciprocity if target has annotations
                pass
            target_map = by_url.get(target)
            if target_map is None:
                missing_returns.append(
                    {
                        "source_url": src,
                        "target_url": target,
                        "hreflang": lang,
                        "cause": "target has no hreflang at all",
                    }
                )
                continue
            # Does target point back to src with any code?
            if src not in {normalize_url(h) for h in target_map.values()}:
                missing_returns.append(
                    {
                        "source_url": src,
                        "target_url": target,
                        "hreflang": lang,
                        "cause": "target does not point back",
                    }
                )

        meta = page_meta.get(src) or {}
        canon = meta.get("canonical") or ""
        if canon and normalize_url(canon) != src and mapping:
            canonical_conflicts.append(
                {
                    "url": src,
                    "canonical": canon,
                    "issue": "page emits hreflang but canonicalises elsewhere",
                }
            )

    # Clusters via union-find
    uf = UnionFind()
    for src, mapping in by_url.items():
        uf.add(src)
        for href in mapping.values():
            uf.union(src, href)

    clusters_raw = uf.clusters()
    cluster_rows: list[dict[str, Any]] = []
    missing_x_default: list[dict[str, Any]] = []
    for root, members in clusters_raw.items():
        members = sorted(set(normalize_url(m) for m in members))
        # only keep clusters that include at least one annotated page
        annotated_members = [m for m in members if m in by_url]
        if not annotated_members:
            continue
        has_xdefault = False
        locales: set[str] = set()
        for m in annotated_members:
            for lang in by_url[m]:
                if lang == "x-default":
                    has_xdefault = True
                else:
                    locales.add(lang)
        if not has_xdefault and len(annotated_members) >= 2:
            missing_x_default.append(
                {
                    "cluster_id": root,
                    "members": " ".join(annotated_members),
                    "issue": "cluster has no x-default",
                }
            )
        cluster_rows.append(
            {
                "cluster_id": root,
                "member_count": len(annotated_members),
                "locales": " ".join(sorted(locales)),
                "members": " ".join(annotated_members),
            }
        )

    non_200: list[dict[str, Any]] = []
    if check_http:
        try:
            import requests
        except ImportError:
            check_http = False
    if check_http:
        import requests

        checked: set[str] = set()
        for mapping in by_url.values():
            for href in mapping.values():
                if href in checked:
                    continue
                checked.add(href)
                try:
                    r = requests.head(href, allow_redirects=False, timeout=15)
                    status = r.status_code
                    if status != 200:
                        non_200.append({"url": href, "status": status})
                except Exception as e:  # noqa: BLE001
                    non_200.append({"url": href, "status": f"error:{e}"})
    else:
        # use crawl status when available
        for mapping in by_url.values():
            for href in mapping.values():
                meta = page_meta.get(href)
                if not meta:
                    continue
                status = meta.get("status")
                try:
                    code = int(status)
                except (TypeError, ValueError):
                    continue
                if code != 200:
                    non_200.append({"url": href, "status": code})

    results = {
        "missing_return_tags": missing_returns,
        "invalid_codes": invalid_codes,
        "missing_self_reference": missing_self,
        "canonical_conflicts": canonical_conflicts,
        "non_200_targets": non_200,
        "missing_x_default": missing_x_default,
        "clusters": cluster_rows,
    }
    critical = bool(missing_returns or invalid_codes)
    return results, critical


def main() -> None:
    parser = argparse.ArgumentParser(description="hreflang reciprocity validator")
    parser.add_argument("input", help="crawl.jl or annotations.csv")
    parser.add_argument("--format", choices=["auto", "jl", "csv"], default="auto")
    parser.add_argument("--out", default="./hreflang")
    parser.add_argument(
        "--check-http",
        action="store_true",
        help="HEAD-request annotation targets for non-200 (slow)",
    )
    args = parser.parse_args()
    path = Path(args.input)
    fmt = args.format
    if fmt == "auto":
        fmt = "csv" if path.suffix.lower() == ".csv" else "jl"

    page_meta: dict[str, dict[str, Any]] = {}
    if fmt == "csv":
        annotations = load_from_csv(path)
    else:
        annotations, page_meta = load_from_jl(path)

    if not annotations:
        print("No hreflang annotations found in input", file=sys.stderr)
        raise SystemExit(2)

    results, critical = run_checks(annotations, page_meta, check_http=args.check_http)
    out = Path(args.out)
    write_csv(
        out / "missing_return_tags.csv",
        results["missing_return_tags"],
        ["source_url", "target_url", "hreflang", "cause"],
    )
    write_csv(
        out / "invalid_codes.csv",
        results["invalid_codes"],
        ["code", "problem", "occurrences"],
    )
    write_csv(
        out / "missing_self_reference.csv",
        results["missing_self_reference"],
        ["url", "issue"],
    )
    write_csv(
        out / "canonical_conflicts.csv",
        results["canonical_conflicts"],
        ["url", "canonical", "issue"],
    )
    write_csv(
        out / "non_200_targets.csv",
        results["non_200_targets"],
        ["url", "status"],
    )
    write_csv(
        out / "missing_x_default.csv",
        results["missing_x_default"],
        ["cluster_id", "members", "issue"],
    )
    write_csv(
        out / "clusters.csv",
        results["clusters"],
        ["cluster_id", "member_count", "locales", "members"],
    )

    summary = {
        "annotations": len(annotations),
        "pages_with_annotations": len({normalize_url(a["url"]) for a in annotations}),
        "clusters": len(results["clusters"]),
        "critical_missing_returns": len(results["missing_return_tags"]),
        "critical_invalid_codes": len(results["invalid_codes"]),
        "high_missing_self": len(results["missing_self_reference"]),
        "high_canonical_conflicts": len(results["canonical_conflicts"]),
        "out": str(out),
    }
    print(json.dumps(summary, indent=2))
    raise SystemExit(1 if critical else 0)


if __name__ == "__main__":
    main()
