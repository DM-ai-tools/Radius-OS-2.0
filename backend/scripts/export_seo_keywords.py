"""One-off export: every DataForSEO keyword for the seed "SEO" with volume > 0."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path

from app.config import get_settings
from app.integrations.dataforseo import (
    MAX_LABS_LIMIT,
    _keyword_rows,
    _post,
    coerce_labs_location,
)

SEED = "SEO"
OUT = Path(__file__).resolve().parents[2] / "data" / "seo-keywords.csv"

ENDPOINTS = (
    (
        "keyword_suggestions",
        "/dataforseo_labs/google/keyword_suggestions/live",
        "keyword_info.search_volume",
    ),
    (
        "keyword_ideas",
        "/dataforseo_labs/google/keyword_ideas/live",
        "keyword_info.search_volume",
    ),
    (
        "related_keywords",
        "/dataforseo_labs/google/related_keywords/live",
        "keyword_data.keyword_info.search_volume",
    ),
)


def _total_count(body: dict | None) -> int | None:
    for task in (body or {}).get("tasks") or []:
        if not isinstance(task, dict):
            continue
        for result in task.get("result") or []:
            if isinstance(result, dict) and result.get("total_count") is not None:
                return int(result["total_count"])
    return None


def _payload(endpoint: str, *, location_code: int, language_code: str, offset: int, volume_field: str) -> dict:
    seed = SEED.lower()
    body: dict = {
        "location_code": location_code,
        "language_code": language_code,
        "include_serp_info": False,
        "limit": MAX_LABS_LIMIT,
        "offset": offset,
        "filters": [volume_field, ">", 0],
        "order_by": [f"{volume_field},desc"],
    }
    if endpoint == "keyword_ideas":
        body["keywords"] = [seed]
    else:
        body["keyword"] = seed
        body["include_seed_keyword"] = True
    if endpoint == "related_keywords":
        body["depth"] = 1
    return body


def _merge(bucket: dict[str, dict], row: dict) -> None:
    volume = row.get("volume")
    if not isinstance(volume, int) or volume <= 0:
        return
    key = str(row.get("keyword") or "").strip().lower()
    if not key:
        return
    current = bucket.get(key)
    endpoint = str(row.get("dataforseo_endpoint") or "")
    if current is None:
        bucket[key] = {
            "keyword": str(row["keyword"]).strip(),
            "search_volume": volume,
            "cpc": row.get("cpc"),
            "competition": row.get("competition"),
            "competition_level": row.get("competition_level"),
            "keyword_difficulty": row.get("difficulty"),
            "intent": row.get("intent"),
            "endpoints": {endpoint} if endpoint else set(),
        }
        return
    current["endpoints"].add(endpoint)
    if volume > current["search_volume"]:
        current["keyword"] = str(row["keyword"]).strip()
        current["search_volume"] = volume
    for field, source in (
        ("cpc", "cpc"),
        ("competition", "competition"),
        ("competition_level", "competition_level"),
        ("keyword_difficulty", "difficulty"),
        ("intent", "intent"),
    ):
        if current.get(field) is None and row.get(source) is not None:
            current[field] = row.get(source)


async def _pages(endpoint: str, path: str, volume_field: str, location_code: int, language_code: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    total: int | None = None
    while offset < 100_000:
        body = await _post(
            path,
            [
                _payload(
                    endpoint,
                    location_code=location_code,
                    language_code=language_code,
                    offset=offset,
                    volume_field=volume_field,
                )
            ],
        )
        if body is None:
            print(f"{endpoint} failed at offset {offset}", flush=True)
            break
        if total is None:
            total = _total_count(body)
            print(f"{endpoint} total_count={total}", flush=True)
        page = _keyword_rows(body, endpoint=endpoint)
        print(f"{endpoint} offset={offset} page={len(page)}", flush=True)
        if not page:
            break
        rows.extend(page)
        offset += len(page)
        if len(page) < MAX_LABS_LIMIT:
            break
        if total is not None and offset >= total:
            break
    return rows


async def main() -> None:
    settings = get_settings()
    location_code = coerce_labs_location(settings.dataforseo_location_code)
    language_code = settings.dataforseo_language_code or "en"
    print(f"location={location_code} language={language_code}", flush=True)
    bucket: dict[str, dict] = {}
    for endpoint, path, volume_field in ENDPOINTS:
        for row in await _pages(endpoint, path, volume_field, location_code, language_code):
            _merge(bucket, row)
    ordered = sorted(bucket.values(), key=lambda row: (-row["search_volume"], row["keyword"].lower()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "keyword",
                "search_volume",
                "cpc",
                "competition",
                "competition_level",
                "keyword_difficulty",
                "intent",
                "endpoints",
            ],
        )
        writer.writeheader()
        for row in ordered:
            writer.writerow(
                {
                    **{key: row.get(key) for key in writer.fieldnames if key != "endpoints"},
                    "endpoints": "|".join(sorted(ep for ep in row["endpoints"] if ep)),
                }
            )
    print(f"wrote {len(ordered)} keywords to {OUT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
