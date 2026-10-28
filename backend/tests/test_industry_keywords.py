"""The industry keyword layer: category-level seeds for the client's industry (2026-10-27).

The model is always faked. These tests pin down:
- the resolver asks the model once per run, caps the terms at the setting, falls back to
  the industry name without a model, and shares model terms through the keyword pool
  (never the fallback ones, and never when the terms or the provider are not real);
- the industry seeds form a plan of their own, tagged ``seed_origin="industry"`` and
  fetched on top of the seed budget;
- industry seeds top up informational keywords below 10%, service seeds below 15%;
- industry rows are judged against the client's whole service list and pass when they
  name the industry; service rows are judged exactly as before;
- the industry plan never becomes a client service.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.agents.search_demand import run_search_demand
from app.config import get_settings
from app.integrations import dataforseo
from app.services import keyword_seeding, service_seeding
from app.services.industry_keywords import (
    IndustryKeywordResolver,
    clean_terms,
    generator,
    industry_key,
)
from app.services.industry_keywords.prompts import SYSTEM_PROMPT, build_user_prompt
from app.services.keyword_relevance import (
    build_relevance_context,
    evaluate_keyword,
    filter_relevant_keywords,
)
from app.services.keyword_seeding import (
    INFORMATIONAL_SHARE_FLOOR,
    expand_seed,
    run_multi_mode_seeding,
)
from app.services.keyword_service_contract import (
    judge_industry_keyword,
    judge_keyword,
    partition_service_rejects,
)
from app.services.service_seeding import (
    EXTRA_ORIGINS,
    INDUSTRY_ORIGIN,
    INDUSTRY_SERVICE_ID,
    ServiceSeedPlan,
    industry_seed_plan,
    run_service_scoped_seeding,
)
from app.services.shared_keyword_pool.reuse import PoolContext
from app.services.shared_keyword_pool.store import load_seed
from tests.test_service_scoped_seeding_flow import _client, _profile

AU = 2036
US = 2840
AGENCY_SERVICES = ["SEO", "PPC Management"]
GEO = "Melbourne, Australia"


class FakeModel:
    """Stands in for the model call: answers with fixed terms and counts the calls."""

    def __init__(self, terms=None, *, error=None, delay=0.0, reply=None):
        self.terms = list(terms or [])
        self.error = error
        self.delay = delay
        self.reply = reply
        self.calls: list[dict] = []

    async def __call__(self, industry, **context):
        self.calls.append({"industry": industry, **context})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.reply is not None:
            return self.reply
        return {"terms": list(self.terms)}


@pytest.fixture
def live(monkeypatch):
    """Real model and provider mode (the test default is mock), so the pool is used."""
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_llm", False)
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "dataforseo_sandbox", False)
    monkeypatch.setattr(settings, "keyword_pool_enabled", True)


def _resolver(industry, services, *, ask, location_code=AU, language_code="en", **kw):
    return IndustryKeywordResolver(
        industry,
        "Australia",
        services,
        location_code=location_code,
        language_code=language_code,
        ask=ask,
        **kw,
    )


# ------------------------------------------------------------------ the resolver


async def test_resolver_caps_terms_at_the_setting(monkeypatch):
    monkeypatch.setattr(get_settings(), "keyword_industry_max_seeds", 3)
    model = FakeModel(
        [
            "digital marketing agency",
            "online marketing company",
            "marketing agency",
            "internet marketing company",
            "digital agency",
        ]
    )
    result = await IndustryKeywordResolver(
        "Digital Marketing Agency", "Australia", AGENCY_SERVICES, ask=model
    ).resolve()

    assert result.terms == ["digital marketing agency", "online marketing company", "marketing agency"]
    assert result.source == "model"
    assert result.industry_key == "digital marketing agency"
    # The model is told the industry, the market and the services it must not repeat.
    assert model.calls == [
        {"industry": "Digital Marketing Agency", "market": "Australia", "services": AGENCY_SERVICES}
    ]


async def test_resolver_falls_back_without_a_model():
    assert get_settings().use_mock_llm, "the test suite runs with the mock model"
    result = await IndustryKeywordResolver("Dental Clinic", "Australia", ["Teeth Whitening"]).resolve()

    assert result.terms == ["dental clinic", "dental clinic services"]
    assert result.source == "fallback"
    assert result.industry_key == "dental clinic"


@pytest.mark.parametrize(
    "model",
    [
        FakeModel(error=RuntimeError("model down")),
        FakeModel(["dentist"], delay=0.5),
        FakeModel(reply={"answer": "dentist"}),
        FakeModel(["what is a dentist", "dentist near me", "dentist melbourne"]),
    ],
    ids=["failure", "timeout", "no_list", "nothing_usable"],
)
async def test_a_failing_or_confused_model_falls_back_to_the_industry_name(model):
    result = await IndustryKeywordResolver(
        "Dental clinic", "Australia", [], ask=model, timeout=0.05
    ).resolve()
    assert result.source == "fallback"
    assert result.terms == ["dental clinic", "dental clinic services"]


async def test_one_run_asks_the_model_once():
    model = FakeModel(["dentist", "dental clinic"], delay=0.01)
    resolver = IndustryKeywordResolver("Dental clinic", "Australia", [], ask=model)
    first, second, third = await asyncio.gather(
        resolver.resolve(), resolver.resolve(), resolver.resolve()
    )
    again = await resolver.resolve()
    assert len(model.calls) == 1
    assert first == second == third == again


def test_the_industry_key_is_the_normalised_text():
    assert industry_key("Dental Clinic") == "dental clinic"
    assert industry_key("Dental lab") == "dental lab", "no coarse slug: a lab is not a clinic"
    assert industry_key("  Digital   Marketing Agency ") == "digital marketing agency"
    assert industry_key("Café Bakery") == "cafe bakery"
    assert industry_key("") is None and industry_key(None) is None


def test_terms_are_cleaned_to_place_free_category_searches():
    terms = clean_terms(
        [
            "Digital Marketing Agency",
            "digital marketing agencies",  # the same search, plural
            "SEO services",  # one of the client's services: seeded by the service already
            "marketing agency near me",
            "digital marketing agency melbourne",
            "what is a digital marketing agency",
            {"term": "  Online   Marketing Company. "},
            "a phrase with far too many words in it",
            42,
        ],
        services=AGENCY_SERVICES,
        limit=8,
    )
    assert terms == ["digital marketing agency", "online marketing company"]


def test_the_prompt_asks_for_category_terms_and_never_for_metrics():
    assert '{"terms"' in SYSTEM_PROMPT
    assert "search volume" in SYSTEM_PROMPT.lower()
    prompt = build_user_prompt("Dental clinic", market="Australia", services=["Teeth Whitening"])
    assert "Industry: Dental clinic" in prompt
    assert "Market: Australia" in prompt
    assert "Teeth Whitening" in prompt


# ------------------------------------------------------------------ the pool


async def test_a_second_client_in_the_same_industry_reuses_the_terms(db_session, live):
    model = FakeModel(["dentist", "dental clinic", "family dentist", "dental practice"])

    first = await _resolver("Dental clinic", ["Teeth Whitening"], ask=model).resolve()
    second = await _resolver("dental  clinic", ["Dental Implants"], ask=model).resolve()

    assert first.source == "model" and second.source == "pool"
    assert len(model.calls) == 1, "the second client did not ask the model"
    assert second.terms == first.terms == ["dentist", "dental clinic", "family dentist", "dental practice"]
    assert second.industry_key == first.industry_key == "dental clinic"

    # Another country has its own spelling and its own searches: it asks again.
    other = await _resolver("Dental clinic", ["Teeth Whitening"], ask=model, location_code=US).resolve()
    assert other.source == "model" and len(model.calls) == 2


async def test_industries_share_terms_only_when_worded_alike(db_session, live):
    lab = FakeModel(["dental laboratory", "dental lab services"])
    clinic = FakeModel(["dentist", "dental clinic"])
    assert (await _resolver("Dental lab", [], ask=lab).resolve()).source == "model"
    other = await _resolver("Dental clinic", [], ask=clinic).resolve()
    assert other.source == "model" and other.terms == ["dentist", "dental clinic"]

    agency = FakeModel(["digital marketing agency", "online marketing company"])
    first = await _resolver("Digital marketing agency", [], ask=agency).resolve()
    second = await _resolver("digital  Marketing Agency", [], ask=agency).resolve()
    assert (first.source, second.source) == ("model", "pool")
    assert len(agency.calls) == 1 and second.terms == first.terms


async def test_fallback_terms_are_not_saved(db_session, live):
    failing = FakeModel(error=RuntimeError("model down"))
    result = await _resolver("Dental clinic", [], ask=failing).resolve()
    assert result.source == "fallback"

    saved = await load_seed(
        "dental clinic", kind="industry_terms", location_code=AU, language_code="en", min_volume=0, limit=1
    )
    assert saved is None

    working = FakeModel(["dentist", "dental clinic"])
    again = await _resolver("Dental clinic", [], ask=working).resolve()
    assert again.source == "model" and len(working.calls) == 1


@pytest.mark.parametrize(
    "setting, value",
    [
        ("use_mock_llm", True),
        ("use_mock_providers", True),
        ("dataforseo_sandbox", True),
        ("keyword_pool_enabled", False),
    ],
)
async def test_the_pool_is_left_alone_when_the_terms_or_the_provider_are_not_real(
    live, monkeypatch, setting, value
):
    monkeypatch.setattr(get_settings(), setting, value)
    load, save = AsyncMock(return_value=None), AsyncMock(return_value="complete")
    monkeypatch.setattr(generator, "load_seed", load)
    monkeypatch.setattr(generator, "save_seed", save)

    result = await _resolver("Dental clinic", [], ask=FakeModel(["dentist"])).resolve()

    assert result.source == "model" and result.terms == ["dentist"]
    load.assert_not_awaited()
    save.assert_not_awaited()


async def test_without_a_language_the_pool_is_left_alone(live, monkeypatch):
    load, save = AsyncMock(return_value=None), AsyncMock(return_value="complete")
    monkeypatch.setattr(generator, "load_seed", load)
    monkeypatch.setattr(generator, "save_seed", save)
    await _resolver("Dental clinic", [], ask=FakeModel(["dentist"]), language_code=None).resolve()
    load.assert_not_awaited()
    save.assert_not_awaited()


async def test_a_pool_failure_never_fails_the_resolver(live, monkeypatch):
    monkeypatch.setattr(generator, "load_seed", AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(generator, "save_seed", AsyncMock(side_effect=RuntimeError("db down")))

    result = await _resolver("Dental clinic", [], ask=FakeModel(["dentist", "dental clinic"])).resolve()

    assert result.source == "model" and result.terms == ["dentist", "dental clinic"]


# ------------------------------------------------------------------ the seed plan


def _service_plan(service: str, seeds: list[str]) -> ServiceSeedPlan:
    key = service.lower()
    return ServiceSeedPlan(
        service=service,
        service_id=key,
        seeds=list(seeds),
        seed_targets={
            s.lower(): {"target": service, "target_type": "service", "service": service, "service_id": key}
            for s in seeds
        },
    )


def _canned(seeds: list[str], seed_targets: dict) -> dict:
    """What a per-service fetch returns: one keyword per seed, clusters tagged like the real ones."""
    clusters, dataset = [], []
    for seed in seeds:
        meta = seed_targets.get(seed.lower()) or {}
        row = {"keyword": f"{seed} cost", "seed": seed, "match_class": "exact", "volume": 100}
        cluster = {
            "seed": seed,
            "target": meta.get("target") or seed,
            "target_type": meta.get("target_type") or "keyword",
            "exact": [dict(row)],
            "phrase": [],
            "related": [],
            "broad": [],
            "keyword_count": 1,
        }
        if meta.get("seed_origin"):
            cluster["seed_origin"] = meta["seed_origin"]
        clusters.append(cluster)
        dataset.append({**row, "target": cluster["target"], "target_type": cluster["target_type"]})
    return {
        "seeds": list(seeds),
        "seed_clusters": clusters,
        "keyword_dataset": dataset,
        "keyword_count": len(dataset),
        "class_counts": {"exact": len(dataset), "phrase": 0, "related": 0, "broad": 0},
        "target_type_counts": {},
        "class_coverage": [],
        "seeds_with_all_classes": 0,
        "provider_errors": [],
        "providers": ["dataforseo"],
        "excluded_keywords": [],
        "keyword_cleaning": {},
    }


def test_industry_plan_seeds_are_extra_and_tagged():
    plan = industry_seed_plan(
        "Digital marketing agency",
        ["digital marketing agency", "online marketing company", "Online Marketing Company"],
    )

    assert plan.service == "Digital marketing agency"
    assert plan.service_id == INDUSTRY_SERVICE_ID == "industry"
    assert plan.is_industry
    assert plan.status == "planned"
    assert plan.seeds == ["digital marketing agency", "online marketing company"]
    assert INDUSTRY_ORIGIN == "industry" and INDUSTRY_ORIGIN in EXTRA_ORIGINS
    for seed in plan.seeds:
        assert plan.is_extra(seed)
        assert plan.seed_targets[seed]["seed_origin"] == "industry"
        assert plan.seed_targets[seed]["service_id"] == "industry"
        assert plan.seed_targets[seed]["service"] == "Digital marketing agency"
    assert plan.extra_seed_count == len(plan.seeds)
    assert industry_seed_plan("Dental clinic", []).status == "no_seeds"


async def test_industry_seeds_ride_on_top_of_the_seed_budget():
    seo = _service_plan("SEO", ["seo", "seo audit", "seo agency"])
    industry = industry_seed_plan("Digital marketing agency", ["digital marketing agency", "online marketing company"])
    fetched: dict[str, list[str]] = {}

    async def fetch(seeds, *, seed_targets=None, **kw):
        fetched[next(iter(seed_targets.values()))["service_id"]] = list(seeds)
        return _canned(seeds, seed_targets)

    result = await run_service_scoped_seeding([seo, industry], fetch=fetch, total_cap=2)

    assert fetched["seo"] == ["seo", "seo audit"], "the service keeps its whole share"
    assert fetched["industry"] == ["digital marketing agency", "online marketing company"]
    runs = {r["service_id"]: r for r in result["service_runs"]}
    assert runs["industry"]["kind"] == "industry" and runs["industry"]["status"] == "ok"
    assert runs["seo"].get("kind") != "industry"
    assert result["note"].startswith("Per-service multi-mode seeding: 1 of 1 service(s) fetched")


@pytest.mark.parametrize("with_industry, expected", [(True, [4.5, 4.5, 1.0]), (False, [5.0, 5.0])])
async def test_the_industry_plan_takes_a_fixed_slice_of_the_dollar_cap(with_industry, expected):
    from app.services import api_meter

    caps: dict[str, float] = {}

    async def fetch(seeds, *, seed_targets=None, **kw):
        caps[next(iter(seed_targets.values()))["service_id"]] = api_meter._service_budget.get().cap
        return _canned(seeds, seed_targets)

    plans = [_service_plan("SEO", ["seo"]), _service_plan("PPC", ["ppc"])]
    if with_industry:
        plans.append(industry_seed_plan("Digital marketing agency", ["digital marketing agency"]))
    async with api_meter.api_meter_context(phase_budget_usd=10.0):
        await run_service_scoped_seeding(plans, fetch=fetch, total_cap=4)

    assert service_seeding.INDUSTRY_BUDGET_SHARE == 0.10
    assert [caps[p.service_id] for p in plans] == expected


async def test_a_working_industry_fetch_does_not_hide_failed_services():
    seo = _service_plan("SEO", ["seo"])
    industry = industry_seed_plan("Digital marketing agency", ["digital marketing agency"])

    async def fetch(seeds, *, seed_targets=None, **kw):
        if next(iter(seed_targets.values())).get("seed_origin") != INDUSTRY_ORIGIN:
            raise RuntimeError("provider down")
        return _canned(seeds, seed_targets)

    with pytest.raises(RuntimeError, match="provider down"):
        await run_service_scoped_seeding([seo, industry], fetch=fetch, total_cap=4)


async def test_a_failed_industry_fetch_is_not_reported_as_a_service_failure():
    seo = _service_plan("SEO", ["seo"])
    industry = industry_seed_plan("Digital marketing agency", ["digital marketing agency"])

    async def fetch(seeds, *, seed_targets=None, **kw):
        if next(iter(seed_targets.values())).get("seed_origin") == INDUSTRY_ORIGIN:
            raise RuntimeError("provider down")
        return _canned(seeds, seed_targets)

    result = await run_service_scoped_seeding([seo, industry], fetch=fetch, total_cap=4)

    errors = result["provider_errors"]
    assert not [e for e in errors if e.startswith("service_seeding_failed")]
    assert [e for e in errors if e.startswith("industry_seeding_failed:Digital marketing agency")]


# ------------------------------------------------------------------ informational floor


async def test_industry_seeds_use_the_ten_percent_informational_cap(monkeypatch):
    floors: dict[str, float] = {}

    async def fake_expand(seed, **kw):
        floors[seed] = kw["informational_floor"]
        return [], []

    monkeypatch.setattr(keyword_seeding, "expand_seed", fake_expand)
    monkeypatch.setattr(dataforseo, "search_volume", AsyncMock(return_value=([], [])))
    industry = industry_seed_plan("Digital marketing agency", ["digital marketing agency"])
    targets = {**_service_plan("SEO", ["seo"]).seed_targets, **industry.seed_targets}

    await run_multi_mode_seeding(
        ["seo", "digital marketing agency"], seed_targets=targets, location_code=AU, language_code="en"
    )

    assert get_settings().keyword_industry_informational_share == 0.10
    assert floors == {"seo": INFORMATIONAL_SHARE_FLOOR, "digital marketing agency": 0.10}
    assert INFORMATIONAL_SHARE_FLOOR == 0.15


async def test_industry_seeds_ask_for_no_query_variants(monkeypatch):
    seen: dict[str, object] = {}
    asked: list[list[str]] = []

    async def fake_expand(seed, **kw):
        seen[seed] = kw["variants"]
        return [], []

    class Variants:
        async def variants_for(self, seeds):
            asked.append(list(seeds))
            return {s: ["what is " + s] for s in seeds}

    monkeypatch.setattr(keyword_seeding, "expand_seed", fake_expand)
    monkeypatch.setattr(dataforseo, "search_volume", AsyncMock(return_value=([], [])))
    industry = industry_seed_plan("Digital marketing agency", ["digital marketing agency"])
    targets = {**_service_plan("SEO", ["seo"]).seed_targets, **industry.seed_targets}

    await run_multi_mode_seeding(
        ["seo", "digital marketing agency"],
        seed_targets=targets,
        location_code=AU,
        language_code="en",
        query_variants=Variants(),
    )

    assert asked == [["seo"]], "the variants model is never asked about an industry seed"
    assert seen["digital marketing agency"] is None
    assert await seen["seo"]() == ["what is seo"], "service seeds still get theirs"


@pytest.mark.parametrize("floor, tops_up", [(0.15, True), (0.10, False)])
async def test_expand_seed_tops_up_only_below_its_own_floor(monkeypatch, floor, tops_up):
    """One informational keyword in nine (11%): under a service seed's 15%, over an industry seed's 10%."""
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "keyword_extra_idea_pulls", True, raising=False)

    async def main_pull(seed, **kw):
        rows = [
            {"keyword": f"{seed} {word}", "volume": 100, "funnel": "BOFU", "source": "dataforseo", "match_class": "phrase"}
            for word in ("cost", "price", "quote", "hire", "company", "agency", "firm", "pricing")
        ]
        rows.append(
            {"keyword": f"what is {seed}", "volume": 100, "funnel": "TOFU", "source": "dataforseo", "match_class": "phrase"}
        )
        return rows, []

    ideas = AsyncMock(return_value=([], []))
    monkeypatch.setattr(keyword_seeding, "expand_seed_dataforseo", main_pull)
    monkeypatch.setattr(keyword_seeding.dataforseo, "keyword_ideas", ideas)
    report: dict = {}

    await expand_seed(
        "digital marketing agency",
        location_code=AU,
        language_code="en",
        report=report,
        informational_floor=floor,
    )

    assert report["informational_share_before"] == pytest.approx(1 / 9, abs=0.001)
    assert ideas.await_count == (1 if tops_up else 0)


# ------------------------------------------------------------------ the gate


def _industry_row(keyword: str, industry: str) -> dict:
    return {
        "keyword": keyword,
        "seed": "digital marketing agency",
        "match_class": "exact" if keyword == "digital marketing agency" else "broad",
        "service": industry,
        "service_id": INDUSTRY_SERVICE_ID,
        "seed_origin": INDUSTRY_ORIGIN,
        "target_type": "industry",
    }


def _industry_ctx(industry: str):
    """The relevance context Phase 5 builds for the industry plan's fetch."""
    return build_relevance_context(
        services=AGENCY_SERVICES,
        seeds=["digital marketing agency"],
        industry=industry,
        geography=GEO,
        industry_layer=True,
    )


