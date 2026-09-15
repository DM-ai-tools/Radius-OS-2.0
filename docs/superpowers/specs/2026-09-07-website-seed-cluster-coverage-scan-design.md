# Website ↔ Seed-Cluster Coverage Scan — Design

**Date:** 2026-09-07
**Status:** Approved for planning
**Phase touched:** Phase 5 (Search Demand) primarily; Phase 6 (Site Architecture / URL mapping) consumes output.

## Problem

Today Phase 5 (`run_search_demand`) builds `seed_clusters` from CDD/services/pages,
then immediately runs keyword relevance analysis, clustering, and topic planning.
Phase 6 later maps clusters to existing pages during URL mapping. Nothing scans the
**current live website** and matches it against the seed clusters *before* keyword
analysis and URL mapping run. As a result:

- Keyword analysis can over-prune clusters that the site already ranks/has pages for.
- URL mapping re-discovers page↔cluster relationships from scratch, with no
  pre-established coverage baseline.

We want a new step that scans the current website and, for every seed cluster,
determines whether an existing on-site page already covers it — a **coverage/gap
map** that feeds both keyword analysis and URL mapping.

## Decisions (locked with stakeholder)

1. **Output** — a coverage/gap map: per seed cluster → which existing page(s) cover
   it (`covered` / `partial` / `gap`). Feeds both keyword analysis and URL mapping.
2. **Scan source** — reuse the Phase 3 page inventory (`website_situation_summary`);
   live-enrich only pages missing the fields matching needs (title / H1 / body text).
   No duplicate full crawl.
3. **Match method** — deterministic token/semantic overlap, reusing the scoring
   helpers already in `url_mapping.py`. Free, fast, deterministic.
4. **Renderer** — Playwright (headless chromium) as the **primary** renderer for
   the enrichment fetch, with graceful fallback to the existing stack
   (httpx/Firecrawl) and finally Perplexity-via-OpenRouter research when rendering
   is blocked/unavailable.

## Data flow

```
run_search_demand (Phase 5)
  ...
  seed_clusters built            (search_demand.py:674)
     │
     ▼  NEW STEP
  scan_and_match_clusters(seed_clusters, website, client_domain=...)
     • collect Phase 3 pages (collect_crawl_pages)
     • enrich pages missing title/H1/text via browser_fetch.render_page()
         Playwright  →  httpx/Firecrawl (fetch_page_html)  →  Perplexity research
     • deterministic overlap: each seed cluster → best on-site page(s)
     • band each cluster: covered | partial | gap
     → cluster_coverage report
     │
     ▼
  keyword relevance analysis     (llm_filter_keywords_by_seed, line ~695)
     • receives covered_targets set → don't over-prune covered clusters,
       nudge gap clusters up
     │
     ▼
  clustering / topic plan / ranking
  ...
  profile.search_demand_summary["cluster_coverage"] = report   (persisted)

Phase 6 (site_architecture / url_mapping)
  • map_cluster_to_url() reads coverage_hint → covered clusters seed their
    suggested_url with the matched page for consistency with the pre-scan.
```

The new step runs **after** `seed_clusters` exist and **before** keyword relevance
analysis, satisfying "before keyword analysis and URL mapping."

## New modules

### `backend/app/integrations/browser_fetch.py`

Headless Playwright renderer with fallback chain.

