# Round 2 — P0-2 fixed, fixture 1 prechecked, one new P0. STOP.

Branch `validation/adversarial-e2e`. No fixtures run beyond the resumed phase-2
verification. Spend this round: **$0.31** (5 precheck calls + 1 discovery + verification).

---

## 1. P0-2 fixed and scoped, not disabled — `c9cd2d9`

| | |
|---|---|
| ALLOWED | www ↔ apex, http → https, trailing slash, IDN/punycode |
| REFUSED | any variant or **redirect** changing the registrable domain — raises `CrossRegistrableDomain` |
| eTLD+1 source | Public Suffix List via `tldextract`, bundled snapshot (`suffix_list_urls=()`, no runtime network), **private suffixes on** so `alice.github.io` ≠ `bob.github.io` |
| enforcement | opt-in per call site; on for discovery pre-research + tracking tag detection. Competitor crawling still follows cross-domain redirects — that is the product working |

The `.com → .com.au` and `.co.uk → .com` guesses are gone. Both `fetch_url` and
`tracking.py` re-raise the exception past their `except Exception` handlers so it cannot be
downgraded to "fetch failed" or "no tags found".

**29 tests** (`tests/test_registrable_domain_scope.py`): www↔apex, http→https, `.co`→`.com`
refused, `example`→`exampleshop` refused, punycode/unicode pair, the live
`weareexample.com` incident end to end, and that the competitor path is unaffected.

One thing I got wrong and fixed: my first implementation did not IDNA-normalise before the
PSL lookup, so `пример.com` and `xn--e1afmkfd.com` compared as different domains. Caught by
the punycode test failing, not by review.

**Live proof:** fixture 9 phase 2 re-run touches `example.com` only. The same phase
previously reached `example.com.au` and `weareexample.com`.
See `validation/runs/09-broken-input/hosts.txt`.

---

## 2. NEW — P0-4 · DataForSEO search volume is silently discarded

Found while running the fixture-1 precheck. **Not fixed** — ledgered per your standing rule.

`dataforseo.search_volume()` returns `([], [])` — no rows, **no errors** — for a response
that contained real data:

```
DataForSEO 200, status_code 20000 "Ok.", result_count 3, cost $0.09
  vacuum feedthrough      search_volume 170
  cleanroom tacky mats    search_volume 110
  emi shielding gasket    search_volume 260
_task_items(body) returned 0 items
```

**Mechanism** — `dataforseo.py:194 _task_items`:

```python
items = first.get("items") if isinstance(first, dict) else None
if isinstance(items, list):
    return [i for i in items if isinstance(i, dict)]
return []          # ← silent
```

It assumes the Labs/SERP shape `result[0].items[]`. The Google Ads endpoint
`/keywords_data/google_ads/search_volume/live` returns keyword rows **directly in
`result[]`** with no `items` key, so every row is dropped and no error is raised.

**Blast radius** — `search_demand.py:914`:

```python
volumes, e6 = await dataforseo.search_volume(all_kw_names, location_code=location_code)
provider_errors.extend(e6)      # e6 is always []
if volumes:                     # always False
    ...
    dfs_rows.extend(kept)
```

So for up to 60 keywords per run: no volume data enters the dataset, `dataforseo` is never
added to `providers_used`, and `provider_errors` stays empty. The user is told nothing.

**Why this matters more than it looks:** combined with Ahrefs at 100% failure
(`Insufficient plan`), **the pipeline currently has no working search-volume source at all**,
while reporting no provider errors. That is the textbook P2 (silent degradation the user
cannot detect) feeding a P0 (whatever conclusion Phase 5 draws is drawn blind).

`keyword_difficulty` and the Labs endpoints (`keyword_ideas`, `related_keywords`,
`keyword_suggestions`, `competitors_domain`) use the `items[]` shape and are unaffected —
which is why historical runs still produced keyword datasets and the bug stayed hidden.

**Proposed fix:** `_task_items` should fall back to `result[]` when `result[0]` has no
`items` key, and `search_volume` should report an error when it receives a 200 with rows it
cannot parse. Needs its own regression test using the captured response.

---

## 3. Fixture 1 precheck — the stop-gate answer

US (location 2840), DataForSEO Google Ads, today. Ahrefs could not corroborate
(`Insufficient plan` on all five clusters), so **these are single-source figures**.

