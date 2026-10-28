# Keyword pool, industry keywords, hierarchical URL map, secondary keywords: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse place-free keyword research across clients, fetch industry-level keywords for the home page, build a clear L1 to L4 URL tree from the keywords, and give every URL-map row measured secondary keywords.

**Architecture:** A pool of cached provider fetches (new `shared_keyword_pool` package, two tables) sits behind the existing per-seed fetch (`expand_seed_dataforseo`), so each client's filters run after reuse. An industry layer adds extra seeds through the same fetch path. Phase 6 gets a new tree builder (`url_tree.py`) and a post-step that adds volume-checked modifier variants as secondary keywords.

**Tech Stack:** Python 3.12, SQLAlchemy async (SQLite in tests, Postgres in production), Alembic, pytest with `asyncio_mode = auto`, DataForSEO Labs, ruff.

**Spec:** `docs/superpowers/specs/2026-10-27-keyword-pool-industry-hierarchy-design.md`. Executors read it; it settles anything this plan leaves open.

## Global Constraints

- DataForSEO is the only keyword provider. The model may suggest terms and never supplies metrics.
- Saved keywords expire after **180 days** (`keyword_pool_ttl_days`).
- Only **place-free** keywords are stored in the pool. A place-specific keyword is never reused.
- Pool key: provider-cleaned normalised seed + country-level Labs location code + language code. Never the client.
- No client name, client id or URL is stored in the pool.
- Every reused keyword still goes through the new client's own filters (anchor filter, volume floor, relevance, buyer fit).
- Pool read or write failure never fails a phase: log it and fetch as today. Mock-provider mode (`use_mock_providers`) never touches the pool.
- New Alembic migration `009` for the pool tables (latest existing is `008`). Tables also appear via `Base.metadata.create_all`.
- Phase 5 time budget (about 5 minutes) holds: pool access is a few queries per run, not one per keyword; industry seeds are capped at 8 and join the existing bounded concurrent fetch.
- Modifier secondary keywords: measured volume of at least 10, no label or tag of any kind on kept ones. A row with no measured variants stays empty.
- Caps (settings): 5 places, 500 modifier candidates, 8 industry seeds, 10% informational share for industry seeds, top up to 5 secondary keywords.
- The working tree holds other people's uncommitted work in `service_seeding.py`, `keyword_seeding.py`, `search_demand.py`, `url_mapping.py`. Build on it, revert nothing.
- **No commits.** The owner commits when they ask, so plans' usual commit steps are replaced by running the area's tests.
- Test command (from `backend/`): `python -m pytest <path> -q -p no:cacheprovider`. Lint: `python -m ruff check <files> --select F401,F811,F821,F841,E711,E712,E722`.
- Python file edits on this machine: files may be CRLF; preserve the file's existing line endings.

## Review Focus

Failure modes the spec implies that no single task's main tests would otherwise cover. Each has a test in the task named.

1. The pool tables are missing or the DB errors during a run: the run behaves exactly as today (Task 4).
2. A client with no geography at all: no place top-up, place detection falls back to the word lists (Tasks 1 and 4).
3. A reused row served for a differently worded seed ("SEO services" vs "SEO") keeps a valid `seed` and `match_class` for the requesting seed, so seed clusters still build (Task 4).
4. Two clients fetch the same seed at the same moment: one fetch row results, no unique-constraint crash (Task 3).
5. Industry rows must not become a client service: `service_catalog`, prioritization and per-service summaries are unchanged (Task 6).
6. Modifier candidates: empty primary, primary that already contains the modifier, a place word in the primary, and more than 500 candidates (Task 9).

---

Naming note (ruling R6): an existing tracked module `backend/app/services/keyword_pool.py` (Phase 5 pool sizing) already owns the name `keyword_pool`, so this feature's package is `shared_keyword_pool`. Never move or edit the old module.

## File structure

