"""Keyword fetch covers every search intent, keeps keywords without measured volume
(flagged), and labels every row with intent and funnel (2026-10-13)."""

import asyncio

import pytest

from app.integrations.dataforseo import _labs_query_fields
from app.services import keyword_seeding
from app.services.keyword_seeding import (
    INFORMATIONAL_SHARE_FLOOR,
    expand_seed,
    keep_for_volume,
    run_multi_mode_seeding,
    volume_coverage,
    volume_status,
)
from app.services.memory_packs import slim_search_demand_memory
from app.services.query_variants import (
    QueryVariantResolver,
    anchor_term,
    clean_variants,
)
from app.services.query_variants import generator as qv
from app.services.service_seeding import ServiceSeedPlan, run_service_scoped_seeding

# ------------------------------------------------------------------ variants


def test_variants_are_cleaned_to_on_topic_research_queries():
    out = clean_variants(
        "physiotherapy",
        [
            "How does physiotherapy work?",
            "what is physiotherapy",
            "physiotherapy near me",  # buying phrase
            "physiotherapy cost",  # buying phrase
            "how to stretch your back",  # not about the seed
            "physiotherapy",  # the seed itself
            "physiotherapy benefits for older adults with chronic knee pain",  # > 8 words
            "physiotherapy benefits",
            "physiotherapy vs chiropractic",
            "what to expect at physiotherapy",  # a fifth: over the cap
        ],
    )
    assert out == [
        "how does physiotherapy work",
        "what is physiotherapy",
        "physiotherapy benefits",
        "physiotherapy vs chiropractic",
    ]


def test_the_anchor_word_keeps_variant_keywords_on_topic():
    assert anchor_term("physiotherapy") == "physiotherapy"
    assert anchor_term("seo melbourne", "SEO") == "seo"  # shared with the service wins
    assert anchor_term("mortgage broker") == "mortgage"
    assert anchor_term("best agency") is None  # nothing but generic words


class _Cache:
    def __init__(self):
        self.store: dict = {}

    async def get_many(self, keys):
        return {k: self.store[k] for k in keys if k in self.store}

    async def set_many(self, values, ttl):
        self.store.update(values)


@pytest.fixture
def cache(monkeypatch):
    c = _Cache()
    monkeypatch.setattr(qv, "cache_get_many", c.get_many)
    monkeypatch.setattr(qv, "cache_set_many", c.set_many)
    return c


def _model(calls):
    async def ask(seeds, **kw):
        calls.append(list(seeds))
        await asyncio.sleep(0.01)
        return {"variants": {s: [f"what is {s}", f"how does {s} work", f"{s} benefits"] for s in seeds}}

    return ask


async def test_one_call_per_batch_then_memory_then_redis(cache):
    calls: list = []
    resolver = QueryVariantResolver(industry="health", market="Australia", ask=_model(calls))
    seeds = [f"seed {i}" for i in range(30)]
    first = await resolver.variants_for(seeds)
    assert len(first) == 30 and first["seed 0"] == ["what is seed 0", "how does seed 0 work", "seed 0 benefits"]
    assert sorted(len(c) for c in calls) == [5, 25], "batches of 25"
    await resolver.variants_for(seeds[:3])
    assert len(calls) == 2, "this run's memory answers repeats"
    assert resolver.cache_hits == 0, "...without even reading Redis"
    other_run = QueryVariantResolver(industry="health", market="Australia", ask=_model(calls))
    again = await other_run.variants_for(seeds[:3])
    assert again == {s: first[s] for s in seeds[:3]}
    assert len(calls) == 2 and other_run.cache_hits == 3, "the next run reads Redis"


async def test_concurrent_requests_share_one_call(cache):
    calls: list = []
    resolver = QueryVariantResolver(ask=_model(calls))
    a, b = await asyncio.gather(resolver.variants_for(["physio"]), resolver.variants_for(["physio"]))
    assert a == b and len(calls) == 1


async def test_a_failing_model_leaves_seeds_without_variants(cache):
    async def boom(seeds, **kw):
        raise RuntimeError("model down")

    resolver = QueryVariantResolver(ask=boom)
    assert await resolver.variants_for(["physio"]) == {}
    assert resolver.failures


# ------------------------------------------------------------------ filters


