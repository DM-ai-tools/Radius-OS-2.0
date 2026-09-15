# Adversarial validation — interim ledger

**STOP point.** Nothing fixed. Branch `validation/adversarial-e2e`.
Phase 12 never ran in write mode; `WORDPRESS_ALLOW_LIVE_PUBLISH=false` throughout.

## Coverage — read first

| | |
|---|---|
| fixtures run | **1 of 10** (#9 BROKEN INPUT, `https://example.com`) |
| phases run | **1–6 of 12** |
| spend | **$0.20** of the $52–86 estimate |
| artifacts | `validation/runs/09-broken-input/` — 6 phase files, 293 captured provider req/resp |

Only one fixture because **the other nine domains have not been supplied** — I listed what I
need in `PRE-RUN-BLOCKER.md` and it is still outstanding. Fixture 9 was the one I could
source myself (`example.com` is IANA's reserved placeholder: no business, no content, safe to
crawl).

Two harness gaps had to be closed first, both flagged pre-run and both now working:
Phases 1 and 2 are **human-in-the-loop** (they park at `in_progress` awaiting
`submit_questionnaire` / `submit_known_changes`), and nothing captured raw provider bodies.
`backend/scripts/validation_harness.py` now simulates both gates and captures every
request/response below `httpx.AsyncClient.send`.

**Unrun is not passed.** Fixtures 1–8 and 10, and phases 7–12, are untested.

---

## The single most important result

Fixture 9 asked: *does Phase 1 fail loudly, or produce a confident profile of nothing?*

**Neither. It produces a confident, structured profile of *other people's businesses*.**

---

## P0 — confidently wrong output reaches the user

### P0-1 · A correct LLM refusal is converted into a fabrication

**Artifact:** `09-broken-input/phase01_discovery_agent.json` → `events[2].payload.research_fields`
**Raw evidence:** `09-broken-input/providers.jsonl` line 2 (the OpenRouter response)
**Code:** `app/integrations/llm.py:690` (fallback) + `:534` (`_parse_json_content`)

The model did exactly the right thing for a placeholder domain — every field `null`/`[]` at
`confidence: 0.0`. It then noticed its own first block was malformed and emitted a corrected
one:

```
{...b2b_c50": null}
```
Note: The above malformed placeholder keys were unintentional. Correcting with the proper schema below.
```json
{ "inferred_industry": {"value": null, "confidence": 0.0}, ... }
```

`_parse_json_content` strips one leading fence and one trailing fence, then `json.loads` the
whole string — which now contains **two** JSON objects plus prose. It raises
`Extra data: line 64 column 1`. The salvage branch at `:540` is skipped because after
fence-stripping the text *does* start with `{`. There is no code path that survives a
self-correcting model, and self-correction is common.

`synthesize_json` returns `None`, and `live_pre_research:690` does:

```python
parsed = await synthesize_json(system, user)
if not parsed:
    return mock_pre_research(display_name, primary_url, industry=industry)
```

**The sibling fallback 90 lines earlier (`:600`, for fetch failure) caps every confidence at
0.25 and rewrites the values. This one does neither.** So the canned template is returned at
its full authored confidence, and the client sees:

| field | value | confidence |
|---|---|---|
| `public_reviews_summary` | "Public reviews lean mixed: service praised; price sensitivity mentioned." | **medium (0.45)** |
| `products` | `["Core unknown offering", "Complementary services", "Support / aftercare"]` | **medium (0.65)** |
| `positioning` | "Mid-market unknown brand with quality-focused messaging" | **medium (0.60)** |
| `google_business_signals` | "Local / regional presence suggested by site copy — confirm listing." | low (0.35) |
| `business_model` | "VALIDATION 09 Broken Input appears to operate in unknown via example.com" | **high (0.72)** |

There are no public reviews of `example.com`. There is no site copy suggesting a local
presence. Every one of these is invented, and one of them asserts customer sentiment.

**Mechanism:** parse failure → honest answer discarded → confident template substituted.
**Fix:** (a) make `_parse_json_content` take the *last* complete JSON object when several are
present; (b) give the `:690` fallback the same confidence-cap and value-rewrite as `:600`;
(c) better — on parse failure return `None` upward and let Discovery report "could not
research" rather than substituting anything.

### P0-2 · The crawler silently audits a different company's website

**Artifact:** `09-broken-input/providers.jsonl` — 65 requests to `example.com.au`, 3 to
`www.weareexample.com`
**Code:** `app/integrations/web_fetch.py:110` `_url_host_variants`, fallback logged at `:321`

`_url_host_variants` guesses alternative domains, including `.com → .com.au`:

```python
if bare.endswith(".com") and not bare.endswith(".com.au"):
    alts.append(bare[:-4] + ".com.au")
```

`example.com` was *correctly* identified as a placeholder by `_looks_parked_or_placeholder`
(`:308`). Instead of stopping and reporting that, the loop `continue`s to the next variant.
`example.com.au` is a **real, unrelated Australian company** that redirects to
`weareexample.com`, so the crawler fetched their pages —
`/who-we-help/gold-coast`, `/who-we-help/dubai`, `/who-we-help/hotels-travel` — and treated
them as the client's site. The only trace is one `info`-level log line.

For a real client this puts another legal entity's site structure and content into the
deliverable. The `.com.au` guess is a plausible typo-correction for an AU client; it is not
acceptable to then proceed silently when it lands on a different organisation.

**Fix:** a parked/placeholder verdict must terminate with "this domain has no auditable
site", not fall through to a guessed domain. If host fallback is kept, the resolved host must
be surfaced in the deliverable and require confirmation before any content is attributed.

### P0-3 · Fabricated tiered competitor set

**Artifact:** `09-broken-input/phase04_competitor_market_agent.json` → `summaries.competitive_landscape_summary.competitors`
**Code:** `app/agents/competitor.py` + `ads-category-competitors` skill

Phase 4 returned, for a business that does not exist, a structured competitive analysis:

| competitor | tier |
|---|---|
| **Heroku** | Tier 2 — "Strong Direct Competitors" |
| **W3C (World Wide Web Consortium)** | Tier 3 — "Emerging Challengers" |
| **MDN Web Docs**, **IANA**, **Glitch** | listed |

These are the outbound links on the IANA placeholder page, promoted into a
`tiered_16_parameter` analysis with tier names. Phase 4 reached `pending_signoff` and was
approved to `complete`. Nothing in the output signals that the competitor set is unusable.

---

## P1 — pipeline proceeds on garbage

### P1-1 · Phase 3 reports `complete` with zero pages

`phase03_website_situation_agent.json`: `pages_found: 0`, `indexable: 0`,
`site_sitemap.url_count: 1` — and `status.after_approve: "complete"`.
A website audit that found no website is not a complete website audit. Phase 4 then ran on
it and produced P0-3.

### P1-2 · Phase 5 consumed an empty site and still ran

`search_demand` ran 107.8 s on a client with a 1-URL sitemap, reaching
`awaiting_service_selection`. DataForSEO returned `40102 No Search Results`. The correct
behaviour for fixture 9 — and the whole point of fixture 1 — is to stop.

---

## What worked (record these before changing anything)

| behaviour | evidence |
|---|---|
| Phase 2 disclosed missing Google APIs honestly | *"Google APIs not connected… Historical metrics stay empty."* — `phase02` events |
| Gates 3, 6 genuinely blocked | `phase03/phase06` `blocked: true`, 0 provider calls, status unchanged |
| Backlink fallback discloses its provider | `provider: 'live_site_signals'`, `referring_domains: None` (null, not 0) |
| Placeholder detection itself works | `_looks_parked_or_placeholder` correctly flagged `example.com` — the bug is what happens next |

---

## Carried from the pre-run audit (evidence in `validation/runs/_preexisting/`)

- **P2** — the backlink note says *"Ahrefs/Moz keys not set"* when the key **is** set; the real
  failure is `Insufficient plan` / `Rate limit exceeded` (543/543 Ahrefs calls).
- **P2** — `authority_score` is `min(70, 15 + 0.4 × len(hrefs) + 5 × len(json_ld))`, a
  link-count heuristic stored in a field that reads as a Domain Rating (56.2 / 15.0 for the
  two real clients) and persisted to `backlink_snapshots.authority_score`.
- **P2** — `search_demand.py:960` reads `website["backlinks"]` / `["authority"]`; the stored
  key is `backlink_summary`, so the value is never read and Phase 5 silently falls back to
  `client_baseline_maturity`.
- **P2** — Ahrefs cost is `NULL` on all 543 calls: Ahrefs spend is invisible to the meter.

---

## Fabrication rate

**Not reportable yet.** Phase E requires three fixtures and independent verification of every
factual claim; one fixture is run and Ahrefs (the source of most checkable numbers) is at
100% failure. What can be said from fixture 9: of the 7 populated Discovery research fields,
**7 were fabricated** — but n=1 fixture is not a rate.

---

## What I need to continue

1. **The nine remaining domains** (table in `PRE-RUN-BLOCKER.md`). Fixture 1 (NO DEMAND) above all.
2. **A decision on P0-2 before any further crawling.** The host-variant fallback means every
   fixture risks auditing a third party's site. I would rather not run nine more fixtures
   while that is live — tell me if you want it disabled for the run, which changes what the
   matrix measures but stops the pipeline touching unrelated domains.
3. Confirmation that Phase 5's `awaiting_service_selection` gate should be auto-answered by
   the harness (like phases 1–2), or treated as a legitimate stop.