| File | Responsibility |
|---|---|
| `backend/app/config.py` (modify) | New settings |
| `backend/app/services/shared_keyword_pool/__init__.py` (create) | Exports |
| `backend/app/services/shared_keyword_pool/location.py` (create) | `is_location_specific`, `client_places` |
| `backend/app/models/keyword_pool.py` (create) | `KeywordPoolFetch`, `KeywordPoolEntry` |
| `backend/alembic/versions/009_keyword_pool.py` (create) | Migration |
| `backend/app/services/shared_keyword_pool/store.py` (create) | Load, save, expiry, statuses |
| `backend/app/services/shared_keyword_pool/reuse.py` (create) | `PoolContext`, `pooled_seed_fetch`, place top-up, report |
| `backend/app/services/keyword_seeding.py` (modify) | Route the base fetch through the pool; `pool` and informational-floor parameters |
| `backend/app/agents/search_demand.py` (modify) | Build the `PoolContext`; industry plan; report keys |
| `backend/app/services/industry_keywords/` (create) | Model suggestions of industry terms |
| `backend/app/services/service_seeding.py` (modify) | `industry_seed_plan` |
| `backend/app/services/url_tree.py` (create) | L1 to L4 tree |
| `backend/app/services/url_mapping.py` (modify) | Home from industry layer, tree hook, secondary docstring |
| `backend/app/services/secondary_keywords.py` (create) | Modifier candidates and volume top-up |
| `backend/app/services/site_architecture.py` (modify) | Pass catalog; run the top-up |

---

### Task 1: Settings and place detection

**Files:**
- Modify: `backend/app/config.py` (after `subservice_expansion_max_seeds`)
- Create: `backend/app/services/shared_keyword_pool/__init__.py`, `backend/app/services/shared_keyword_pool/location.py`
- Test: `backend/tests/test_keyword_pool_location.py`

**Interfaces:**
- Produces settings: `keyword_pool_enabled: bool = True`, `keyword_pool_ttl_days: int = 180`, `keyword_pool_place_cap: int = 5`, `keyword_industry_max_seeds: int = 8`, `keyword_industry_informational_share: float = 0.10`, `keyword_secondary_target: int = 5`, `keyword_secondary_lookup_cap: int = 500`.
- Produces `is_location_specific(keyword: str, places: Iterable[str] = ()) -> bool` and `client_places(geo: Mapping[str, Any]) -> list[str]` in `shared_keyword_pool/location.py`.
- Produces `MODIFIER_WORDS: frozenset[str]` in `shared_keyword_pool/location.py`: `best top cost price pricing company companies agency agencies firm services service consultant provider cheap affordable near me vs how to what is`. Tasks 3 and 8 share it.

- [ ] **Step 1: Write the failing tests** in `test_keyword_pool_location.py`:
  - `test_place_words_make_a_keyword_location_specific`: `is_location_specific("dentist melbourne")`, `("dentist near me")`, `("dentist toronto")` are True; `("teeth whitening cost")`, `("invisalign vs braces")`, `("teeth whitening")` are False.
  - `test_the_clients_own_places_count`: `("dentist in bondi", places=["bondi"])` is True and False without `places`; `("seo australia", places=["australia"])` is True.
  - `test_client_places_come_from_the_geography`: `client_places({"city": "Perth", "location_name": "Western Australia", "geographic_focus": "Perth, Fremantle"}) == ["perth", "western australia", "fremantle"]`; `client_places({}) == []`.
  - `test_settings_defaults`: `get_settings()` carries the defaults above.
  - `test_modifier_words_include_the_owners_examples`: `best`, `company`, `agency`, `firm` are in `MODIFIER_WORDS`.
- [ ] **Step 2: Run to verify failure.** Expect `ModuleNotFoundError` / `AttributeError`.
- [ ] **Step 3: Add the seven settings** with a one-line comment each, in `config.py`.
- [ ] **Step 4: Implement `is_location_specific` and `client_places`** in `location.py`. Whole-word or whole-phrase matching on the normalised keyword. Place words: import the existing frozensets `_AU_CITIES` and `_FOREIGN_CITIES` (`keyword_service_contract.py`) and `_LOCAL_WORDS` (`keyword_exclusions.py`) rather than copying them, plus a small module constant of Australian states and country names. "near me" and "nearby" always count. `client_places` splits `geographic_focus` on commas, adds `city` and `location_name`, lowercases, de-duplicates in first-seen order.
- [ ] **Step 5: Run the tests; expect PASS.** Run ruff on the new files.