def test_labs_filters_keep_null_volume_and_and_extra_conditions():
    plain = _labs_query_fields(limit=50, min_volume=10)
    assert plain["filters"] == ["keyword_info.search_volume", ">=", 10]
    combined = _labs_query_fields(
        limit=50,
        min_volume=10,
        keep_null_volume=True,
        extra_filters=[["search_intent_info.main_intent", "=", "informational"], ["keyword", "like", "%physio%"]],
    )
    assert combined["filters"] == [
        [["keyword_info.search_volume", ">=", 10], "or", ["keyword_info.search_volume", "=", None]],
        "and",
        ["search_intent_info.main_intent", "=", "informational"],
        "and",
        ["keyword", "like", "%physio%"],
    ]


# ------------------------------------------------------------------ fetch


def _intent_equals(filters: list) -> str | None:
    for item in filters:
        if isinstance(item, list) and len(item) >= 3 and item[1] == "=" and str(item[0]).endswith("main_intent"):
            return str(item[2])
    return None


def _is_topup(call: dict) -> bool:
    """The informational top-up also restricts the keyword to the seed's subject.

    The main fetch asks for an informational page too, with only the intent
    filter, so that page is not this top-up.
    """
    filters = call.get("extra_filters") or []
    on_subject = any(
        isinstance(item, list) and len(item) >= 2 and item[0] == "keyword" and item[1] == "like"
        for item in filters
    )
    return _intent_equals(filters) == "informational" and on_subject


class FakeDFS:
    """Records every DataForSEO call; answers per endpoint."""

    def __init__(self, *, commercial_only: bool = True):
        self.commercial_only = commercial_only
        self.calls: list[dict] = []

    async def suggestions(self, seed, **kw):
        self.calls.append({"endpoint": "suggestions", "seed": seed, **kw})
        rows = [
            {"keyword": f"{seed} services", "volume": 500, "intent": "commercial"},
            {"keyword": f"hire {seed}", "volume": 90, "intent": "transactional"},
            {"keyword": f"{seed} sydney", "volume": None, "intent": "commercial"},  # no volume
            {"keyword": f"{seed} perth", "volume": 0, "intent": "commercial"},  # zero is not measured demand
            {"keyword": f"{seed} hobart", "volume": 6, "intent": "commercial"},  # measured, below the floor
        ]
        if not self.commercial_only:
            rows += [
                {"keyword": f"what is {seed} about", "volume": 300, "intent": "informational"},
                {"keyword": f"{seed} exercises for back pain", "volume": 200, "intent": "informational"},
                {"keyword": f"does {seed} help arthritis", "volume": 120, "intent": "informational"},
            ]
        return rows, []

    async def related(self, seed, **kw):
        self.calls.append({"endpoint": "related", "seed": seed, **kw})
        return [{"keyword": f"{seed} agency", "volume": 200, "intent": "commercial"}], []

    async def ideas(self, seed, **kw):
        self.calls.append({"endpoint": "ideas", "seed": seed, **kw})
        filters = kw.get("extra_filters") or []
        if _is_topup({"extra_filters": filters}):
            return [
                {"keyword": f"{seed} exercises at home", "volume": 400, "intent": "informational"},
                {"keyword": f"is {seed} worth it", "volume": 150, "intent": "informational"},
            ], []
        if kw.get("keywords") and kw["keywords"] != [seed]:
            return [{"keyword": f"how long does {seed} take", "volume": 250, "intent": "informational"}], []
        if _intent_equals(filters) == "informational":
            if self.commercial_only:
                return [], []
            return [
                {"keyword": f"what is {seed} about", "volume": 300, "intent": "informational"},
                {"keyword": f"{seed} exercises for back pain", "volume": 200, "intent": "informational"},
            ], []
        return [{"keyword": f"{seed} clinic", "volume": 800, "intent": "commercial"}], []

    async def volume(self, *a, **kw):
        return [], []


@pytest.fixture
def dfs(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "use_mock_providers", False)
    monkeypatch.setattr(get_settings(), "dataforseo_language_code", "en", raising=False)
    monkeypatch.setattr(get_settings(), "keyword_extra_idea_pulls", True, raising=False)
    fake = FakeDFS()
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_suggestions", fake.suggestions)
    monkeypatch.setattr(keyword_seeding.dataforseo, "related_keywords", fake.related)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", fake.ideas)
    monkeypatch.setattr(keyword_seeding.dataforseo, "search_volume", fake.volume)
    return fake


async def _variants():
    return ["what is physiotherapy", "how does physiotherapy work", "physiotherapy benefits"]


