# `app/integrations/providers.py` — 2,549 LOC, churn 5, score 12,745 (queue #1)

**Coverage:** lines 1–530 read line-by-line. Lines 531–2549 covered by symbol-level caller
analysis, death-proofing, and targeted pattern verification (AST re-import check, ruff
selectors, duplicate-block counts) — **not** a full line read. Anything in that range is
reported only where I have direct evidence; the rest is listed as NOT YET AUDITED.

Module docstring claim ("GA4 / GSC / GTM / Ahrefs / Moz adapters with mock mode + fallback")
was checked and is **accurate** — GA4 ×15, GSC ×7, GTM ×9, Moz ×14 references in-file. Not stale.

---

## Q1 — caller map for all 17 top-level symbols

| symbol | lines | external callers | verdict |
|---|---:|---|---|
| `pull_backlinks` | 17–139 | `agents/website.py`, `tests/test_fallback.py` | KEEP |
| `_mock_backlinks` | 142–158 | in-file ×4 | KEEP |
| **`pull_rankings`** | **161–220** | **none, anywhere** | **DEAD** |
| `validate_tracking` | 223–425 | `agents/tracking.py` | KEEP |
| `_crawl_from_research_pages` | 428–466 | in-file ×2 | KEEP |
| `crawl_site` | 469–733 | `agents/website.py` | KEEP |
| `check_broken_links` | 736–911 | `agents/website.py`, `services/technical_seo.py`, 2 tests | KEEP |
| `_extract_keyword` | 914–924 | in-file ×2 | KEEP |
| `optimize_on_page` | 927–1098 | `agents/website.py`, `services/on_page_seo.py`, test | KEEP |
| `run_technical_seo_audit` | 1101–1448 | 2 modules, 1 script, 1 test | KEEP |
| `find_duplicate_field` | 1451–1474 | `agents/seo-audit/SKILL.md`, test | KEEP — referenced from a **skill doc** |
| `_psi_metric` | 1477–1479 | in-file ×5 | KEEP |
| `parse_pagespeed_response` | 1482–1524 | test only | KEEP (see note) |
| `run_cwv_measurement` | 1527–1603 | `agents/cwv-measurement/SKILL.md`, `services/technical_seo.py`, 2 tests | KEEP |
| `run_seo_audit` | 1606–2374 | `agents/firecrawl/SKILL.md`, `agents/website.py`, `services/technical_seo.py`, test | KEEP |
| `discover_competitors` | 2377–2549 | `agents/competitor.py` | KEEP |

> `parse_pagespeed_response` has no production caller — only a test. It is *not* DEAD: it is
> called in-file by `run_cwv_measurement` (verified), and the test pins its parsing contract.

---

## Findings

