# Pre-run report — cost estimate, and a blocker that makes the matrix measure the wrong thing

**Nothing has been run. No provider spend incurred.** Branch `validation/adversarial-e2e`,
clean tree at `07a0be8`.

---

## Environment check (ground rules 1–3)

| check | result |
|---|---|
| clean tree, on a branch | yes — `validation/adversarial-e2e` |
| environment | `development`, local Postgres 18.4, 21 tables |
| `WORDPRESS_ALLOW_LIVE_PUBLISH` | **false** ✓ |
| `WORDPRESS_DEFAULT_STATUS` | `draft` ✓ |
| `USE_MOCK_PROVIDERS` / `USE_MOCK_LLM` | `false` / `false` — real calls ✓ |
| keys present | Ahrefs, DataForSEO, OpenRouter, Anthropic, Firecrawl, Brandfetch |
| keys absent | Perplexity, Google PageSpeed, Moz (set but empty) |

---

## Cost estimate (ground rule 4)

From `api_usage_log` — **1,327 real logged calls** across two completed clients.

| basis | value |
|---|---|
| attributed spend, Click Trends | $2.98 (224 calls) |
| attributed spend, ARG Finance | $2.77 (282 calls) |
| **median per full client run** | **~$2.88** |
| unattributed (`client_id` NULL) | $3.89 / 821 calls |
| total logged | $9.64 |

Per-phase, per client:

| phase | cost | phase | cost |
|---|---:|---|---:|
| content_production | $0.87 | website_situation | $0.17 |
| search_demand | $0.63 | tracking_access | $0.14 |
| competitor_market | $0.20 | content_audit | $0.12 |
| discovery | $0.20 | content_planning | $0.11 |
| site_architecture | $0.19 | on_page_seo | $0.10 |
| content_strategy | $0.18 | technical_seo | $0.01 |

**Projected matrix cost**

| stage | runs | estimate |
|---|---:|---:|
| Phase B — 10 fixtures × phases 1–12 | 10 | **$29–48** |
| Phase D — 8 faults on 1 fixture | 8 | **$23–38** |
| Phase E — reads artifacts, no runs | 0 | $0 |
| **total** | 18 | **$52–86** |

3× median abort threshold = **$8.64 per single run**.

**Caveat: this number is incomplete.** Ahrefs cost is `NULL` on all 543 calls — the meter
never records it, so Ahrefs quota consumption is invisible and excluded from every figure
above. That is itself a defect (P2: you cannot see what Ahrefs costs you).

---

## BLOCKER — the providers are already down, so the matrix would measure the wrong thing

| provider | calls | errors | rate |
|---|---:|---:|---:|
| **ahrefs** | 543 | **543** | **100.0%** |
| openrouter | 164 | 27 | 16.5% |
| dataforseo | 620 | 35 | 5.6% |

**Ahrefs has never once returned data in this environment**, across all history including
today (2026-09-15: 399 of 723 calls errored). Two causes:

- `{"error": "Insufficient plan"}` — 300 calls. The plan does not cover the endpoints called.
- `code 10029 "Rate limit exceeded"` — 243 calls.

**OpenRouter is out of credits:** `HTTP 402 — "This request requires more credits, or fewer
max_tokens"` (27 calls). OpenRouter is the single largest cost centre
(content_production, $0.87/client), so Phase 10 will fail on every fixture.

**DataForSEO returns `40200 Payment Required`** on a minority of calls.

### Why this invalidates the plan as written

You asked for real keys because "mocked providers cannot find the failures I care about."
Correct — but the providers are *already* failing 100% / 16.5% / 5.6% of the time. A fresh
10-fixture matrix would not measure "is the output accurate." It would measure "what does the
pipeline do when its primary data source is entirely absent" — on all ten fixtures at once,
with no working-provider baseline to compare against.

Specifically, **Phase E (accuracy audit) cannot run as designed.** Its claim types are
*search volume, keyword difficulty, trend, competitor sets, authority/backlink counts* —
Ahrefs supplies most of these and returns nothing. There would be almost no provider-sourced
numbers to verify. The fabrication rate — the headline number you want — would be measured
against a pipeline running blind, which is not the number you asked for.

---

## What I established for free, from existing artifacts

Two clients have **all 13 phases `complete`** despite Ahrefs at 100% failure. Captured to
`validation/runs/_preexisting/` (1.5 MB, 11 phase summaries each + the full 1,327-row call log).

### The canary, traced

You flagged `authority_score = 0.0` as returning identically for "unreachable" and "parse
failed". The real behaviour is different from the hypothesis, in both directions:

**Better than feared — the fallback is honest at the data layer.** When Ahrefs failed,
`pull_backlinks` fell through to `live_signals()` and the stored record says so:

```
backlink_summary.summary.provider          = 'live_site_signals'
backlink_summary.summary.referring_domains = None          ← null, not 0
backlink_summary.summary.note              = 'Ahrefs/Moz keys not set — authority
                                              estimated from live page signals only;
                                              referring_domains unavailable.'
```

**Defect 1 (P2) — the disclosure misdiagnoses the cause.** The note says *"Ahrefs/Moz keys
not set"*. The Ahrefs key **is** set. The actual failure is `Insufficient plan` and
`Rate limit exceeded`. An operator reading this goes and sets a key that is already set, and
never learns their plan does not cover the endpoint. Evidence:
`validation/runs/_preexisting/*/website_situation_summary.json` vs the 543 Ahrefs rows in
`api_usage_log.json`.

**Defect 2 (P2, arguably P0) — `authority_score` is a fabricated number in a field that
looks like a Domain Rating.** Click Trends stored `authority_score = 56.2`, ARG Finance
`15.0`. Neither is a Domain Rating. Both come from `providers.py`:

```python
authority = min(70.0, 15 + len(parser.hrefs) * 0.4 + len(parser.json_ld) * 5)
```

That is *15 plus 0.4 × the number of links on the homepage*. It is stored in the same field
that would otherwise hold a real Ahrefs DR, and persisted to
`backlink_snapshots.authority_score` as a `Numeric(5,2)`. 56.2 reads as a Domain Rating to
any consumer. It is not one.

**Defect 3 (P2) — the value is then never read, because of a key mismatch.**
`search_demand.py:960`:

```python
bl = website.get("backlinks") or website.get("authority") or {}
authority = bl.get("authority_score") or bl.get("domain_rating")
```

The stored key is **`backlink_summary`**. Neither `backlinks` nor `authority` exists at the
top level of `website_situation_summary` — confirmed against both clients. So `bl` is always
`{}`, `authority` is always `None`, and Phase 5 silently falls through to
`competitive.client_baseline_maturity` (42.4 / 38.8). A dead read that no test and no output
would reveal.

**Net effect on the canary trace you asked for:** the fabricated authority number does *not*
currently reach any user-facing recommendation — because of Defect 3, not by design. I
searched every downstream phase summary for authority/DR/backlink assertions; the only hit is
an unrelated calendar label ("Authority"). So the canary is real but currently defused by a
second bug. If Defect 3 is ever fixed without fixing Defect 2, the fabricated number starts
flowing.

---

## Two gaps in the harness I would need to build

1. **`run_all_phases.py` covers phases 3–12 only.** Phases 1 (discovery) and 2 (tracking)
   are not in it and are interactive — discovery expects CDD upload / answers. I need to seed
   those per fixture.
2. **Nothing captures raw provider responses.** Requirement 5 asks for them in every
   `phase<NN>.json`. `api_usage_log.error_detail` holds error bodies but success bodies are
   discarded. I would add an httpx-level capture wrapper for the run harness only.

---

## What I need from you

**Decision first — the matrix is not worth running until providers work:**

- **(a) Fix Ahrefs plan + top up OpenRouter/DataForSEO, then run the full matrix.** Best
  data, ~$52–86, gives you the real fabrication rate.
- **(b) Run the matrix anyway as an "all providers down" stress test.** Cheaper (most calls
  fail fast), and it would harden the degradation paths — but it answers Phase C/D/F, not
  Phase E. The headline fabrication number would be meaningless.
- **(c) Skip fresh runs. Deep-audit the two captured runs I already have.** $0. Gets you
  Phase C (flow), Phase D canary tracing, and Phase F (output quality) against real
  artifacts. Cannot give you Phase E accuracy or the 10 adversarial fixtures.

**I recommend (a), and (c) in the meantime** — the captured artifacts already yielded three
defects for free.

**Domains I need** (you said you would supply them). For each: a real URL, and for fixtures
needing it, the business name + a one-line description so Discovery has CDD input.

| # | fixture | what it must break | what I need |
|---|---|---|---|
| 1 | **NO DEMAND** | invents a plan where none is justified | niche B2B domain, near-zero volume |
| 2 | JS-ONLY SPA | HTML-source crawling | SPA domain |
| 3 | NO SITEMAP/ROBOTS | discovery has no seed | domain with neither |
| 4 | HUGE | caps, `MAX_PAGES=8` truncation | 50k+ URL ecommerce |
| 5 | MULTILINGUAL | hreflang, non-English primary | multi-locale domain |
| 6 | BRAND NEW | every provider empty | domain registered recently |
| 7 | NAME COLLISION | competitor discovery returns the giant | brand colliding with a major one |
| 8 | HOSTILE | Cloudflare / rate limiting | bot-protected domain |
| 9 | BROKEN INPUT | confident profile of nothing | 2×redirect→404, or parked |
| 10 | NON-ENGLISH MARKET | thin provider coverage | non-US/UK primary market |

Fixture 1 is the one you called P0-critical; if you supply only one, supply that.