async def test_variants_widen_the_pull_and_their_keywords_are_marked(dfs):
    report: dict = {}
    rows, _ = await expand_seed(
        "physiotherapy", min_volume=10, location_code=2036, language_code="en",
        variants=_variants, service="Physiotherapy", report=report,
    )
    variant_calls = [c for c in dfs.calls if c["endpoint"] == "ideas" and c.get("keywords") == list(await _variants())]
    assert variant_calls, "the variants went to keyword ideas"
    first = variant_calls[0]
    assert ["keyword", "like", "%physiotherapy%"] in first["extra_filters"], "kept on the seed's topic"
    assert ["search_intent_info.main_intent", "in", ["informational", "commercial"]] in first["extra_filters"]
    assert first["keep_null_volume"] is True
    by_kw = {r["keyword"]: r for r in rows}
    variant_row = by_kw["how long does physiotherapy take"]
    assert variant_row["query_variant"] is True
    assert variant_row["variant_queries"] == list(await _variants())
    assert variant_row["funnel"] and variant_row["intent"], "labelled like every other row"
    assert report["variant_queries"] == list(await _variants())


async def test_low_informational_share_triggers_an_informational_topup(dfs):
    report: dict = {}
    rows, _ = await expand_seed(
        "physiotherapy", min_volume=10, location_code=2036, language_code="en",
        variants=_variants, report=report,
    )
    topups = [c for c in dfs.calls if _is_topup(c)]
    assert len(topups) == 1
    assert topups[0]["keywords"] == list(await _variants()), "the top-up uses the variant queries"
    assert ["keyword", "like", "%physiotherapy%"] in topups[0]["extra_filters"]
    assert report["informational_share_before"] < INFORMATIONAL_SHARE_FLOOR
    assert report["topped_up"] == 2
    assert report["funnel_after"]["TOFU"] > report["funnel_before"]["TOFU"]
    topped = [r for r in rows if r.get("informational_topup")]
    assert {r["keyword"] for r in topped} == {"physiotherapy exercises at home", "is physiotherapy worth it"}
    # "Worth it" is a consideration-stage assessment (MOFU) on its own merits.
    assert {r["keyword"]: r["funnel"] for r in topped} == {
        "physiotherapy exercises at home": "TOFU",
        "is physiotherapy worth it": "MOFU",
    }


async def test_enough_informational_keywords_means_no_topup(dfs):
    dfs.commercial_only = False
    report: dict = {}
    await expand_seed("physio", min_volume=10, location_code=2036, language_code="en", report=report)
    assert report["informational_share_before"] >= INFORMATIONAL_SHARE_FLOOR
    assert not [c for c in dfs.calls if _is_topup(c)]
    assert report["topped_up"] == 0


async def test_without_variants_the_topup_falls_back_to_the_seed(dfs):
    await expand_seed("physiotherapy", min_volume=10, location_code=2036, language_code="en")
    topups = [c for c in dfs.calls if _is_topup(c)]
    assert topups and topups[0]["keywords"] == ["physiotherapy"]


# ------------------------------------------------------------------ volume


def test_volume_status_and_threshold():
    assert volume_status({"volume": 120}) == "measured"
    assert volume_status({"volume": None}) == "unverified"
    assert volume_status({"volume": 0}) == "unverified"
    assert volume_status({"volume": 50, "volume_estimated": True}) == "estimated"
    assert keep_for_volume({"volume": None}, 10) and keep_for_volume({"volume": 0}, 10)
    assert not keep_for_volume({"volume": 6}, 10)
    assert keep_for_volume({"volume": 10}, 10) and not keep_for_volume({"volume": 30}, 50)
    assert volume_coverage([{"volume": 5}, {"volume": None}, {"volume": 1, "provider_fallback": True}]) == {
        "measured": 1, "estimated": 1, "unverified": 1, "total": 3,
    }


async def test_the_dataset_keeps_unverified_volume_and_reports_coverage(dfs):
    result = await run_multi_mode_seeding(
        ["physiotherapy"], min_volume=10, location_code=2036, language_code="en"
    )
    by_kw = {r["keyword"]: r for r in result["keyword_dataset"]}
    assert by_kw["physiotherapy sydney"]["volume_unverified"] is True
    assert by_kw["physiotherapy sydney"]["volume"] is None
    assert by_kw["physiotherapy perth"]["volume_status"] == "unverified"
    assert "physiotherapy hobart" not in by_kw, "measured 6 is below the floor"
    cov = result["volume_coverage"]
    assert cov["unverified"] == 2 and cov["measured"] == len(by_kw) - 2 and cov["total"] == len(by_kw)
    assert "kept and flagged volume_unverified" in result["note"]