### Task 2: Pool tables and migration 009

**Files:**
- Create: `backend/app/models/keyword_pool.py`, `backend/alembic/versions/009_keyword_pool.py`
- Modify: `backend/app/models/__init__.py` (import both models so `create_all` sees them)
- Test: `backend/tests/test_keyword_pool_models.py`

**Interfaces:**
- Produces `KeywordPoolFetch` (table `keyword_pool_fetches`): `id` GUID pk; `kind` Text (`seed` or `lookup`); `seed_key` Text; `seed_text` Text; `concept_key` Text null; `industry_key` Text null; `location_code` Integer; `language_code` Text; `status` Text (`complete`, `partial`, `empty`); `row_count` Integer; `min_volume` Integer; `row_limit` Integer; `errors` JSON; `fetched_at` DateTime(tz). Unique on (`kind`, `seed_key`, `location_code`, `language_code`).
- Produces `KeywordPoolEntry` (table `keyword_pool_entries`): `id` GUID pk; `fetch_id` GUID FK to the fetch, `ondelete="CASCADE"`, indexed; `keyword_norm` Text, indexed; `keyword` Text; `volume` Integer null; `row` JSON (the full provider row: cpc, competition, difficulty, intent, funnel, trend, match class, seed origin). Unique on (`fetch_id`, `keyword_norm`).
- Use `GUID` and `JSONType` from `app.models.types`, following `KeywordRejection` in `models/governance.py`. No client id column.

- [ ] **Step 1: Write failing tests** using the `db_session` fixture: `test_tables_exist_after_create_all`; `test_a_fetch_is_unique_per_kind_seed_location_language` (second insert raises `IntegrityError`); `test_an_entry_is_unique_per_fetch_and_keyword`; `test_entries_are_deleted_with_their_fetch` (enable SQLite foreign keys in the test or delete through the ORM relationship with cascade); `test_migration_009_follows_008` (read the revision module: `revision == "009"`, `down_revision == "008"`).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement the models and the migration** (migration mirrors `007_keyword_rejections.py`: `op.create_table`, indexes, `downgrade` drops both).
- [ ] **Step 4: Run the tests; expect PASS.** Also run `tests/test_hot_path_indexes.py`.

### Task 3: Pool store

**Files:**
- Create: `backend/app/services/shared_keyword_pool/store.py`
- Test: `backend/tests/test_keyword_pool_store.py`

**Interfaces:**
- Consumes: Task 1 `is_location_specific`; Task 2 models; `clean_provider_seed` from `keyword_seeding`; `AsyncSessionLocal` from `app.db`.
- Produces: `seed_key(seed: str) -> str`; `concept_text(seed: str) -> str | None`; `@dataclass(frozen=True) PoolHit(rows: list[dict], status: str, fetched_at: datetime, age_days: int)`; `async load_seed(key: str, *, location_code: int, language_code: str, min_volume: int, limit: int, kind: str = "seed", now: datetime | None = None) -> PoolHit | None`; `async save_seed(key: str, *, seed_text: str, location_code: int, language_code: str, min_volume: int, limit: int, rows: list[dict], errors: list[str], places: Sequence[str] = (), kind: str = "seed", industry_key: str | None = None, concept_key: str | None = None, now: datetime | None = None) -> str` returning the saved status.

Semantics the tests pin:
- Status: any entry in `errors` gives `partial`; no errors and no rows gives `empty`; otherwise `complete`.
- `load_seed` returns a hit only for `complete` and `empty`, only when `fetched_at` is within `keyword_pool_ttl_days`, only when the stored `row_limit` is at least the requested `limit` and the stored `min_volume` is at most the requested `min_volume`. Returned rows keep those with `volume is None or volume >= min_volume`.
- Import rule: `shared_keyword_pool` modules must not import `keyword_seeding` at module level (`keyword_seeding` imports `shared_keyword_pool.reuse` in Task 5, which would be a cycle). Import `clean_provider_seed` and `classify_expansion` lazily inside the functions that use them.
- Same-service matching: `concept_text(seed)` is the sorted, comma-joined `concept_key(seed)` from `service_taxonomy.py`, or `None` when the seed is not classified as a service. `save_seed` stores it in `concept_key`. `load_seed` tries the exact `seed_key` first, then a fetch with the same `concept_key` (same kind, location, language) only when the requested seed contains no place word and its tokens outside `MODIFIER_WORDS` equal the stored seed's tokens outside `MODIFIER_WORDS`. So "seo services" hits a stored "seo", while "seo for dentists" does not.
- `save_seed` drops place-specific rows before storing and is an upsert: saving the same (kind, key, location, language) again replaces its entries, leaving one fetch row.