@pytest.mark.parametrize("industry", ["Digital marketing agency", "Marketing agency", "marketing"])
def test_an_industry_term_survives_the_gate_for_an_agency_client(industry):
    row = _industry_row("digital marketing agency", industry)

    # The relevance gate inside each fetch.
    kept, excluded, _ = filter_relevant_keywords([row], _industry_ctx(industry))
    assert [r["keyword"] for r in kept] == ["digital marketing agency"] and not excluded
    assert kept[0]["commercial_target_eligible"] is True

    # The service rules of the keyword validation gate: kept as it is, not held for review.
    kept, dropped = partition_service_rejects(
        [row], services=AGENCY_SERVICES, industry=industry, geography=GEO
    )
    assert kept == [row] and not dropped


@pytest.mark.parametrize("industry", ["Digital marketing agency", "Marketing agency", "marketing"])
def test_an_unrelated_term_still_fails_the_gate(industry):
    row = _industry_row("dog grooming", industry)

    kept, excluded, _ = filter_relevant_keywords([row], _industry_ctx(industry))
    assert not kept and [e["keyword"] for e in excluded] == ["dog grooming"]

    kept, dropped = partition_service_rejects(
        [row], services=AGENCY_SERVICES, industry=industry, geography=GEO
    )
    assert not kept and dropped[0]["relevance_drop_reason"] == "service_mismatch"


