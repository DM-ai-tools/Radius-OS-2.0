# Keyword pool, industry keywords, hierarchical URL map, secondary keywords

Date: 2026-10-27. Status: approved 2026-10-27 (the owner set the expiry to 180 days). Nothing is built.
Owner's words are marked **Decided**. Anything marked **My call** is a choice
made while writing this and is open to change.

## Why

Each client's keyword research is fetched from scratch. Two dental clients
that share most services pay the keyword provider twice for the same
searches, and the second run is slower. The research also starts from the
client's own services only, so category-level searches for the industry
(what a buyer types to find "a digital marketing agency") are never
fetched for the home page. In the URL map, levels L1 to L4 are often empty
or follow the URL path rather than the keywords, and many rows have no
secondary keywords.

## Goals

1. **Industry keywords.** For the client's industry, fetch the
   category-level keywords that a home page should target, covering the
   funnel stages and search intents a home page needs.
2. **Keyword pool.** Store every fetched keyword. A later client reuses the
   saved keywords for services it shares with earlier clients, and only the
   missing services are fetched.
3. **Hierarchical URL map.** L1 to L4 form a clear tree built from the
   keywords and the client's services and sub-services. Every keyword
   cluster lands in exactly one place in the tree.
4. **Secondary keywords on every row** of the URL map, with real search
   volumes and no label.

## Non-goals

- No change to the cleansing rules (buyer fit, relevance, service contract).
  They run on reused keywords exactly as on fetched ones.
- The 15% top-of-funnel top-up, the SEO synonym seeds and cross-seed
  duplicates are separate open questions and are not touched here.
- No back-fill of earlier clients' runs into the pool, unless the builder
  finds a cheap way and the owner agrees.
- No change to report layouts beyond what the new data needs.

## Anti-goals (a failure even if it technically works)

- Client A's place-specific keywords ("dentist melbourne") appearing in
  client B's research.
- Any invented volume. A keyword without a measured volume is never shown
  as if it had one.
- A label, tag or footnote on modifier-built secondary keywords.
- A reused keyword skipping the new client's own filters.
- The pool making a run slower or less reliable than today. If the pool is
  unreachable, the run behaves exactly as it does now.

## Constraints that already exist

- DataForSEO is the only keyword data provider. The model may suggest
  terms; it never supplies metrics.
- Tables are created by SQLAlchemy `create_all` on startup and tracked by
  Alembic migrations (`backend/alembic/versions`, latest `008`). The pool
  needs a new migration `009`. **Approving this document approves that
  schema change.**
- Phase 5 must stay within its time budget (about 5 minutes). Pool lookups
  must be a small number of queries per run, not one per keyword.
- The working tree holds other people's uncommitted work in
  `service_seeding.py`, `keyword_seeding.py`, `search_demand.py` and
  `url_mapping.py`. Build on top of it; revert nothing.
- Nothing is committed until the owner asks.

## 1. Industry keywords

**Decided.** For the client's industry, the system picks keywords related to
the industry, checks which funnel stages and which intents a home page needs,
and fetches those keywords. The owner delegated the research of what a
home page needs; the findings are below.

What the research found (practitioner and vendor sources, no single
authority):

- A home page targets one to three broad terms that describe the business,
  without a city and without hyper-specific phrases. Service pages go deeper.
- Intent maps to funnel stage by convention: informational is top of funnel,
  commercial comparison ("best", "top", "vs") is middle, transactional
  ("price", "hire", "near me") is bottom.
- Terms for a digital marketing agency include "digital marketing agency",
  "online marketing company", "SEO services", "PPC management".

**Design.**