# ------------------------------------------------------------------ labels


async def test_every_row_has_intent_and_funnel_from_every_endpoint(dfs, cache):
    calls: list = []
    result = await run_multi_mode_seeding(
        ["physiotherapy", "dry needling"], min_volume=10, location_code=2036, language_code="en",
        query_variants=QueryVariantResolver(ask=_model(calls)),
    )
    dataset = result["keyword_dataset"]
    endpoints = {r.get("ahrefs_endpoint") for r in dataset}
    assert {
        "dataforseo_keyword_suggestions",
        "dataforseo_related_keywords",
        "dataforseo_keyword_ideas",
        "dataforseo_keyword_ideas_variants",
        "dataforseo_keyword_ideas_informational_topup",
    } <= endpoints
    assert all(r.get("intent") for r in dataset)
    assert all(r.get("funnel") in {"TOFU", "MOFU", "BOFU"} for r in dataset)
    for cluster in result["seed_clusters"]:
        for cls in ("exact", "phrase", "related", "broad"):
            assert all(r.get("intent") and r.get("funnel") for r in cluster.get(cls) or [])
    assert len(calls) == 1, "one variants call for the service's seeds"
    assert result["informational_topups"] == 2
    assert {r["seed"] for r in result["funnel_coverage"]} == {"physiotherapy", "dry needling"}


# ------------------------------------------------------------------ service output


async def test_each_service_run_shows_volume_and_funnel_coverage(dfs):
    plan = ServiceSeedPlan(
        service="Physiotherapy",
        service_id="physiotherapy",
        seeds=["physiotherapy"],
        seed_targets={"physiotherapy": {"target": "Physiotherapy", "target_type": "service"}},
    )
    merged = await run_service_scoped_seeding(
        [plan], fetch=run_multi_mode_seeding, total_cap=5, min_volume=10,
        location_code=2036, language_code="en",
    )
    run = merged["service_runs"][0]
    assert run["volume_coverage"]["unverified"] == 2
    assert set(run["funnel_counts"]) == {"TOFU", "MOFU", "BOFU"}
    assert run["informational_topups"] == 1
    assert merged["volume_coverage"] == run["volume_coverage"]
    assert merged["funnel_coverage"][0]["service"] == "Physiotherapy"


def test_saved_memory_keeps_the_funnel_on_keyword_rows():
    slim = slim_search_demand_memory(
        {
            "seed_clusters": [
                {
                    "seed": "physio",
                    "exact": [{"keyword": "physio", "volume": 10, "intent": "commercial", "funnel": "MOFU"}],
                    "phrase": [{"keyword": "what is physio", "volume": None, "intent": "informational",
                                "funnel": "TOFU", "volume_unverified": True}],
                }
            ]
        }
    )
    cluster = slim["seed_clusters"][0]
    assert cluster["exact"][0]["funnel"] == "MOFU"
    assert cluster["phrase"][0]["funnel"] == "TOFU" and cluster["phrase"][0]["volume_unverified"] is True


def _patch_endpoints(monkeypatch, handler):
    for endpoint in ("keyword_suggestions", "related_keywords", "keyword_ideas"):
        monkeypatch.setattr(
            keyword_seeding.dataforseo,
            endpoint,
            lambda seed, _e=endpoint, **kwargs: handler(_e, seed, **kwargs),
        )


@pytest.mark.asyncio
async def test_each_endpoint_fetches_one_page_for_all_funnel_intents(monkeypatch):
    """A page that is not full already holds every intent's rows: one request, not three."""
    calls: list[tuple[str, dict]] = []

    async def _ok(endpoint: str, seed: str, **kwargs):
        calls.append((endpoint, kwargs))
        intents = kwargs["extra_filters"][0][2]
        return [{"keyword": f"{endpoint} {i}", "volume": 40, "intent": i} for i in intents], []

    _patch_endpoints(monkeypatch, _ok)
    rows, errors = await keyword_seeding.expand_seed_dataforseo(
        "seo", location_code=2036, limit=1000, language_code="en"
    )
    assert errors == []
    assert len(rows) == 9
    assert len(calls) == 3
    for endpoint, kwargs in calls:
        field, op, intents = kwargs["extra_filters"][0]
        assert (op, intents) == ("in", ["informational", "commercial", "transactional"])
        assert kwargs["limit"] == 1000 and kwargs["min_volume"] == 10 and kwargs["keep_null_volume"]
        if endpoint == "related_keywords":
            assert field == "keyword_data.search_intent_info.main_intent"
        else:
            assert field == "search_intent_info.main_intent"
    assert {endpoint for endpoint, _kwargs in calls} == {"keyword_suggestions", "related_keywords", "keyword_ideas"}