@pytest.mark.parametrize("term", ["online marketing company", "marketing services"])
def test_an_industry_seed_term_survives_without_being_demoted(term):
    row = {**_industry_row(term, "Digital marketing agency"), "seed": term, "match_class": "exact"}
    ctx = build_relevance_context(
        services=AGENCY_SERVICES, seeds=[term], industry="Digital marketing agency", geography=GEO,
        industry_layer=True,
    )
    kept, excluded, _ = filter_relevant_keywords([row], ctx)
    assert [r["keyword"] for r in kept] == [term] and not excluded
    assert kept[0]["commercial_target_eligible"] is True
    assert kept[0]["relevance_reason"] != "service_review"

    kept, dropped = partition_service_rejects(
        [row], services=AGENCY_SERVICES, industry="Digital marketing agency", geography=GEO
    )
    assert kept == [row] and not dropped, "kept as it is, not held for review"


def test_a_client_without_service_contracts_keeps_its_own_industry_seed_terms():
    services = ["Blocked Drains", "Hot Water Repairs"]
    ctx = build_relevance_context(
        services=services, seeds=["plumber"], industry="Plumbing", geography=GEO, industry_layer=True
    )
    rows = [
        {**_industry_row(keyword, "Plumbing"), "seed": "plumber", "match_class": cls}
        for keyword, cls in (("plumber", "exact"), ("emergency plumber", "phrase"), ("dog grooming", "broad"))
    ]
    kept, excluded, _ = filter_relevant_keywords(rows, ctx)
    assert [r["keyword"] for r in kept] == ["plumber", "emergency plumber"]
    assert all(r["commercial_target_eligible"] is True for r in kept)
    assert [e["keyword"] for e in excluded] == ["dog grooming"]