- [ ] **Step 1: Write failing tests** (`db_session` fixture; pass `now=` to control age): `test_a_complete_fetch_is_reused`; `test_a_fetch_older_than_180_days_is_not_reused` (179 days hit, 181 miss); `test_a_partial_fetch_is_saved_but_never_reused`; `test_an_empty_fetch_is_reused_as_empty` (hit with `rows == []`); `test_place_specific_rows_are_not_stored` (rows "teeth whitening", "teeth whitening perth", "dentist near me" give only the first back); `test_a_smaller_earlier_pull_does_not_satisfy_a_larger_request` (saved limit 40, requested 60 gives None); `test_saving_twice_leaves_one_fetch` ; `test_two_concurrent_saves_of_the_same_seed_do_not_crash` (`asyncio.gather` of two `save_seed` calls, then one fetch row); `test_seed_key_ignores_case_spacing_and_provider_noise` (`seed_key("  SEO ")` equals `seed_key("seo")`); `test_a_differently_worded_seed_for_the_same_service_hits` (saved "seo", `load_seed` for "seo services" hits); `test_a_narrower_seed_does_not_hit_a_broader_one` ("seo for dentists" and "seo melbourne" miss a stored "seo").
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement the store.** Each function opens its own short `AsyncSessionLocal()` session and commits. For the concurrent upsert, catch `IntegrityError` on insert, roll back and retry as an update once.
- [ ] **Step 4: Run the tests; expect PASS.**

### Task 4: Reuse wrapper and place top-up

**Files:**
- Create: `backend/app/services/shared_keyword_pool/reuse.py`
- Test: `backend/tests/test_keyword_pool_reuse.py`

**Interfaces:**
- Consumes: Task 3 store; Task 1 location helpers; `dataforseo.search_volume(keywords, *, location_code, language_code)`.
- Produces: `@dataclass PoolContext(places: tuple[str, ...] = (), enabled: bool = True, industry_key: str | None = None, report: PoolReport = ...)`; `PoolReport` with `record(service: str | None, seed: str, outcome: str, rows: int, age_days: int | None = None, calls_avoided: int = 0)` and `as_dict() -> dict` giving a per-service summary (counts of each outcome, rows, calls avoided); `async pooled_seed_fetch(seed: str, *, pool: PoolContext, service: str | None, fetch: Callable[..., Awaitable[tuple[list[dict], list[str]]]], location_code: int, language_code: str | None, min_volume: int, limit: int, classes: tuple[str, ...]) -> tuple[list[dict], list[str]]`.
- Outcomes recorded: `reused`, `reused_empty`, `fetched` (and saved), `fetched_not_saved` (partial), `pool_error` (any pool exception: fetched as today).

Behaviour: if `pool.enabled` is false, or `language_code` is missing, call `fetch` unchanged. On a hit, return the pool rows with `seed` set to the requesting seed and `match_class` recomputed with `classify_expansion(keyword, seed)`, then add the place top-up rows. On a miss, call `fetch`, save with `places=pool.places`, return the fetched rows unchanged (the client keeps its own place rows). Place top-up: for up to `keyword_pool_place_cap` of `pool.places`, build `"<seed> <place>"` candidates, one `search_volume` call at `location_code`, keep rows with volume of at least 10, tag `place_topup=True`; with no places, do nothing.