- A new step before per-service seeding asks the model for 5 to 10
  category-level terms for the industry (for example "digital marketing
  agency", "online marketing company", "marketing services"). It follows
  the pattern of the existing synonym and sub-service seed resolvers: a
  per-run cache, a fallback when the model is unavailable, and a tag on
  every resulting row (`seed_origin="industry"`). These seeds are extra to
  the existing seed budget.
- Each term is fetched across the funnel and intent grid a home page needs.
  **My call:** commercial and transactional are the bulk; navigational
  (brand names) is not fetched; informational is capped at 10% of the
  industry layer. The cap is a setting.
- Place-specific variants ("... near me", "... melbourne") are not part of
  the industry layer's pool entries (see section 2).
- The home page row in the URL map takes its primary keyword from this layer:
  the highest-volume in-scope term that survives the filters. Its secondary
  keywords come from the rest of the layer.
- The industry terms are themselves stored in the pool keyed by industry, so
  the second dental client reuses them.

**More calls (settled after a review of the repo).**

- **Fetch path.** Industry seeds go through the same seed fetch as service
  seeds (`expand_seed`), so they get the same pool, the same provider
  filters and the same time budget. `expand_seed` gets an
  `informational_share` parameter. Service seeds keep today's 15% floor;
  industry seeds use the 10% cap. The industry layer is capped at 8 seeds and
  joins the same bounded concurrent fetch, so it counts against the Phase 5
  deadline guard.
- **Which service the industry terms belong to.** None. The relevance
  contract judges (service, keyword) pairs, and "digital marketing agency" is
  not one service. Industry-layer rows are judged against the client's whole
  service list and the industry's own name: a row passes when any of the
  client's service families fits, or the keyword names the industry. The
  strictness of each rule is unchanged; only the "which service" lookup
  accepts the industry target. The builder finds the smallest change for
  this.
- **If nothing survives for the home page.** The home row falls back to the
  highest-volume head term from the client's own services, and the report
  says so. It is never left blank or invented.
- **How the industry layer is keyed.** `industry_slug` only knows dental,
  bakery and saas, so it returns nothing for "digital marketing agency". Key
  by the client's industry text, normalised, with the slug used where it
  exists. Matching reuses `same_concept`. Wording that differs ("dental
  clinic" and "dentist") may miss reuse; it never merges unrelated
  industries.

Left to the builder: the exact prompt, the exact grid, and where in
`search_demand.py` the step sits.

## 2. Keyword pool

**Decided.**

- All fetched keywords are stored and reused across clients.
- Reuse is per service. A new client's services are compared with what is
  saved; keywords for services in common are reused; only the missing
  services are fetched. (Example given: 3 shared services reused, 2 new ones
  fetched.)
- Keywords that are specific to a location are never reused. Only the
  place-free keywords are.

**My calls.**

- **The reuse unit is a seed's provider fetch, not the cleaned result.** The
  pool caches what the provider returned for one seed query, before the
  client's anchor filter, the LLM query variants and the volume floor are
  applied. Those are client-specific, so applying them after reuse keeps the
  result identical to a fresh fetch. A "shared service" is simply a client
  whose planned seeds are the same query strings as an earlier client's.
  The lookup goes seed by seed over the new client's own planned seed set:
  sub-services the new client excludes are never planned, so they are never
  pulled in.
- **Matching seed text.** The key is the provider-cleaned, normalised seed
  (`clean_provider_seed`) plus country and language. `canonical_name` and
  `same_concept` from `service_taxonomy.py` are used only to recognise that
  two differently worded seeds name the same service ("SEO services" and
  "SEO"); when they do, the first client's rows are served for both. A seed
  with no canonical form is matched on exact normalised text only.
- **LLM query variants** are separate provider queries, each cached the same
  way. A failed variants call has no effect on the seed's own fetch status.
  The service is the reuse key, not the industry, so "SEO" is shared by
  every client that sells it. The industry layer is keyed by industry
  (section 1).
- **Same country and language.** Volumes differ by country. The pool key is
  the country-level location code (`coerce_labs_location`) plus language
  code. A client in another country fetches its own.
- **Freshness.** Saved keywords expire after 180 days (setting
  `KEYWORD_POOL_TTL_DAYS`). An expired seed is re-fetched on next use and
  its saved rows replaced.
- **Raw provider rows are stored, not the cleaned result.** Each client's
  filters differ, so cleansing runs after reuse, on every client, always.
- **What "location-specific" means.** A keyword is location-specific if it
  contains a place (city, suburb, state, region, country when the market is
  that country), "near me", "nearby", or a place named in the client's own
  geography. Detection is a deterministic word list built from the existing
  place lists in the repo plus the client's geography. A place the list does
  not know would be stored as place-free, but the new client's geography
  filter (`geo_mismatch`) still runs on reuse, so it cannot reach their
  output.
- **Volumes.** The pool stores the country-level volumes the provider
  returned, before any merge. The pipeline's existing overview measurement
  (`dataforseo.search_volume` on the seed roots at the client's location)
  runs as today on reused seeds, so displayed volumes are measured the same
  way as in a fresh run. Modifier lookups (section 4) use the same location
  as that overview call and are stored in the pool only when that location is
  the country code.
- **Place-specific keywords for a reused service.** The pool holds none, so a
  new client with a reused service still needs its own place keywords. Today
  those arrive as incidental rows from the seed pulls; a reused seed no
  longer makes that pull. Replace it with a targeted lookup: build
  "<seed> <place>" candidates for the client's own service areas (capped at
  5 places, a setting), check them in one batched search-volume call, and
  keep those with a measured volume of at least 10. The results belong to
  that client only and are not stored in the pool. This is a cost-saving
  trade: a reused service may return a different set of place keywords than
  a full fetch would, aimed at the client's own places instead of whatever
  the provider surfaced. The run reports it.
- **What gets stored.**
  - A `keyword_pool_fetches` row per (seed concept, country location code,
    language): the seed text, the concept key, the industry slug, when it was
    fetched, how many rows came back, the provider errors, and a status of
    `complete`, `partial` or `empty`.
  - A `keyword_pool_entries` row per place-free keyword: the normalised
    keyword, the fetch it belongs to, match class, volume, cost per click,
    competition, difficulty, intent, funnel, trend, seed origin tag, and the
    fetched time. Unique on (fetch, keyword).
  - No client names, client ids or URLs are stored. The pool is public
    keyword data only.
- **Statuses are distinct.** `complete` seeds are reused. `partial` seeds
  (provider error, plan limit, budget stop) are not treated as complete and
  are re-fetched. `empty` means the provider answered with nothing and is
  reused for the 180 days instead of being fetched again. This keeps "fetched
  but returned nothing", "never fetched" and "dropped for budget" separate in
  the report, as the earlier seeding brief required.
- **Reporting.** The Search Demand result records, per service, whether it
  was reused or fetched, how many keywords, the age of the saved rows, and
  how many provider calls were avoided. Dollar figures appear only if the
  API meter already provides them.
- **Failure behaviour.** If the pool read or write fails, log it and fetch as
  today. A pool failure never fails a phase.
- **Concurrency.** Two clients fetching the same seed at the same time may
  both fetch. Writes are an upsert, so the result is one set of rows.

Left to the builder: module layout (suggested `app/services/keyword_pool/`
with store, location detection, reuse), the exact hook (wrap `expand_seed`
in `keyword_seeding.py` or sit above it in the seeding plan), the migration,
and indexes.

## 3. Hierarchical URL map (L1 to L4)

**Decided.**

- L1 to L4 are clear and hierarchical and cover all categories the keywords
  fall into.
- Not every row has all four levels. Depth depends on the keywords and on how
  deep the structure goes.
- Example: SEO is L1, Ecommerce SEO is L2, Shopify SEO is L3, and so on. The
  levels can come from the sub-services and sub-sub-services, or from
  sub-topics the keywords show.

**My calls.**

- A level exists only when the keywords back it. A keyword group becomes a
  deeper level only when it is a real sub-topic of the group above it. Rows
  with nothing beneath them stop at L1, L2 or L3.
- Two sources, in this order: the client's service catalog (services,
  sub-services, sub-sub-services) gives the skeleton; sub-topics the
  keywords show (a qualifier such as "ecommerce" or "shopify", an audience,
  a location) add levels under it.
- Modifiers do not create levels. "Best", "cost", "price", "company",
  "agency", "firm", "services", "vs", "how to" and similar belong to the
  node's secondary keywords. This keeps the tree from growing a branch per
  phrase.
- Maximum depth is 4. A row at L3 always has an L2; a row at L2 always has an
  L1. No gaps.
- Every in-scope keyword cluster is placed in exactly one node. A check
  fails the run's QA if any cluster is unplaced or placed twice.
- The existing taxonomy labelling and containment logic in `url_mapping.py`
  (the block that sets `l1_category` to `l4_attribution` from labels, and the
  URL-path based `_taxonomy_from_url`) is the starting point. The change
  makes the catalog the source of the skeleton and removes the dependence on
  URL path depth.

**More calls (settled after a review of the repo).**

- The catalog today goes service, then sub-service. Where a sub-sub-service
  exists it is used; otherwise L3 and L4 come from the sub-topics the
  keywords show. This is intended and matches the owner's example.
- The home page sits above the tree, as it does in the current sheet. The
  builder keeps today's home row handling.
- Where clusters go that fit no service: industry clusters attach to the home
  row. Informational clusters attach under the service they relate to, at the
  deepest level they fit. A cluster that matches no catalog service goes to a
  visible "needs review" list with a reason; it is never dropped and never
  placed twice.
- "Every cluster placed once" is a check in the Phase 6 validation. It flags
  a violation as a warning in the phase validation record. It does not block
  the phase.

Left to the builder: the thresholds for "a real sub-topic" (minimum number of
keywords, minimum measured volume), how existing pages' URLs are matched to
nodes, and how the report columns change, if at all.

## 4. Secondary keywords on every row

**Decided.**

- Every row in the URL map has secondary keywords.
- When a row has none from its cluster, use modifiers such as "best",
  "company", "agency", "firm" and similar.
- Check them against their real volume. Do not label them as suggested or
  with any other tag.

**My calls.**

- A row with fewer than 5 measured secondary keywords is topped up. The
  current cap of 10 stays.
- Candidates are the row's primary keyword with each modifier before or
  after it. Candidates containing a place word are not built.
- All candidates for the run go to DataForSEO in batched search-volume
  calls. A candidate is kept only if it has a measured volume of at least
  10, the existing floor. Kept candidates then pass the client's relevance
  and buyer-fit filters like any other keyword.
- Kept candidates look the same as every other secondary keyword.
- A row can still end up with no secondary keywords when no variant has
  measured searches. It is left empty, never filled with an unmeasured
  keyword. This is the one case where "every row" cannot hold, and it is a
  property of the data.
- **Which phase.** The primary keyword is chosen in Phase 6 (URL mapping), so
  the modifier lookup happens there, after primaries are set: one batched
  DataForSEO search-volume call for all candidates in the run, capped at 500
  candidates (a setting). The relevance and buyer-fit filters are pure
  functions of a relevance context; Phase 6 rebuilds that context from the
  client's stored services and geography, so no Phase 5 state is needed.
- Modifier lookups are place-free, so they are stored in the pool and reused
  (subject to the location rule in section 2).
- `_sheet_secondary_keywords` in `url_mapping.py` currently states that the
  column is not padded with such variants. That docstring and the tests that
  pin it change.

Left to the builder: the modifier list per page type, batching, and cost
guards (a per-run cap on lookups).

## How to know it works

- Pool: with a fake provider, two clients sharing 3 of 5 services produce
  provider calls only for the 2 missing services plus the place top-up. A
  place keyword from client A never appears for client B. A seed older than
  180 days is re-fetched. A `partial` seed is not reused as complete. An
  `empty` seed is reused. A pool outage leaves the run unchanged.
- Industry layer: for a "digital marketing agency" fixture, the home page row
  gets a broad, place-free primary keyword from the layer, rows are tagged
  `industry`, and no row has an invented metric.
- Hierarchy: the SEO, Ecommerce SEO, Shopify SEO example produces
  L1, L2, L3. A standalone service stops at L1. No L3 without L2. Every
  cluster is placed once.
- Secondary keywords: with a fake volume lookup, rows with measured clusters
  keep them, rows without are topped up with modifiers that have volume,
  modifiers without volume are dropped, and no output field or label marks
  the origin.
- A replay of the latest stored Search Demand run (22 October, about 4,000
  rows) shows the second pass reusing the first pass's rows.
- Existing keyword, URL mapping and report suites keep passing.

## Open items for the owner

None are blocking if the calls above are accepted. The ones most worth a
second look: the 180-day expiry, the 10% informational cap in the industry
layer, topping up to 5 secondary keywords, the targeted place lookup that
replaces incidental place rows for reused services, and the home page
falling back to a service head term when no industry term survives.