# Service rows, judged before the industry gate existed (recorded 2026-10-28 with
# scratchpad probe_service_rows.py): (keyword, service, contract verdict, lexical verdict).
SERVICE_ROW_VERDICTS = [
    ("seo agency", "SEO", "keep", (True, "service_match")),
    ("seo agency", "PPC Management", "drop:service_mismatch", (False, "service_mismatch")),
    ("digital marketing agency", "SEO", "review", (True, "service_overlap")),
    ("digital marketing agency", "PPC Management", "drop:service_mismatch", (True, "service_overlap")),
    ("online marketing company", "SEO", "review", (True, "service_review")),
    ("online marketing company", "PPC Management", "drop:service_mismatch", (False, "service_mismatch")),
    ("ppc management melbourne", "SEO", "drop:service_mismatch", (False, "service_mismatch")),
    ("ppc management melbourne", "PPC Management", "keep", (True, "service_match")),
    ("seo sydney", "SEO", "drop:geo_mismatch", (False, "geo_mismatch")),
    ("seo sydney", "PPC Management", "drop:geo_mismatch", (False, "geo_mismatch")),
    ("dog grooming", "SEO", "drop:service_mismatch", (False, "service_mismatch")),
    ("dog grooming", "PPC Management", "drop:service_mismatch", (False, "service_mismatch")),
    ("google ads gmail", "SEO", "drop:negative_concept", (False, "platform_navigation")),
    ("google ads gmail", "PPC Management", "drop:negative_concept", (False, "platform_navigation")),
    ("what is seo", "SEO", "keep", (True, "service_match")),
    ("what is seo", "PPC Management", "drop:service_mismatch", (False, "service_mismatch")),
    ("marketing services", "SEO", "review", (True, "service_review")),
    ("marketing services", "PPC Management", "drop:service_mismatch", (False, "service_mismatch")),
    ("plumber", "SEO", "drop:service_mismatch", (False, "service_mismatch")),
    ("plumber", "PPC Management", "drop:service_mismatch", (False, "service_mismatch")),
]