- [ ] **Step 1: Write failing tests** with a fake `fetch` that counts calls and a faked `dataforseo.search_volume` (monkeypatch):
  - `test_second_client_reuses_shared_seeds_and_fetches_only_new_ones`: client A seeds `["seo", "ppc", "cro"]`, client B `["seo", "ppc", "email marketing"]`; the fake fetch is called 3 times for A and once (email marketing) for B.
  - `test_a_place_keyword_from_client_a_never_reaches_client_b`: A's fetch returns "seo melbourne"; B (places `["perth"]`) gets no "seo melbourne" from the pool.
  - `test_reused_rows_carry_the_requesting_seed`: A fetched "seo"; B asks for "seo services" and gets rows with `seed == "seo services"`, a valid `match_class`, and `seed_clusters` still build through `build_seed_clusters`.
  - `test_the_place_topup_looks_up_seed_plus_place_and_keeps_only_measured`: candidates `["seo perth", "seo fremantle"]`, volume only for the first; result has one `place_topup` row; it is not saved to the pool.
  - `test_no_places_means_no_topup_call`.
  - `test_a_pool_failure_falls_back_to_a_normal_fetch` (monkeypatch `load_seed` to raise): rows equal the fetch result; outcome `pool_error`.
  - `test_a_missing_table_falls_back_to_a_normal_fetch` (drop the tables in the test DB first).
  - `test_disabled_pool_never_touches_the_store`.
  - `test_the_report_counts_reused_and_fetched_per_service`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** `reuse.py`. Wrap every pool call in `try/except Exception`, log `keyword_pool_error`, and fall back.
- [ ] **Step 4: Run the tests; expect PASS.**

### Task 5: Wire the pool into seeding and the Search Demand report

**Files:**
- Modify: `backend/app/services/keyword_seeding.py` (`expand_seed`, `run_multi_mode_seeding`), `backend/app/agents/search_demand.py`
- Test: `backend/tests/test_keyword_pool_seeding.py`

**Interfaces:**
- Consumes: Task 4 `PoolContext`, `pooled_seed_fetch`.
- Produces: `expand_seed(..., pool: PoolContext | None = None)` and `run_multi_mode_seeding(..., pool: PoolContext | None = None)`. In `expand_seed`, both `expand_seed_dataforseo(...)` calls (the `keyword_extra_idea_pulls` branch and the plain branch) go through one local helper that calls `pooled_seed_fetch` when `pool` is set and `expand_seed_dataforseo` directly otherwise. `run_multi_mode_seeding` passes `pool` and the service name to `expand_seed`. Variant pulls (`_variant_pull`) and the informational top-up stay uncached: they depend on the anchor filter and the client's variants.
- In `search_demand.py`: build `PoolContext(places=client_places(geo), enabled=settings.keyword_pool_enabled)` next to the `run_service_scoped_seeding(...)` call and pass it as `pool=`. After the call, set `seeding["keyword_pool"] = pool.report.as_dict()` and add one operator notice such as "Reused saved keywords for 3 of 5 services; fetched 2 (N provider calls avoided)". No dollar figures unless the meter already provides them.

- [ ] **Step 1: Write failing tests**: `test_expand_seed_uses_the_pool_when_given_one` (patch `expand_seed_dataforseo`; second call with a fresh `PoolContext` and the same seed fetches nothing); `test_expand_seed_is_unchanged_without_a_pool` (patched fetch called, no store call); `test_mock_provider_mode_never_touches_the_pool` (default test settings use mock providers; assert the store is not called); `test_run_multi_mode_seeding_reports_reuse` (two seeds, one pre-saved); `test_search_demand_summary_carries_keyword_pool` using the existing fixtures in `tests/test_service_scoped_seeding_flow.py` as the pattern.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement the wiring** as described.
- [ ] **Step 4: Run** the new tests plus `tests/test_keyword_seeding.py`, `tests/test_service_scoped_seeding.py`, `tests/test_service_scoped_seeding_flow.py`, `tests/test_keyword_funnel_coverage.py`; expect PASS.

### Task 6: Industry keyword layer

**Files:**
- Create: `backend/app/services/industry_keywords/__init__.py`, `generator.py`, `prompts.py`
- Modify: `backend/app/services/service_seeding.py` (`industry_seed_plan`), `backend/app/services/keyword_seeding.py` (informational floor), `backend/app/agents/search_demand.py`
- Test: `backend/tests/test_industry_keywords.py`