def _labs_match(row: dict, cond) -> bool:
    """Evaluate a Labs filter on a fake row (volume and intent only)."""
    if isinstance(cond[0], str):
        field, op, value = cond
        actual = row["intent"] if field.endswith("main_intent") else row["volume"] if "search_volume" in field else None
        if field.endswith("keyword"):
            return True  # noise filters: fake keywords are clean
        if op == "in":
            return actual in value
        if op == "=":
            return actual == value
        if actual is None:
            return False
        return {">=": actual >= value, "<=": actual <= value, ">": actual > value, "<": actual < value}[op]
    result = _labs_match(row, cond[0])
    for i in range(1, len(cond), 2):
        nxt = _labs_match(row, cond[i + 1])
        result = (result or nxt) if cond[i] == "or" else (result and nxt)
    return result


def _fake_labs(pool: list[dict]):
    """A fake Labs endpoint over ``pool``: filters, volume desc with nulls last, limit."""
    calls: list[dict] = []

    async def handler(endpoint: str, seed: str, **kwargs):
        calls.append(kwargs)
        conds = list(kwargs["extra_filters"])
        if kwargs.get("min_volume") is not None:
            floor = ["keyword_info.search_volume", ">=", kwargs["min_volume"]]
            conds.append([floor, "or", ["keyword_info.search_volume", "=", None]] if kwargs["keep_null_volume"] else floor)
        hits = [r for r in pool if all(_labs_match(r, c) for c in conds)]
        hits.sort(key=lambda r: (r["volume"] is None, -(r["volume"] or 0)))
        return [dict(r) for r in hits[: kwargs["limit"]]], []

    return handler, calls


def _old_three_pages(pool: list[dict], page: int) -> set[str]:
    out: set[str] = set()
    for intent in ("informational", "commercial", "transactional"):
        mine = [r for r in pool if r["intent"] == intent and (r["volume"] is None or r["volume"] >= 10)]
        mine.sort(key=lambda r: (r["volume"] is None, -(r["volume"] or 0)))
        out |= {r["keyword"] for r in mine[:page]}
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("nulls", [0, 400])
async def test_full_page_follow_ups_return_what_three_pages_would(monkeypatch, nulls):
    """A full all-intents page is topped up per intent below its lowest volume, so the
    keywords match one full page per intent, at one extra request instead of a wasted page."""
    pool = []
    # Both intents share each volume three times over, so the page is cut through
    # ties of both (page 50 = 8 volumes x 6 rows + 2 informational rows at 960).
    for i in range(120):
        pool.append({"keyword": f"info {i}", "volume": 1000 - (i // 3) * 5, "intent": "informational"})
    for i in range(60):
        pool.append({"keyword": f"comm {i}", "volume": 1000 - (i // 3) * 5, "intent": "commercial"})
    for i in range(15):
        pool.append({"keyword": f"trans {i}", "volume": 20 + i, "intent": "transactional"})
    # More nulls than an intent's page has room for, so the null follow-up limit matters.
    for i in range(nulls):
        pool.append({"keyword": f"null {i}", "volume": None, "intent": ("informational", "commercial")[i % 2]})
    pool.append({"keyword": "nav", "volume": 5000, "intent": "navigational"})
    pool.append({"keyword": "tiny", "volume": 5, "intent": "commercial"})

    handler, calls = _fake_labs(pool)
    _patch_endpoints(monkeypatch, handler)
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "keyword_labs_followups", True, raising=False)
    # Without nulls the page is cut mid-volume (with ties); with nulls it reaches them.
    page = 50 if not nulls else 210
    rows, errors = await keyword_seeding.expand_seed_dataforseo(
        "seo", location_code=2036, limit=page, language_code="en", classes=("phrase",)
    )
    assert errors == []
    keywords = [r["keyword"] for r in rows]
    assert len(keywords) == len(set(keywords))  # follow-up overlap is de-duplicated
    assert set(keywords) == _old_three_pages(pool, page)
    assert 2 <= len(calls) <= 4  # one page plus at most one follow-up per intent