@pytest.mark.parametrize("keyword, service, contract, lexical", SERVICE_ROW_VERDICTS)
def test_service_rows_keep_their_verdicts(keyword, service, contract, lexical):
    seed = "seo" if service == "SEO" else "ppc management"
    kept, dropped = partition_service_rejects(
        [{"keyword": keyword, "seed": seed, "service": service}],
        services=AGENCY_SERVICES,
        industry="Digital marketing agency",
        geography=GEO,
    )
    got = (
        "drop:" + dropped[0]["relevance_drop_reason"]
        if dropped
        else ("review" if kept[0].get("relevance_reason") == "service_review" else "keep")
    )
    ctx = build_relevance_context(
        services=AGENCY_SERVICES, seeds=["seo"], industry="Digital marketing agency", geography=GEO
    )
    assert got == contract
    assert evaluate_keyword(keyword, ctx, match_class="phrase", seed=seed)[:2] == lexical


def test_naming_the_industry_does_not_lift_the_hard_rules():
    # A place outside the client's market still drops it.
    verdict = judge_industry_keyword(
        "marketing agency sydney", industry="Marketing agency", services=AGENCY_SERVICES, geography=GEO
    )
    assert verdict.decision == "DROP" and verdict.reason == "geo_mismatch"
    # A query one of the client's services keeps is kept, whatever the industry.
    verdict = judge_industry_keyword(
        "ppc management agency", industry="Marketing agency", services=AGENCY_SERVICES, geography=GEO
    )
    assert verdict.decision == "KEEP" and verdict.family == "ppc"
    # A client none of whose services has a contract is left to the lexical gate, as today.
    verdict = judge_industry_keyword("dog grooming", industry="Dental clinic", services=["Teeth Whitening"])
    assert verdict.decision == "SKIP"