**Interfaces:**
- Produces `IndustryKeywordResolver(industry: str | None, market: str | None, services: list[str])` with `async resolve() -> IndustryTerms`, where `IndustryTerms` has `terms: list[str]` (at most `keyword_industry_max_seeds`), `source: str` (`model` or `fallback`), `industry_key: str | None`. Model for the pattern: `SeedSynonymResolver` in `seed_synonyms/generator.py` (per-run cache, fallback when the model is unavailable, mock-safe). The model returns category terms only, no metrics. Fallback: the industry text plus "<industry> services".
- Produces `industry_seed_plan(industry: str, terms: list[str]) -> ServiceSeedPlan` in `service_seeding.py` with `service=industry`, `service_id="industry"`, every seed in `seed_targets` carrying `seed_origin="industry"`, added to `EXTRA_ORIGINS` so they ride on top of the seed budget.
- Produces `expand_seed(..., informational_floor: float = INFORMATIONAL_SHARE_FLOOR)`; `run_multi_mode_seeding` passes `keyword_industry_informational_share` for seeds whose `seed_targets` entry has `seed_origin == "industry"`.
- Industry terms are themselves pooled (spec section 1): before asking the model, the resolver calls `load_seed(industry_key, kind="industry_terms", ...)` (location and language of the run); a fresh hit returns its entries as the terms (`source="pool"`). After a model answer it calls `save_seed(industry_key, kind="industry_terms", rows=[{"keyword": term} ...], errors=[], ...)`. Fallback terms are never saved. `IndustryTerms.source` is `model`, `fallback` or `pool`.
- `industry_key`: the normalised industry text (lowercase, single spaces), using `industry_slug()` from `keyword_exclusions` when it returns one. Pass it to `PoolContext.industry_key`.
- Gate: industry-origin rows are judged against the client's whole service list, and a row also passes when it names the industry. Find the smallest change in `keyword_service_contract.judge_keyword` or its caller; every rule's strictness stays the same.
- In `search_demand.py`: resolve industry terms next to the synonym resolver, append `industry_seed_plan(...)` to `service_plans` when terms exist, and mark its run `kind: "industry"` in `service_runs`.

- [ ] **Step 1: Write failing tests**: `test_resolver_caps_terms_at_the_setting`; `test_resolver_falls_back_without_a_model` (mock LLM gives the fallback terms, `source == "fallback"`); `test_a_second_client_in_the_same_industry_reuses_the_terms` (model stub called once across two resolver runs for "dental clinic", second `source == "pool"`); `test_fallback_terms_are_not_saved`; `test_industry_plan_seeds_are_extra_and_tagged` (`plan.is_extra(seed)` True, `seed_origin == "industry"`); `test_industry_seeds_use_the_ten_percent_informational_cap` (patch `expand_seed`, assert the floor argument is 0.10 for an industry seed and 0.15 for a service seed); `test_an_industry_term_survives_the_gate_for_an_agency_client` ("digital marketing agency" for a client whose services are SEO and PPC Management); `test_industry_rows_do_not_become_a_service` (`service_catalog`, prioritization and the per-service summary list carry no "industry" service; the run is marked `kind == "industry"`).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the resolver, prompts, plan factory, floor parameter and wiring.
- [ ] **Step 4: Run** the new tests plus `tests/test_seed_synonyms.py`, `tests/test_subservice_expansion.py`, `tests/test_service_contract_relevance.py`, `tests/test_keyword_funnel_coverage.py`; expect PASS.

### Task 7: Home page takes the industry head term

**Files:**
- Modify: `backend/app/services/url_mapping.py` (`build_final_url_map`, `map_cluster_to_url` area)
- Test: `backend/tests/test_url_map_home_industry.py`

**Interfaces:**
- Produces `is_industry_cluster(cluster: dict) -> bool` (a majority of `cluster["keywords"]` rows have `seed_origin == "industry"`). Industry clusters are merged into one home entry mapped to `/`: primary keyword is the highest-volume in-scope keyword among them, the other industry keywords become its secondary keywords (up to 10). `build_final_url_map` result gains `home_page: {"keyword": str | None, "source": "industry" | "service_fallback"}`. When no industry cluster survives, the home row keeps today's handling and `source` is `service_fallback`.