- `async def render_page(url: str, *, timeout: float = 20.0) -> tuple[str, dict]`
  - Returns `(html, meta)` shaped like `web_fetch.fetch_page_html` so callers are
    interchangeable. `meta` includes `source` in
    `{"playwright", "fetch_url", "firecrawl", "perplexity", ...}`, `status_code`,
    `final_url`, `error`.
  - Order:
    1. If `settings.use_playwright` and chromium is available → render with
       async Playwright (chromium, headless), wait for network idle, return DOM
       HTML + text.
    2. On Playwright import/launch failure, timeout, or thin/empty content →
       fall back to `web_fetch.fetch_page_html` (httpx → Firecrawl).
    3. If still blocked/empty → Perplexity single-page research fallback
       (reusing `site_research._perplexity_research` to summarize the page's
       topic/title/H1 when the DOM can't be fetched).
  - Reuses `web_fetch.assert_safe_url` (SSRF guard) before any fetch.
  - Playwright launch is lazy/guarded so import errors never crash the pipeline.

### `backend/app/services/cluster_coverage.py`

Deterministic matcher.

- `async def scan_and_match_clusters(seed_clusters, website, *, client_domain=None,
   enrich=True, max_pages=200) -> dict`
  - `collect_crawl_pages(website=website)` → candidate on-site pages.
  - Enrich: for pages missing `title`/`h1`/`text`, call
    `browser_fetch.render_page` (bounded concurrency, e.g. `asyncio.Semaphore(4)`),
    parse with `web_fetch.parse_html` + `page_text_excerpt`.
  - For each seed cluster, build a **synthetic cluster** compatible with the
    `url_mapping` helpers: primary keyword = `target`/`seed`; `keywords` =
    flattened `exact` + `phrase` + `related` + `broad` buckets. Score the best
    page via `build_url_candidates` + `score_semantic_match` +
    `score_keyword_topic_match` + `_overlap_ratio`.
  - Band: `covered` (score ≥ `_HIGH_THRESHOLD`), `partial`
    (≥ `_MEDIUM_THRESHOLD`), else `gap`.

  **Shared helpers:** the token/overlap helpers currently private to
  `url_mapping.py` (`_norm_kw`, `_tokens`, `_overlap_ratio`, `url_n`,
  `build_url_candidates`, `score_semantic_match`, `score_keyword_topic_match`,
  thresholds) will be **imported** by `cluster_coverage.py`, not duplicated. If any
  are too entangled to import cleanly, lift them into a small shared module
  (e.g. `app/services/url_match_common.py`) and have both files import from there.
  Do not copy-paste.

### Output shape

```json
{
  "generated_at": "2026-09-07T...Z",
  "pages_scanned": 42,
  "pages_enriched": 12,
  "renderer_used": {"playwright": 10, "firecrawl": 2, "fetch_url": 30},
  "clusters": [
    {
      "seed": "seo services",
      "target": "seo services",
      "target_type": "service",
      "coverage": "covered",
      "matched_url": "/services/seo",
      "match_score": 71.5,
      "competing_urls": ["/seo", "/local-seo"]
    }
  ],
  "summary": {"covered": 8, "partial": 5, "gap": 11, "clusters": 24}
}
```

Persisted at `profile.search_demand_summary["cluster_coverage"]` and included in the
`search_demand_report` card payload for operator visibility.

## Downstream consumption (minimal — YAGNI)

- **Keyword analysis:** compute a `covered_targets: set[str]` (normalized
  seed/target of `covered`/`partial` clusters) and pass it to the relevance step so
  covered clusters aren't over-pruned; gap clusters get a small priority nudge.
  Additive; no change when the set is empty.
- **URL mapping (Phase 6):** `map_cluster_to_url` gains an optional
  `coverage_hint: dict | None`. When a cluster is `covered`, its `matched_url`
  seeds `suggested_url` so mapping stays consistent with the pre-scan. Absent hint
  → identical to current behavior.

## Config & deployment

- `app/config.py`: add `use_playwright: bool = True` and (optional)
  `coverage_scan_max_pages: int = 200`.
- `backend/requirements.txt`: add `playwright`.
- **Docker/Railway** (`Dockerfile`, `backend/Dockerfile`): after `pip install`,
  run `python -m playwright install --with-deps chromium`. Note the image size
  increase; `--with-deps` installs the OS libs chromium needs on `python:3.12-slim`.
  If image size is unacceptable, `use_playwright=false` disables the browser and the
  scan degrades to httpx/Firecrawl/Perplexity with no code change.
- Behavior when Playwright is absent at runtime: `render_page` catches the import/
  launch error and falls through the chain — the pipeline never fails on a missing
  browser.

## Error handling

- Every per-page render is wrapped; a failed page contributes whatever Phase 3
  already had (title/meta) and is marked not-enriched. One bad page never aborts the
  scan.
- `scan_and_match_clusters` is defensive: empty/malformed `website` → returns an
  empty coverage report with `summary.clusters = len(seed_clusters)` and all `gap`,
  so Phase 5 continues.
- The new step is wrapped in Phase 5 so a total scan failure logs a `system_notice`
  and continues without `cluster_coverage` (non-blocking).

## Testing

Mirror `backend/tests/test_url_mapping.py` conventions.

- `test_cluster_coverage.py` — covered/partial/gap banding with in-memory page +
  seed-cluster fixtures; no network. Verify synthetic-cluster construction from
  the `exact/phrase/related/broad` buckets, and the `covered_targets` derivation.
- `test_browser_fetch.py` — Playwright mocked: (1) success path returns rendered
  HTML with `source="playwright"`; (2) Playwright launch failure falls back to
  `fetch_page_html`; (3) SSRF guard rejects unsafe hosts.
- Phase 5 integration test — `run_search_demand` attaches `cluster_coverage` to the
  summary and computes it before relevance analysis (assert ordering via a spy /
  recorded call sequence), with providers mocked.

## Out of scope

- No fresh full re-crawl (Phase 3 inventory is reused).
- No LLM-based coverage judgement (deterministic only; LLM tiebreak was declined).
- No change to how Phase 3 discovers pages.
- No auto-merge/redirect decisions — coverage only informs; humans still approve
  URL mapping.
```