def test_service_rows_are_judged_exactly_as_before():
    service_row = {"keyword": "digital marketing agency", "seed": "seo", "match_class": "related", "service": "SEO"}
    expected = judge_keyword("digital marketing agency", service="SEO", geography=GEO)
    assert expected.decision == "REVIEW"

    kept, dropped = partition_service_rejects(
        [service_row], services=AGENCY_SERVICES, industry="Marketing agency", geography=GEO
    )
    assert not dropped and kept[0]["relevance_reason"] == "service_review"

    # A service plan's relevance context is not an industry context.
    ctx = build_relevance_context(
        services=AGENCY_SERVICES, seeds=["seo"], industry="Marketing agency", geography=GEO
    )
    assert ctx.industry_layer is False
    assert evaluate_keyword("digital marketing agency", ctx, match_class="related", seed="seo")[:2] == (
        True,
        "service_review",
    )
    assert evaluate_keyword("digital marketing agency", ctx, match_class="exact", seed="digital marketing agency")[0] is False


# ------------------------------------------------------------------ Phase 5


async def _run_phase(fetch):
    """Run the whole phase with ``fetch`` as the per-service keyword fetch."""
    profile = _profile()
    profile.id = uuid4()
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock())
    captured: dict = {}
    real = service_seeding.run_service_scoped_seeding

    async def seeding_spy(plans, **kw):
        captured["plans"] = list(plans)
        captured["result"] = await real(plans, **kw)
        return captured["result"]

    related = AsyncMock(return_value=([], []))
    with (
        patch("app.agents.search_demand.get_profile", AsyncMock(return_value=profile)),
        patch("app.agents.search_demand.load_skill", return_value=""),
        patch("app.agents.search_demand.load_skill_file", return_value=""),
        patch("app.agents.search_demand._load_cdd_fields", AsyncMock(return_value={})),
        patch("app.agents.search_demand.resolve_competitors", AsyncMock(return_value=[])),
        patch(
            "app.agents.search_demand._harvest_competitor_page_keywords",
            AsyncMock(return_value={"keywords": [], "snapshots": [], "stats": {}}),
        ),
        patch("app.agents.search_demand.run_multi_mode_seeding", fetch),
        patch("app.agents.search_demand.run_service_scoped_seeding", seeding_spy),
        patch.object(dataforseo, "related_keywords", related),
    ):
        events = await run_search_demand(
            db,
            client=_client(),
            session_id=uuid4(),
            user_id=uuid4(),
            message="Run keyword research",
        )
    return profile.search_demand_summary, events, captured, related