- The home entry carries `is_home: True` (Task 8 reads it). `seed_origin` must survive from seeded rows into `cluster_report["clusters"][i]["keywords"]` through the real pipeline path (`keyword_clustering`, `memory_packs`); verify this and make the smallest fix if a step drops it, and say so in the report.

- [ ] **Step 1: Write failing tests**: `test_industry_clusters_map_to_the_home_page`; `test_seed_origin_survives_into_cluster_keywords`; `test_home_primary_is_the_highest_volume_industry_keyword_and_others_are_secondary`; `test_no_industry_cluster_falls_back_and_says_so`; `test_service_clusters_are_unaffected`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** inside `build_final_url_map`, before supporting rows resolve.
- [ ] **Step 4: Run** the new tests plus `tests/test_url_mapping.py`, `tests/test_url_mapping_topic_decisions.py`, `tests/test_url_mapping_fixes.py`; expect PASS.

### Task 8: Hierarchical tree (L1 to L4)

**Files:**
- Create: `backend/app/services/url_tree.py`
- Modify: `backend/app/services/url_mapping.py` (`build_final_url_map`), `backend/app/services/site_architecture.py`
- Test: `backend/tests/test_url_tree.py`

**Interfaces:**
- Produces `apply_url_tree(entries: list[dict], *, service_catalog: list[dict], min_subtopic_keywords: int = 2) -> dict` which sets `l1_category`, `l2_subcategory`, `l3_subsubcategory`, `l4_attribution` and `level` on each entry in place and returns `{"placed": int, "needs_review": [{"cluster_id", "primary_keyword", "reason"}], "violations": [str]}`.
- `build_final_url_map(..., service_catalog: list[dict] | None = None)`: after `apply_workbook_taxonomy` (line about 2156) call `apply_url_tree` when a catalog is given; put its report in the result under `"url_tree"` and add `violations` to the phase validation warnings. In `site_architecture.py`, pass `demand.get("service_catalog") or prioritization.get("service_catalog")`.

Algorithm (the part the signature does not determine):
1. Skeleton: each catalog service is an L1 node; its sub-services L2; sub-sub-services L3, up to level 4. Match an entry to the deepest node whose label tokens are all inside the entry's primary keyword (reuse `same_concept` and `_tokens_fit_inside`).
2. Remainder tokens, after removing the node's tokens, stop words and `MODIFIER_WORDS` (from `shared_keyword_pool/location.py`), are the qualifier. A non-empty qualifier makes a child node one level deeper (maximum level 4) once at least `min_subtopic_keywords` clusters in the node share it or the cluster has measured volume. An empty qualifier places the entry at the node itself.
3. Industry clusters attach to the home row, recognised by `entry.get("is_home")` set in Task 7 (no levels). Informational clusters attach under the service they relate to, at the deepest level they fit. A cluster matching no node goes to `needs_review` with a reason.
4. Invariants checked into `violations`: no L3 without L2, no L2 without L1, every in-scope cluster placed exactly once.

- [ ] **Step 1: Write failing tests**: `test_seo_ecommerce_seo_shopify_seo_builds_three_levels` (catalog SEO with sub-service "Ecommerce SEO"; clusters "seo", "ecommerce seo", "shopify seo" give L1 SEO, L2 Ecommerce SEO, L3 Shopify SEO); `test_a_standalone_service_stops_at_l1`; `test_modifiers_do_not_create_levels` ("best seo agency" stays on the SEO node); `test_depth_is_capped_at_four`; `test_no_l3_without_l2`; `test_every_cluster_is_placed_exactly_once`; `test_unmatched_cluster_goes_to_needs_review_with_reason`; `test_informational_cluster_attaches_under_its_service`; `test_empty_catalog_leaves_existing_levels_untouched`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** `url_tree.py` and the hook.
- [ ] **Step 4: Run** the new tests plus `tests/test_url_mapping*.py`, `tests/test_report_pdf.py`, `tests/test_report_docx.py`, `tests/test_site_architecture*.py`; expect PASS.

### Task 9: Secondary keywords on every row

**Files:**
- Create: `backend/app/services/secondary_keywords.py`
- Modify: `backend/app/services/site_architecture.py`, `backend/app/services/url_mapping.py` (docstring and comment of `_sheet_secondary_keywords`)
- Test: `backend/tests/test_secondary_keywords.py`