| cluster | total/mo | keywords with volume | under ~500? |
|---|---:|---:|---|
| **vacuum feedthroughs** | **270** | 4/5 | ✅ **best candidate** |
| EMI shielding gasket selection | 310 | 2/5 | ✅ but thin |
| borosilicate sight glasses | 650 | 4/5 | ❌ |
| cleanroom tacky mats | 670 | 3/5 | ❌ |
| ultrasonic level transmitter calibration | 1,030 | 4/5 | ❌ |

Per-keyword detail in `validation/fixture1-precheck.json`.

**Recommendation: vacuum feedthroughs.** 270/mo across the whole cluster, head term only
170, and 4 of 5 terms return measurable volume — so it is demonstrably *thin demand* rather
than *absent data*. EMI shielding gasket (310) is lower risk on paper but only 2 of 5 terms
return any figure, which conflates "no demand" with "no data" and weakens the fixture.

Rejected: ultrasonic level transmitter calibration is carried entirely by one 1,000/mo head
term; cleanroom tacky mats and borosilicate sight glasses both clear 500.

**Caveat:** US-only, one provider, one day. If you want a second opinion before committing
the fixture, Google Keyword Planner or Semrush would be independent — I cannot verify a
DataForSEO number with DataForSEO.

---

## 4. Not done, and why

| item | status |
|---|---|
| Blast-radius fixture (your item 2) | **blocked** — needs the domain you own whose fallback target is your test site |
| Fixtures 2, 3, 8, 9-rebuild (self-hosted) | **blocked** — needs your staging hostnames |
| robots.txt + 1 req/s/host + contact UA for the competitor path | **not started** — wanted your go-ahead on the UA contact URL before putting it on outbound traffic |
| Fixtures 1, 4, 5, 6, 7, 10 | not started; per your run order these come after the four self-hosted |

---

## 5. Something you should know about this working tree

A **concurrent technical-SEO workstream** is editing the same tree: `technical_seo_rules.py`,
`technical_seo_schemas.py`, `technical_seo_companion.py`, `technical_seo.py`, a new
`technical_seo_checklist.py`, `technical-seo-audit/SKILL.md`, `TechnicalSeoCard.tsx`, and two
test files. Earlier in the session a dead-man-switch feature appeared the same way and I
committed it by accident with `git add -A` (commit `5bc99fa`) — since then I have staged
explicit paths only, and none of their work is in `c9cd2d9` or `598f146`.

**Their change breaks a test:**
`tests/test_ahrefs_site_audit.py::test_issues_from_ahrefs_maps_severity_and_rule_id`
expects `affected_url_count == 5`, gets `10`. It passes at HEAD and fails with their
working-tree edits; `technical_seo_rules.py` contains no reference to `web_fetch`,
`tldextract` or `fetch_url`, so it is not mine. I have not touched it — fixing someone
else's in-flight test means guessing their intent.

Current suite: **815 passed, 1 failed** (that one). My own touched areas: 99 passed.

---

## Ledger of open defects

| id | sev | defect | status |
|---|---|---|---|
| P0-1 | P0 | Parse failure on a self-correcting LLM response discards a correct refusal and substitutes confident fabrications | open |
| P0-2 | P0 | Host-variant fallback crawled a different company's site | **fixed `c9cd2d9`** |
| P0-3 | P0 | Heroku/W3C returned as tiered "Strong Direct Competitors" | open |
| P0-4 | P0 | DataForSEO search volume silently discarded, no error | **new**, open |
| P1-1 | P1 | Phase 3 reports `complete` with `pages_found: 0` | open |
| P1-2 | P1 | Phase 5 runs on a 1-URL sitemap | open |
| P2-a..d | P2 | Misattributed Ahrefs failure note; fabricated `authority_score`; dead `backlinks` key read; Ahrefs cost unmetered | open |

---

## What I need

1. **Staging hostnames** for fixtures 2, 3, 8, 9 and the blast-radius domain.
2. **Confirm `vacuum feedthroughs`** as fixture 1's topic, then a company selling into it.
3. **A contact URL** for the crawler User-Agent before I put it on outbound traffic.
4. **A decision on P0-4** — it is a two-line fix, but fixing it changes what every remaining
   fixture measures (Phase 5 would see volume data for the first time). Fixing it *before*
   the matrix gives a truer test; fixing it after keeps this round's findings comparable.
   I would fix it first, for the same reason you gave for fixing P0-2 first.