async def test_a_failing_industry_layer_never_fails_the_phase():
    class Broken:
        def __init__(self, *a, **kw):
            pass

        async def resolve(self):
            raise RuntimeError("unexpected bug")

    async def fetch(seeds, *, seed_targets=None, **kw):
        return _canned(seeds, seed_targets)

    with patch("app.agents.search_demand.IndustryKeywordResolver", Broken):
        summary, events, captured, _ = await _run_phase(fetch)

    layer = summary["keyword_seeding"]["industry_keywords"]
    assert layer["source"] == "fallback"
    assert layer["seeds"] == ["digital marketing agency", "digital marketing agency services"]
    assert [r for r in captured["result"]["service_runs"] if r.get("kind") == "industry"]


async def test_industry_rows_do_not_become_a_service():
    calls: list[dict] = []

    async def fetch(seeds, *, seed_targets=None, relevance_context=None, pool=None, **kw):
        first = next(iter(seed_targets.values()))
        calls.append(
            {
                "service": first["service"],
                "origin": first.get("seed_origin"),
                "seeds": list(seeds),
                "ctx": relevance_context,
                "pool": pool,
            }
        )
        return _canned(seeds, seed_targets)

    summary, events, captured, related = await _run_phase(fetch)
    industry_name = "digital marketing agency"  # the client's industry (mock model: fallback terms)

    # The industry layer was fetched once, beside the services, with its own gate context.
    industry_calls = [c for c in calls if c["origin"] == INDUSTRY_ORIGIN]
    service_calls = [c for c in calls if c["origin"] != INDUSTRY_ORIGIN]
    assert len(industry_calls) == 1
    assert industry_calls[0]["seeds"] == ["digital marketing agency", "digital marketing agency services"]
    assert industry_calls[0]["ctx"].industry_layer is True
    assert {c["service"] for c in service_calls} == {"SEO", "Meta Ads"}
    assert all(c["ctx"].industry_layer is False for c in service_calls)
    assert isinstance(industry_calls[0]["pool"], PoolContext)
    assert industry_calls[0]["pool"].industry_key == industry_name

    # Its run is marked as the industry layer; every other run is a service.
    runs = captured["result"]["service_runs"]
    industry_runs = [r for r in runs if r.get("kind") == "industry"]
    assert [r["service_id"] for r in industry_runs] == ["industry"]
    assert {r["service"] for r in runs if r.get("kind") != "industry"} == {"SEO", "Meta Ads"}

    def _names(rows) -> set[str]:
        return {str(r.get("name") or r.get("service") or "").lower() for r in rows} | {
            str(r.get("id") or r.get("service_id") or "").lower() for r in rows
        }

    # The client's services, catalog and prioritization carry no industry service.
    assert {s.lower() for s in summary["services"]} == {"seo", "meta ads"}
    for rows in (summary["service_catalog"], summary["service_prioritization"]["service_catalog"]):
        assert not ({"industry", industry_name} & _names(rows))
    assert summary["service_prioritization"]["selected_service_ids"] == ["seo", "meta ads"]
    assert set(summary["service_prioritization"]["service_scope"]) == {"seo", "meta ads"}

    # Per-service summaries list the services only.
    seeding = summary["keyword_seeding"]
    assert {e["service"] for e in seeding["sub_service_seeding"]} == {"SEO", "Meta Ads"}
    for key in ("seed_synonyms", "subservice_expansion"):
        assert not ({"industry", industry_name} & {str(e["service"]).lower() for e in seeding[key]})
    assert not [e for e in summary["provider_errors"] if industry_name in e.lower()]

    # The industry layer is reported on its own, never as a service.
    assert seeding["industry_keywords"]["source"] == "fallback"
    assert seeding["industry_keywords"]["seeds"] == industry_calls[0]["seeds"]
    notices = [e["content"] for e in events if e.get("type") == "system_notice"]
    per_service = next(n for n in notices if n.startswith("Seeded per service"))
    assert industry_name not in per_service.lower()
    assert any(n.startswith("Industry seeds") and industry_name in n for n in notices)

    # Related-keyword lookups (a capped, per-service step) never spend a slot on it.
    asked = {str(call.args[0]).lower() for call in related.await_args_list}
    assert asked and not (asked & {"digital marketing agency", "digital marketing agency services"})