**Interfaces:**
- Consumes: Task 3 store (`kind="lookup"`), Task 1 `is_location_specific`, `dataforseo.search_volume`, `filter_relevant_keywords` with a `RelevanceContext` rebuilt via `build_relevance_context` from the client's stored services and geography.
- Produces `SECONDARY_MODIFIERS: tuple[str, ...] = ("best", "top", "company", "agency", "firm", "services", "consultant", "provider", "cheap", "affordable")`; `modifier_candidates(primary: str, existing: Iterable[str], modifiers: Iterable[str] = SECONDARY_MODIFIERS) -> list[str]`; `async top_up_secondary_keywords(entries: list[dict], *, location_code: int, language_code: str, country_location_code: int, relevance_context: Any | None, target: int | None = None, cap: int | None = None) -> dict` which fills `secondary_keywords_sheet` and `secondary_keywords` in place for rows with fewer than `keyword_secondary_target` and returns a report `{"rows_topped_up", "candidates", "kept", "empty_rows"}`.
- In `site_architecture.py`, call it right after `build_final_url_map` returns. Update the `_sheet_secondary_keywords` docstring: it no longer says the column is never padded.

Behaviour: candidates are the primary with each modifier before and after it; skip a candidate containing a place word, a candidate equal to the primary, one already among the secondary keywords, and a modifier the primary already contains. One batched `search_volume` call, at most `keyword_secondary_lookup_cap` candidates (rows with the fewest secondary keywords first). Keep volume of at least 10, then the relevance and buyer-fit gate. Kept candidates are appended as plain strings: no flag, no label, no field recording their origin. Store lookups in the pool only when `location_code == country_location_code`; read cached lookups first.

- [ ] **Step 1: Write failing tests** (fake `search_volume`): `test_rows_below_target_are_topped_up_with_measured_modifier_variants`; `test_variants_without_measured_volume_are_dropped`; `test_kept_variants_carry_no_label_or_extra_field` (the secondary list is plain strings; the entry has no new origin key); `test_a_row_with_no_measured_variant_stays_empty`; `test_rows_already_at_target_are_untouched`; `test_candidates_skip_places_empty_primary_and_modifier_already_in_primary`; `test_lookup_cap_limits_candidates_to_500`; `test_cached_lookups_are_not_fetched_again`; `test_variants_that_fail_buyer_fit_are_dropped`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** `secondary_keywords.py` and the hook.
- [ ] **Step 4: Run** the new tests plus `tests/test_url_mapping*.py` and `tests/test_report_pdf.py`; fix any test that pinned the old "never padded" docstring behaviour by updating it to the new rule.

### Task 10: End-to-end checks, memory and docs

**Files:**
- Test: `backend/tests/test_keyword_pool_end_to_end.py`
- Modify: memory notes only (no repo docs beyond the spec and plan)

- [ ] **Step 1: Write the end-to-end test** with a fake provider: two dental-like clients sharing 3 of 5 services (A seeds first). Assert provider fetch calls for B equal the 2 missing services plus one place lookup; no place keyword from A in B's output; an industry-layer home keyword reaches the URL map's home row; the SEO / Ecommerce SEO / Shopify SEO example builds a three-level tree; every row has secondary keywords or is explicitly empty with no measured variants.
- [ ] **Step 2: Replay the stored 22 October Search Demand rows** (`scratchpad/card_rows.json`, kept outside the repo) through `save_seed` and a second `pooled_seed_fetch` pass in a throwaway script; confirm the second pass reuses the first pass's place-free rows and reports the avoided calls. Do not add this script to the repo.
- [ ] **Step 3: Run the whole backend suite** in the background: `python -m pytest tests -q -p no:cacheprovider --ignore=tests/test_wordpress_publishing.py`. Expect only failures that already existed before this work (compare with the list in memory: none known after the 27 October cleanup). Run `ruff check` with the CI selection. Run `npm run build` in `frontend/` only if a frontend file changed (none planned).
- [ ] **Step 4: Update memory** (`keyword-pool-industry-hierarchy-brief.md`, `current-branch-state.md`, `work-log.md`) and report to the owner what was built, what was verified and what was not, with the 180-day expiry stated.