| # | line | symbol | verdict | evidence | proposed change | risk |
|---:|---:|---|---|---|---|---|
| 1 | 161–220 | `pull_rankings` | **DEAD** | All three death-proofs empty: (a) `git grep` across **all** file types returns only its own `def` and its own log string `"pull_rankings_empty"`; (b) no string-literal / `getattr` / `importlib` reference; (c) not in any `__init__` `__all__`, not a route, not a serializer. `git log -S'pull_rankings'` → introduced by `d44d9de` "Initial commit", never touched again. | Delete the function. | **mechanical** |
| 2 | 6 | `import random` | **DEAD (linked to #1)** | `random.` appears only at lines 174, 175, 181 — all inside `pull_rankings`. Deleting #1 without this leaves an unused import. | Delete the import together with #1. | **mechanical** |
| 3 | 488 | `import asyncio` | **DEAD** | `asyncio` is already bound at module level (line 5). AST check confirms the inner import rebinds an existing module-level name. | Delete line. | **mechanical** |
| 4 | 1619 | `import asyncio` | **DEAD** | Same as #3. | Delete line. | **mechanical** |
| 5 | 915 | `import re` | **DEAD** | `re` already bound at module level (line 7). | Delete line. | **mechanical** |
| 6 | 52–58 | DR coercion in `pull_backlinks.ahrefs` | **SIMPLIFY** *(latent bug)* | Conditional expressions bind looser than `or`, so this parses as `A if isinstance(...) else (B or C or 0)`. When `domain_rating` **is** a dict but its inner key is missing, `A` is `None` and `float(None)` raises `TypeError` — swallowed by the `except Exception` at line 59, silently yielding `authority = 0.0`. The `or … or 0` fallback never protects the dict branch it appears to. | Extract to a small `_domain_rating(dr_data) -> float` helper with explicit branches. **Behaviour-preserving only if the fallback stays 0.0.** | **needs review** |
| 7 | 317, 1943 | f-strings with no placeholder | **SIMPLIFY** | ruff `F541`. Line 317 is `f"No GA4/gtag snippet found on live page."` immediately followed by a real f-string concat — the `f` is vestigial. | Drop the `f` prefix. | **mechanical** |
| 8 | 253–259 | `provider_map` | **SIMPLIFY** | Dict literal is constructed inside the `for el in elements` loop, so it is rebuilt on every iteration and never varies. | Hoist to a module-level constant. | **mechanical** |
| 9 | 251–425 | `validate_tracking` result blocks | **SIMPLIFY** | 12 `results.append({...})` blocks across 175 lines, all of shape `{"element", "check_result", "detail": {"message", "fix", …}}`. Repetition count (12) clears the brief's "don't abstract for two occurrences" bar. | Table of `(element → check fn)` plus one `_result(el, status, message, fix, source)` builder. | **needs review** — each branch's exact status/message string is observable output |
| 10 | 1606–2374 | `run_seo_audit` | **SIMPLIFY** | A single **769-line** function — 30 % of the module. Named as a Q3 flag ("abstractions with exactly one implementation" / oversized units). | Split along its internal phases. Not attempted here: too large to do safely without reading all 769 lines. | **needs review** |
| 11 | 36 sites | in-function imports | **UNCERTAIN** | 36 function-level imports. Three provably redundant (#3–#5). The rest import `httpx`, `app.integrations.*`, `app.services.*`. Some are almost certainly circular-import breakers; I did not verify which. | Determine per-site whether a module-level import creates a cycle; hoist those that do not. | **needs review** |

---

## Cross-module finding (not a providers.py edit)

**The competitor-rankings feature is half-wired.** `pull_rankings` was written to populate it and
never connected. Tracing the other end:

- `CompetitorRanking` model exists — `models/findings.py:103`, table `competitor_rankings`
- It is exported in `models/__init__.py` `__all__` → **public API surface, so never DEAD**
- The table **is** created at runtime: `main.py:62` `Base.metadata.create_all` (migration
  `001_initial_schema.py:21` explicitly documents that tables come from metadata, not DDL —
  my first read of "no migration creates it" was wrong and I corrected it)
- The **only** application reference is a cascade `delete(CompetitorRanking)` at
  `api/clients.py:300`
- Nothing ever constructs a `CompetitorRanking(...)` row; nothing ever selects one

So the system maintains, and carefully cleans up, a table that is never written or read.
Removing the model would be a **behavioural/schema change** and is therefore a FINDING, not an
edit. → see UNCERTAIN Q1 in `SUMMARY.md`.

---

## NOT YET AUDITED in this module

Lines 531–2549 have not had a line-by-line read. Symbol-level Q1 is complete for all of them
(table above) and the mechanical findings #4, #5, #7, #10 come from verified tooling checks
within that range, but Q2 (already solved elsewhere?) and Q3 (simplest form?) remain open for:

`crawl_site` tail (531–733) · `check_broken_links` (736–911) · `optimize_on_page` (927–1098) ·
`run_technical_seo_audit` (1101–1448) · `parse_pagespeed_response` (1482–1524) ·
`run_cwv_measurement` (1527–1603) · `run_seo_audit` (1606–2374) · `discover_competitors` (2377–2549)

That is ~2,000 LOC, and `run_seo_audit` alone is 769 of them.
