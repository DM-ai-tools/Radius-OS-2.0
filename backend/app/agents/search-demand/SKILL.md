---
name: search-demand
description: Phase 5 search demand and keyword research. Use for keyword research, search demand, create topic, keyword clustering, best opportunities, evergreen keywords.
---

# Search Demand & Keyword Research

You are the Radius OS Phase 5 agent. Expand seeds from Discovery / Website / Competitors using **Ahrefs + DataForSEO**. Never invent search volume.

## Multi-mode keyword seeding (Ahrefs first)

For each CDD / Discovery seed keyword, build a **multi-keyword dataset** via Ahrefs:

Every seed root must be expanded into **all four** match classes. The class is
decided by the keyword's actual relationship to the seed — never by which
endpoint returned it (the Ahrefs `terms` feed also returns contiguous phrase
matches, so trusting the endpoint collapses everything into `related`).

| Match class | Meaning | Ahrefs source | DataForSEO top-up |
|---|---|---|---|
| **exact** | The seed itself (plural / punctuation insensitive) | seed overview | seed row |
| **phrase** | Seed tokens appear **contiguously** in the keyword | `matching-terms` `match_mode=phrase` | `keyword_suggestions` |
| **related** | **Every** seed token present, but not contiguous | `matching-terms` `match_mode=terms` | `related_keywords` |
| **broad** | Partial or purely topical overlap | `related-terms` | `keyword_ideas` |

For a **one-word seed**, "contiguous" and "contains every token" are the same
test, so the endpoint decides phrase vs related vs broad.

**Hard filter:** keep only keywords with **volume > 10**. Do not invent volume.
If a class has no result above the floor, fill it from real lower-volume
keywords flagged `below_volume_floor` — never fabricate a keyword.

**Search intent:** every keyword row must include `intent`
(`informational` | `commercial` | `transactional` | `navigational`). Prefer
Ahrefs `intents` when present; otherwise classify from the keyword text. Carry
intent through `seed_clusters`, `keyword_dataset`, `service_clusters`, and
opportunity buckets.

**Relevance cleaning (CDD / services / pages):** after volume filtering, drop
keywords that are not supported by client evidence:

- CDD `business_keywords`, `products`, `products_for_promotion`
- service and sub-service page terms (`cdd_terms`, titles, paths)
- website themes / target keywords
- valid seed lineage (exact / phrase / related of a supported seed)
- approved competitor **gaps** that still overlap the client's services

Block competitor brand names, client-brand-only noise, URLs, and stale years.
Broad terms need distinctive service/CDD/page overlap — topical adjacent-industry
noise does not stay in clusters or topics.

Every original seed still appears once in `service_clusters` even when all of
its expansions were removed. Record exclusions in `keyword_cleaning` (counts by
reason/class/source and a small sample). Do not send excluded terms into Phase 6.

**LLM relevance pass (semantic, after the deterministic filter):** token/phrase
overlap catches literal matches but misses keywords that are relevant in
meaning without sharing words with the CDD/services text, and can keep
adjacent-industry noise that happens to share a generic token. After the
deterministic pass, every surviving keyword is re-checked by Gemini 3 Flash
(`keyword_llm_relevance.llm_filter_keywords_by_seed`), grouped by seed, against
an actual description of the business — name, industry, services/products,
geography (the same fields used to build the deterministic `RelevanceContext`)
— not just the bare company URL. Judge relevance against what this specific
client offers, not the industry category in general. On any provider failure,
fail open (keep the keywords) rather than silently dropping real demand; record
the failure in `keyword_llm_relevance` audit's `errors` so it's visible, not
swallowed.

Output structure (required):

- `seed_clusters[]` — one cluster per seed with `exact[]`, `phrase[]`, `related[]`, `broad[]`, plus `classes_missing[]`
- `keyword_dataset[]` — flat list of all seeded keywords with `seed` + `match_class`
- `keyword_seeding.class_counts` — counts per class
- `keyword_seeding.class_coverage[]` — per-seed counts + any missing classes
- `keyword_seeding.seeds_with_all_classes` — how many seeds filled all four
- `keyword_cleaning` — input/kept/removed counts, `removed_by_reason`, exclusion sample

**Seeds must describe the client**, never a competitor. Competitor brand names
(e.g. an agency's name from the competitive scorecards) are not keyword seeds.

Keep every seed-to-keyword association. A keyword may validly appear under more
than one seed; do not globally deduplicate it out of later seed clusters.

Then continue opportunity scoring + keyword_clustering (intent/funnel) on the merged set.
Match clusters to the **Phase 3 site sitemap**, then draft topics only for **new** clusters.
URL mapping runs afterward in Phase 6.

## Phase 5 pipeline (execution order)

```
PHASE 3 — CLIENT WEBSITE SITEMAP (prerequisite)
│
PHASE 5 — SEARCH DEMAND & KEYWORD RESEARCH
│
├── Keyword Research          (multi-mode seeding + volume/KD/gap)
│       ↓
├── Keyword Cleaning          (CDD / services / pages relevance filter)
│       ↓
├── Keyword Clustering        (intent + funnel on every cluster)
│       ↓
├── Sitemap classification    (existing_topic | existing_review | new_topic)
│       ↓
├── Topic Creation            (create_topic — new_topic clusters only)
│       ↓
└── Search Intent / Cluster Understanding
             │
             ▼
PHASE 6 — SEO STRATEGY & INFORMATION ARCHITECTURE
│
├── Cluster → URL Mapping     (honors Phase 5 dispositions)
├── Existing URL matching     (HIGH → OPTIMIZE EXISTING)
├── New URL identification    (new_topic → CREATE slug)
├── Site structure            (target_url_tree + navigation)
└── Cannibalization decisions (cluster_ownership + competing_urls)
             │
             ▼
PHASE 8/9 — CONTENT PLANNING
│
├── Existing content → audit/optimization  (refresh | no_action | retire)
└── New content → page planning            (create)
             │
             ▼
PHASE 11 — ON-PAGE SEO
│
├── Title
├── H1/H2
├── Meta
├── Content
└── Internal Linking
```

Each phase pack includes `phase_pipeline.stages[]` with per-step readiness for the command center.

## Skills (wired)

1. **keyword_seeding** — Ahrefs multi-mode expansion (exact / phrase / related / broad, vol > 10)
2. **keyword_clustering** — SearchFit clustering with intent/funnel (see `keyword-clustering/SKILL.md`)
3. **create_topic** — drafts only for `new_topic` clusters after sitemap classification (see `create-topic/SKILL.md`)

## Scoring (must output the best)

Rank every keyword against:

1. Demand (Ahrefs ∩ DataForSEO volume when both exist)
2. Difficulty / winnability
3. Competitor gap
4. Trend (rising / stable / declining)
5. Evergreen vs seasonal / newsy
6. Business fit

Buckets: **Best opportunities**, **Strong evergreen**, **Trend plays**, **Avoid / low ROI**.

## Triggers

- "keyword research" / "search demand" / "phase 5"
- "create topic" / "keyword clustering"
- "best opportunities" / "evergreen keywords"

## Rules

- Gate: only after `ready_for_phase5`
- Partial provider failure is OK — report provenance, do not fabricate metrics
- Content SEO Specialist approves into shared memory
- Never invent keywords or volumes; CDD seeds are ground truth for expansion roots

## Role ownership

- **Owner (trigger + approve): Content SEO Specialist** (`content_seo_specialist`)
- **Sub-skills (same owner):** `keyword_seeding`, `create_topic`, `keyword_clustering`
- **Gate:** Approve publishes `search_demand_summary` before Phase 6
