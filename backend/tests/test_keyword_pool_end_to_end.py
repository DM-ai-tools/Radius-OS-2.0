"""End to end: the keyword pool, the industry layer, the URL tree and the secondary keywords.

Two dental-like clients share three of their five services. Client A (Melbourne) researches
first against a fake provider and the real SQLite pool; client B (Sydney) comes second. The
run is real from there on: the industry resolver, the per-service seeding with the pool,
each client's own relevance gate, the rules clustering, ``build_final_url_map`` (home row
and URL tree) and the Phase 6 secondary-keyword top-up. Only the provider is fake
(``expand_seed_dataforseo`` and ``dataforseo.search_volume``) and the industry model reply.

Nothing here calls a network service. Every volume is a number the fake provider chose.
"""

from __future__ import annotations

import copy
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import get_settings
from app.db import AsyncSessionLocal, engine
from app.integrations import dataforseo
from app.models import KeywordPoolEntry, KeywordPoolFetch
from app.services import keyword_seeding
from app.services.competitive_context import resolve_geographic_focus
from app.services.industry_keywords import IndustryKeywordResolver
from app.services.keyword_cluster_structure import stable_cluster_id
from app.services.keyword_clustering import run_keyword_clustering
from app.services.keyword_relevance import build_relevance_context
from app.services.keyword_seeding import dataforseo_rows, run_multi_mode_seeding
from app.services.secondary_keywords import (
    SECONDARY_ROW_CAP,
    modifier_candidates,
    top_up_url_map_secondaries,
)
from app.services.service_seeding import (
    ServiceSeedPlan,
    industry_seed_plan,
    run_service_scoped_seeding,
)
from app.services.shared_keyword_pool.location import (
    client_places,
    is_location_specific,
)
from app.services.shared_keyword_pool.reuse import PoolContext
from app.services.url_mapping import (
    _sheet_page_type,
    build_final_url_map,
    check_url_map_consistency,
    cluster_is_page_ready,
)

LOCATION = 2036
LANGUAGE = "en"
INDUSTRY = "Dental clinic"
LEVELS = ("l1_category", "l2_subcategory", "l3_subsubcategory", "l4_attribution")
# The endpoint a row carries when the targeted place lookup (a search-volume call) measured it.
TOPUP_ENDPOINT = "dataforseo_search_volume"

SHARED = ["Dental implants", "Teeth whitening", "Dental crowns"]
A_ONLY = ["Root canal treatment", "Dental veneers"]
B_ONLY = ["Wisdom teeth removal", "Emergency dentist"]

# --------------------------------------------------------------------------- the fake provider

# What the provider answers for the industry seeds (the head terms of a home page).
INDUSTRY_ROWS: dict[str, list[tuple[str, int]]] = {
    "dental clinic": [
        ("dental clinic", 2400),
        ("best dental clinic", 720),
        ("dental clinic prices", 260),
        ("dental clinic melbourne", 590),
        ("dental clinic near me", 880),
    ],
    "family dentist": [
        ("family dentist", 1900),
        ("family dentist cost", 140),
        ("family dentist melbourne", 320),
        ("family dentist near me", 700),
    ],
}
# One thin seed: a single keyword, so its row has nothing to show as secondary keywords.
THIN_ROWS = {"wisdom teeth removal": [("wisdom teeth removal cost", 90)]}
# What the provider measures for "<seed> <place>" (a place lookup). Missing means no data.
PLACE_VOLUMES = {
    "dental implants sydney": 210,
    "teeth whitening sydney": 90,
    "dental crowns sydney": 60,
    "dental clinic sydney": 400,
    "family dentist sydney": 220,
}


def seed_rows(seed: str) -> list[tuple[str, int]]:
    """What the provider returns for one seed query: the place-free keywords, plus the place
    keywords it surfaces for an Australian location (Melbourne, Sydney, "near me")."""
    if seed in INDUSTRY_ROWS:
        return INDUSTRY_ROWS[seed]
    if seed in THIN_ROWS:
        return THIN_ROWS[seed]
    return [
        (seed, 2900),
        (f"{seed} cost", 500),
        (f"best {seed}", 320),
        (f"{seed} procedure", 140),
        (f"{seed} melbourne", 250),
        (f"{seed} sydney", 180),
        (f"{seed} near me", 150),
    ]


def modifier_answer(keyword: str) -> tuple[bool, int | None]:
    """The provider's answer for a modifier variant: (returned at all, its volume)."""
    if "wisdom" in keyword:
        return False, None  # nobody searches these
    if keyword.endswith(" company"):
        return True, None  # a row comes back, with no volume
    if keyword.endswith((" agency", " firm", " consultant", " provider")):
        return False, None
    if keyword.startswith("best "):
        return True, 300
    if keyword.startswith("top "):
        return True, 60
    if keyword.startswith("cheap "):
        return True, 40
    if keyword.startswith("affordable "):
        return True, 25
    if keyword.endswith(" services"):
        return True, 85
    return False, None


class FakeProvider:
    """``expand_seed_dataforseo`` and ``dataforseo.search_volume`` in one recorder.

    ``stage`` names which client (or phase) is running, so a test can count the calls of
    one run. A ``search_volume`` call that names a language is a place lookup or a
    modifier lookup; the Phase 5 overview call after the seeds names none.
    """

    def __init__(self) -> None:
        self.stage = "setup"
        self.fetches: list[dict[str, Any]] = []
        self.volume_calls: list[dict[str, Any]] = []
        # Any request that reached the network layer. The test fails if there is one: every
        # provider call this scenario makes is one of the two faked above.
        self.network: list[str] = []

    async def expand(self, seed, *, location_code, min_volume=10, limit=60, classes=None, language_code=None):
        self.fetches.append({"stage": self.stage, "seed": seed, "location": location_code, "language": language_code})
        raw = [{"keyword": kw, "volume": vol, "cpc": 2.0} for kw, vol in seed_rows(seed)]
        return dataforseo_rows(raw, endpoint="keyword_suggestions", seed=seed), []

    async def search_volume(self, keywords, *, location_code, language_code=None):
        keywords = list(keywords)
        self.volume_calls.append(
            {"stage": self.stage, "keywords": keywords, "location": location_code, "language": language_code}
        )
        rows = []
        for keyword in keywords:
            if keyword in PLACE_VOLUMES:
                rows.append({"keyword": keyword, "volume": PLACE_VOLUMES[keyword], "cpc": 3.0})
            elif language_code is None:
                # The overview call after the seeds: each seed's own volume, where it has one.
                head = dict(seed_rows(keyword)).get(keyword)
                if head is not None:
                    rows.append({"keyword": keyword, "volume": head, "cpc": 2.0})
            else:
                returned, volume = modifier_answer(keyword)
                if returned:
                    rows.append({"keyword": keyword, "volume": volume, "cpc": 2.5})
        return rows, []

    def fetched(self, stage: str) -> Counter:
        return Counter(call["seed"] for call in self.fetches if call["stage"] == stage)

    def place_lookups(self, stage: str) -> list[dict[str, Any]]:
        """The "<seed> <place>" lookups of a stage: search-volume calls that name a language
        and ask only for Sydney keywords (the modifier lookups of Phase 6 ask for none)."""
        places = (" sydney", " new south wales")
        return [
            call
            for call in self.volume_calls
            if call["stage"] == stage and call["language"] and all(k.endswith(places) for k in call["keywords"])
        ]


class FakeModel:
    """The industry model: category terms only, never a metric. Counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, industry, *, market=None, services=()):
        self.calls += 1
        return {"terms": ["dental clinic", "family dentist", "dentist near me"]}


@contextmanager
def real_llm():
    """The industry terms are saved to the pool only for a real (not mock) model."""
    settings = get_settings()
    before = settings.use_mock_llm
    settings.use_mock_llm = False
    try:
        yield
    finally:
        settings.use_mock_llm = before


# --------------------------------------------------------------------------- one client's Phase 5


@dataclass
class Client:
    name: str
    focus: str
    services: list[str]
    domain: str


CLIENT_A = Client("A", "Melbourne, Victoria", SHARED + A_ONLY, "alpha-dental.example")
CLIENT_B = Client("B", "Sydney", SHARED + B_ONLY, "bright-smile.example")


@dataclass
class Research:
    """One client's seeding: what it returned, the pool report and the industry terms."""

    client: Client
    result: dict[str, Any]
    pool: PoolContext
    terms: Any
    geo: dict[str, Any]

    @property
    def keywords(self) -> list[str]:
        return [row["keyword"] for row in self.result["keyword_dataset"]]


def _plan(service: str) -> ServiceSeedPlan:
    seed = service.lower()
    return ServiceSeedPlan(
        service=service,
        service_id=seed.replace(" ", "-"),
        seeds=[seed],
        seed_targets={
            seed: {"target": seed, "target_type": "keyword", "service": service, "service_id": seed.replace(" ", "-")}
        },
    )


def _context(client: Client, geo: dict[str, Any], seeds: list[str], *, industry_layer: bool = False):
    return build_relevance_context(
        services=client.services,
        cdd_keywords=[s.lower() for s in client.services],
        seeds=seeds,
        brand_name=client.name,
        domain=client.domain,
        industry=INDUSTRY,
        geography=str(geo.get("geographic_focus") or "") or None,
        industry_layer=industry_layer,
    )


async def research(client: Client, model: FakeModel) -> Research:
    """Phase 5's keyword research for one client, the way ``run_search_demand`` wires it:
    the industry terms, one plan per service plus the industry plan, one shared PoolContext
    (the client's own places, switched by ``keyword_pool_enabled``)."""
    geo = resolve_geographic_focus({"geographic_focus": client.focus})
    pool = PoolContext(places=tuple(client_places(geo)), enabled=bool(get_settings().keyword_pool_enabled))
    with real_llm():
        terms = await IndustryKeywordResolver(
            INDUSTRY,
            "Australia",
            client.services,
            location_code=geo["labs_location_code"],
            language_code=geo["language_code"],
            ask=model,
        ).resolve()
    pool.industry_key = terms.industry_key
    plans = [*[_plan(s) for s in client.services], industry_seed_plan(INDUSTRY, terms.terms)]
    result = await run_service_scoped_seeding(
        plans,
        fetch=run_multi_mode_seeding,
        total_cap=20,
        relevance_context_for=lambda plan: _context(client, geo, plan.seeds, industry_layer=plan.is_industry),
        service_concurrency=3,
        country=geo["country"],
        min_volume=10,
        limit_per_mode=40,
        location_code=geo["labs_location_code"],
        language_code=geo["language_code"],
        pool=pool,
    )
    return Research(client, result, pool, terms, geo)


@pytest.fixture
def provider(monkeypatch):
    """Live-provider mode with the provider faked, the pool on, clustering by rules.

    Live mode is what lets the pool work, so it also means any provider call that is not
    faked would go out for real. The network layer is therefore closed: a request to it is
    recorded, refused, and fails the test in the teardown.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "dataforseo_sandbox", False)
    monkeypatch.setattr(settings, "keyword_pool_enabled", True)
    monkeypatch.setattr(settings, "keyword_extra_idea_pulls", False)
    monkeypatch.setattr(settings, "keyword_clustering_use_model", False)
    monkeypatch.setattr(settings, "serp_clustering_enabled", False)
    monkeypatch.setattr(settings, "dataforseo_language_code", LANGUAGE)
    fake = FakeProvider()
    monkeypatch.setattr(keyword_seeding, "expand_seed_dataforseo", fake.expand)
    monkeypatch.setattr(dataforseo, "search_volume", fake.search_volume)

    async def refuse(self, request, **kwargs):
        fake.network.append(str(request.url))
        raise httpx.ConnectError("the end-to-end test makes no real provider call", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    yield fake
    assert fake.network == [], f"a real provider call was attempted: {fake.network}"


@dataclass
class Scenario:
    provider: FakeProvider
    model: FakeModel
    a: Research
    b: Research


@pytest_asyncio.fixture
async def scenario(db_session, provider) -> Scenario:
    model = FakeModel()
    provider.stage = "A"
    a = await research(CLIENT_A, model)
    provider.stage = "B"
    b = await research(CLIENT_B, model)
    return Scenario(provider, model, a, b)


# --------------------------------------------------------------------------- 1. the pool saves calls


async def test_client_b_fetches_only_the_services_the_pool_lacks(scenario):
    provider, a, b = scenario.provider, scenario.a, scenario.b

    # Client A had an empty pool: every seed (five services and two industry seeds) was fetched once.
    assert provider.fetched("A") == Counter(
        {s.lower(): 1 for s in CLIENT_A.services} | {"dental clinic": 1, "family dentist": 1}
    )
    assert a.pool.report.as_dict()["totals"]["outcomes"] == {"fetched": 7}

    # Client B fetched ONLY the two services nobody had researched, one provider pull per seed query.
    assert provider.fetched("B") == Counter({"wisdom teeth removal": 1, "emergency dentist": 1})
    outcomes = b.pool.report.as_dict()["totals"]["outcomes"]
    assert outcomes == {"reused": 5, "fetched": 2}
    by_service = b.pool.report.as_dict()["services"]
    for service in SHARED:
        assert by_service[service]["outcomes"] == {"reused": 1}, service
    for service in B_ONLY:
        assert by_service[service]["outcomes"] == {"fetched": 1}, service
    # The industry layer rides the same pool: B reused A's two industry seeds, and its terms.
    assert by_service[INDUSTRY]["outcomes"] == {"reused": 2}
    assert scenario.model.calls == 1, "the second dental client does not ask the model again"
    assert (a.terms.source, b.terms.source) == ("model", "pool")
    assert b.terms.terms == a.terms.terms == ["dental clinic", "family dentist"]

    # The place top-up: one batched lookup per reused seed, "<seed> <place>" for B's own places,
    # and none for a seed that was fetched.
    lookups = provider.place_lookups("B")
    assert sorted(c["keywords"][0].removesuffix(" sydney") for c in lookups) == sorted(
        [s.lower() for s in SHARED] + ["dental clinic", "family dentist"]
    )
    for call in lookups:
        seed = call["keywords"][0].removesuffix(" sydney")
        assert call["keywords"] == [f"{seed} sydney", f"{seed} new south wales"]
        assert call["location"] == LOCATION and call["language"] == LANGUAGE
    assert not [c for c in provider.place_lookups("B") if "wisdom" in c["keywords"][0] or "emergency" in c["keywords"][0]]

    # Avoided: three pulls per reused seed, less its one place lookup.
    totals = b.pool.report.as_dict()["totals"]
    assert totals["calls_avoided"] == 5 * (3 - 1)
    assert totals["place_topup_rows"] == len(PLACE_VOLUMES)
    assert b.result["provider_expansion_unavailable"] is False
    assert "service_seeding_failed" not in str(b.result["provider_errors"])


# --------------------------------------------------------------------------- 2. no place leaks


async def test_a_place_keyword_from_client_a_never_reaches_client_b_or_the_pool(scenario):
    a, b = scenario.a, scenario.b

    # A is a Melbourne client and keeps its own place keywords.
    assert "dental implants melbourne" in a.keywords
    a_places = {row["keyword"] for row in a.result["keyword_dataset"] if is_location_specific(row["keyword"])}
    assert {"dental implants melbourne", "teeth whitening near me", "dental clinic melbourne"} <= a_places

    # For a service the two share, B gets no place keyword from A: its only place keywords are
    # the ones the targeted lookup measured for B's own city.
    b_rows = b.result["keyword_dataset"]
    shared_seeds = {s.lower() for s in SHARED} | {"dental clinic", "family dentist"}
    shared_rows = [row for row in b_rows if row["seed"] in shared_seeds]
    assert shared_rows
    for row in shared_rows:
        assert "melbourne" not in row["keyword"] and "near me" not in row["keyword"], row["keyword"]
        if is_location_specific(row["keyword"]):
            assert row["keyword"] in PLACE_VOLUMES and row["ahrefs_endpoint"] == TOPUP_ENDPOINT, row["keyword"]
    for keyword in ("dental implants melbourne", "dental implants near me", "teeth whitening melbourne"):
        assert keyword not in b.keywords
    # B's two own pulls return whatever the provider surfaces, as before the pool existed.
    assert {r["seed"] for r in b_rows if "melbourne" in r["keyword"]} <= {s.lower() for s in B_ONLY}

    # B does get keywords for its own city, measured by the targeted lookup (and only those).
    topup = {row["keyword"]: row["volume"] for row in b_rows if row["ahrefs_endpoint"] == TOPUP_ENDPOINT}
    assert topup == PLACE_VOLUMES

    # The pool tables hold public, place-free keywords only; A's place keywords were never stored.
    async with AsyncSessionLocal() as session:
        saved = (await session.execute(select(KeywordPoolEntry.keyword))).scalars().all()
        fetches = (await session.execute(select(KeywordPoolFetch))).scalars().all()
    assert saved, "the pool holds the place-free keywords"
    assert "dental implants melbourne" not in saved
    assert not [k for k in saved if is_location_specific(k)], [k for k in saved if is_location_specific(k)]
    assert {"dental implants cost", "best dental implants", "dental clinic"} <= set(saved)
    # No client name, id or URL is stored with the pool.
    stored_text = " ".join(f"{f.seed_key} {f.seed_text} {f.industry_key}" for f in fetches).lower()
    for client in (CLIENT_A, CLIENT_B):
        assert client.domain.split(".")[0] not in stored_text


# --------------------------------------------------------------------------- 3. industry -> home row


def _site(client: Client) -> dict[str, Any]:
    root = f"https://{client.domain}"
    return {
        "pages": [
            {"url": f"{root}/", "title": f"{client.name} | Dental clinic", "h1": "Dental clinic"},
            {"url": f"{root}/dental-implants/", "title": "Dental implants", "h1": "Dental implants"},
        ]
    }


def _catalog(client: Client) -> list[dict[str, Any]]:
    return [
        {"id": s.lower().replace(" ", "-"), "name": s, "source": "website", "subservices": []}
        for s in client.services
    ]


async def _url_map(study: Research, **kwargs) -> tuple[dict[str, Any], dict[str, Any]]:
    """Cluster the client's seeded keywords and map the clusters to URLs, as Phases 5 and 6 do."""
    client = study.client
    clusters = await run_keyword_clustering(
        study.result["keyword_dataset"],
        seeds=study.result["seeds"],
        brand_name=client.name,
        domain=client.domain,
        relevance_context=_context(client, study.geo, study.result["seeds"]),
    )
    report = build_final_url_map(
        copy.deepcopy(clusters),
        website=_site(client),
        client_domain=client.domain,
        service_catalog=_catalog(client),
        **kwargs,
    )
    return clusters, report


async def test_the_industry_layer_reaches_the_home_row(scenario):
    clusters, report = await _url_map(scenario.b)
    entries = report["final_url_map"]

    homes = [e for e in entries if e.get("is_home")]
    assert len(homes) == 1
    home = homes[0]
    assert entries[0] is home
    assert home["current_url"] == "/" and home["selected_url"] == "/" and home["proposed_url"] is None
    assert report["home_page"]["source"] == "industry"

    # The primary is the highest-volume place-free industry keyword, reused from the pool.
    assert home["primary_keyword"] == "dental clinic"
    assert report["home_page"]["keyword"] == "dental clinic"
    assert not is_location_specific(home["primary_keyword"])
    layer = {
        row["keyword"]: row
        for row in scenario.b.result["keyword_dataset"]
        if row.get("seed_origin") == "industry"
    }
    assert layer["dental clinic"]["volume"] == 2400
    assert layer, "the seeded rows carry the industry tag"

    # The secondaries are the rest of the industry layer (and only it), by volume, none invented.
    secondaries = home["secondary_keywords"]
    assert secondaries and len(secondaries) <= SECONDARY_ROW_CAP
    assert set(secondaries) <= set(layer) - {"dental clinic"}
    assert {"best dental clinic", "family dentist"} <= set(secondaries)
    volumes = [layer[k]["volume"] for k in secondaries]
    assert volumes == sorted(volumes, reverse=True)
    # No service keyword is on the home page, and no industry keyword is a service row's primary.
    services_rows = [e for e in entries if not e.get("is_home")]
    assert not ({"dental clinic", "family dentist"} & {e["primary_keyword"] for e in services_rows})
    assert not [k for e in services_rows for k in e["secondary_keywords"] if k in layer and k != "dental clinic"]

    # The home row sits above the tree: no levels. The map is consistent.
    assert all(home.get(key) is None for key in LEVELS) and home["level"] == 0
    assert report["inconsistent_rows"] == [] and check_url_map_consistency(entries) == []

    # Every page-ready cluster is on exactly one row: as that row's page, folded into it as a
    # secondary keyword, or merged into the home row.
    for cluster in clusters["clusters"]:
        if not cluster_is_page_ready(cluster):
            continue
        keyword = cluster["primary_keyword"]
        owners = [e["cluster_id"] for e in entries if keyword == e["primary_keyword"] or keyword in e["secondary_keywords"]]
        assert len(owners) == 1, (keyword, owners)
    assert report["url_tree"]["violations"] == [] and report["url_tree"]["needs_review"] == []
    assert report["url_tree"]["placed"] == len(services_rows)


# --------------------------------------------------------------------------- 4. the URL tree


def _kw(keyword, volume, **extra):
    return {"keyword": keyword, "volume": volume, "intent": "commercial", "relevance_reason": "seed_phrase", **extra}


def _cluster(name, rows, intent="commercial"):
    primary = rows[0]["keyword"]
    members = [{**r, "role": "Primary" if r["keyword"] == primary else "Secondary"} for r in rows]
    return {"name": name, "primary_keyword": primary, "intent": intent, "keywords": members}


def _levels(row: dict[str, Any]) -> tuple:
    return tuple(row.get(key) for key in LEVELS)


SEO_PAGES = [
    {"url": "https://acme.example/", "title": "Acme | Digital Marketing Agency", "h1": "Digital marketing agency"},
    {"url": "https://acme.example/seo/", "title": "SEO Services", "h1": "SEO services"},
]


def _seo_clusters(*, link_in_cluster: bool) -> list[dict[str, Any]]:
    shopify = [_kw("shopify seo", 590)]
    if link_in_cluster:
        # The searchers combine Shopify and ecommerce SEO: the cluster's own keywords say so.
        shopify.append(_kw("shopify ecommerce seo", 40))
    return [
        _cluster(
            "Digital Marketing Agency",
            [
                _kw("digital marketing agency", 1300, seed_origin="industry"),
                _kw("online marketing company", 320, seed_origin="industry"),
            ],
        ),
        _cluster("SEO", [_kw("seo services", 2400), _kw("seo company", 500)]),
        _cluster("Ecommerce SEO", [_kw("ecommerce seo", 880), _kw("ecommerce seo services", 140)]),
        _cluster("Shopify SEO", shopify),
        _cluster("Shopify SEO Checklist", [_kw("shopify seo checklist", 170)], intent="informational"),
        _cluster("PPC Management", [_kw("ppc management", 700), _kw("ppc agency", 260)]),
    ]


NESTED_CATALOG = [
    {
        "id": "seo",
        "name": "SEO",
        "source": "website",
        "subservices": [
            {
                "id": "ecommerce-seo",
                "name": "Ecommerce SEO",
                "subservices": [{"id": "shopify-seo", "name": "Shopify SEO"}],
            }
        ],
    },
    {"id": "ppc-management", "name": "PPC Management", "source": "website", "subservices": []},
]
FLAT_CATALOG = [
    {
        "id": "seo",
        "name": "SEO",
        "source": "website",
        "subservices": [{"id": "ecommerce-seo", "name": "Ecommerce SEO"}],
    },
    {"id": "ppc-management", "name": "PPC Management", "source": "website", "subservices": []},
]


@pytest.mark.parametrize(
    "catalog, link_in_cluster",
    [
        pytest.param(NESTED_CATALOG, False, id="catalog-nests-shopify-seo"),
        pytest.param(FLAT_CATALOG, True, id="cluster-secondary-links-shopify-seo"),
    ],
)
def test_seo_ecommerce_seo_shopify_seo_builds_a_three_level_tree(catalog, link_in_cluster):
    clusters = _seo_clusters(link_in_cluster=link_in_cluster)
    report = build_final_url_map(
        {"clusters": copy.deepcopy(clusters)},
        website={"pages": SEO_PAGES},
        client_domain="acme.example",
        service_catalog=catalog,
    )
    entries = report["final_url_map"]
    rows = {e["primary_keyword"]: e for e in entries}
    levels = _levels

    assert levels(rows["seo services"]) == ("SEO", None, None, None)
    assert levels(rows["ecommerce seo"]) == ("SEO", "Ecommerce SEO", None, None)
    assert levels(rows["shopify seo"]) == ("SEO", "Ecommerce SEO", "Shopify SEO", None)
    assert [rows[k]["level"] for k in ("seo services", "ecommerce seo", "shopify seo")] == [1, 2, 3]
    # A standalone service stops at L1; an informational cluster sits under the service it is about.
    assert levels(rows["ppc management"]) == ("PPC Management", None, None, None)
    assert levels(rows["shopify seo checklist"])[:3] == ("SEO", "Ecommerce SEO", "Shopify SEO")
    # The home page is above the tree.
    home = next(e for e in entries if e.get("is_home"))
    assert levels(home) == (None, None, None, None) and home["level"] == 0

    # No gaps anywhere: an L4 has an L3, an L3 an L2, an L2 an L1; the level counts the filled ones.
    for row in entries:
        filled = [bool(row.get(key)) for key in LEVELS]
        assert filled == sorted(filled, reverse=True), (row["primary_keyword"], levels(row))
        assert row["level"] == sum(filled)
        if not row.get("is_home"):
            taxonomy = {k: row.get(k) for k in LEVELS}
            assert row["page_type"] == _sheet_page_type(taxonomy), row["primary_keyword"]

    # Every in-scope cluster is placed exactly once, and the QA check has nothing to report.
    tree = report["url_tree"]
    assert tree["violations"] == [] and tree["needs_review"] == []
    placed = Counter(e["cluster_id"] for e in entries if not e.get("is_home")) + Counter(home["merged_cluster_ids"])
    assert placed == Counter(stable_cluster_id(c) for c in clusters)
    assert tree["placed"] == len(entries) - 1
    assert check_url_map_consistency(entries) == [] and report["inconsistent_rows"] == []


# --------------------------------------------------------------------------- 5. secondary keywords


def _demand(client: Client) -> dict[str, Any]:
    """What Phase 5 stored for Phase 6 to read back."""
    return {
        "services": client.services,
        "cdd_keywords": [s.lower() for s in client.services],
        "geographic_focus": client.focus,
        "labs_location_code": LOCATION,
        "keyword_seeding": {"industry_keywords": {"industry": INDUSTRY}},
    }


def _measured(keyword: str) -> bool:
    """True when the fake provider has a volume of at least 10 for this modifier variant."""
    returned, volume = modifier_answer(keyword)
    return returned and volume is not None and volume >= 10


async def test_every_row_has_measured_secondary_keywords_or_is_empty_for_want_of_a_measured_variant(scenario):
    b = scenario.b
    _clusters, report = await _url_map(b)
    entries = report["final_url_map"]
    assert len(entries) > 10
    before = {e["cluster_id"]: (set(e), list(e["secondary_keywords_sheet"])) for e in entries}

    scenario.provider.stage = "P6"
    topup = await top_up_url_map_secondaries(
        report,
        demand=_demand(CLIENT_B),
        commercial={"geographic_focus": CLIENT_B.focus},
        marketing={},
        website=_site(CLIENT_B),
        client_name=CLIENT_B.name,
        domain=CLIENT_B.domain,
    )

    # One batched, place-free lookup at the location every other volume was measured at.
    (call,) = [c for c in scenario.provider.volume_calls if c["stage"] == "P6"]
    asked = call["keywords"]
    assert call["location"] == LOCATION and call["language"] == LANGUAGE
    assert asked and len(asked) == len(set(asked)) <= get_settings().keyword_secondary_lookup_cap
    assert not [k for k in asked if is_location_specific(k, b.pool.places)]
    assert topup["candidates"] == len(asked) and topup["lookup_errors"] == []
    assert topup["kept"] > 0 and topup["rows_topped_up"] > 0

    unmeasured = {k for k in asked if not _measured(k)}
    assert unmeasured, "the fake provider has variants with no volume, to prove they are dropped"
    shown = set()
    empty_rows = []
    for row in entries:
        old_keys, old_sheet = before[row["cluster_id"]]
        sheet, plain = row["secondary_keywords_sheet"], row["secondary_keywords"]
        shown |= set(sheet) | set(plain)

        # Plain strings in the two existing lists. No label, tag or new field says where one came from.
        assert all(type(item) is str for item in [*sheet, *plain])
        assert set(row) == old_keys
        assert len(sheet) <= SECONDARY_ROW_CAP and len(set(sheet)) == len(sheet)
        assert sheet[: len(old_sheet)] == old_sheet, "a row keeps what its cluster gave it, in order"

        # Every added variant is a keyword the provider measured at 10 or more searches.
        added = sheet[len(old_sheet) :]
        assert all(_measured(k) for k in added), (row["primary_keyword"], added)

        if not sheet:
            empty_rows.append(row)
    # A variant with no measured volume never reaches a row.
    assert not (unmeasured & shown), unmeasured & shown
    assert not [k for k in shown if k.endswith(" company")]

    # A row left empty is empty because none of its variants has a measured volume. (The one
    # other kind is an informational row: a "procedure" search is a question, not a page to
    # dress up with "best" and "company", so the top-up leaves it alone.)
    assert topup["empty_rows"] == len(empty_rows)
    no_variant = [r for r in empty_rows if r.get("search_intent") != "informational"]
    for row in empty_rows:
        if row.get("search_intent") == "informational":
            continue
        candidates = modifier_candidates(row["primary_keyword"], [], places=b.pool.places)
        assert not [c for c in candidates if _measured(c)], (row["primary_keyword"], candidates)
    assert "wisdom teeth removal cost" in {r["primary_keyword"] for r in no_variant}, (
        "the thin wisdom-teeth row has no measured variant, so it is empty"
    )

    # The rows that did gain keywords gained the busiest variants first, up to the cap.
    gained = [e for e in entries if len(e["secondary_keywords_sheet"]) > len(before[e["cluster_id"]][1])]
    assert gained
    for row in gained:
        added = row["secondary_keywords_sheet"][len(before[row["cluster_id"]][1]) :]
        volumes = [modifier_answer(k)[1] for k in added]
        assert volumes == sorted(volumes, reverse=True)


# --------------------------------------------------------------------------- 6. a pool outage


async def _phase_6_secondaries(study: Research) -> list[tuple[str, list[str]]]:
    """Map the client's clusters to URLs, top up the secondary keywords, return what each row shows."""
    _clusters, report = await _url_map(study)
    await top_up_url_map_secondaries(
        report,
        demand=_demand(study.client),
        commercial={"geographic_focus": study.client.focus},
        marketing={},
        website=_site(study.client),
        client_name=study.client.name,
        domain=study.client.domain,
    )
    return [(e["primary_keyword"], list(e["secondary_keywords_sheet"])) for e in report["final_url_map"]]


def _dataset(study: Research) -> list[tuple]:
    return sorted(
        (r["seed"], r["keyword"], r["volume"], r["match_class"], r.get("seed_origin") or "")
        for r in study.result["keyword_dataset"]
    )


@pytest.mark.parametrize("outage", ["tables_dropped", "database_unreachable"])
async def test_a_pool_outage_leaves_the_run_as_it_is_without_a_pool(db_session, provider, monkeypatch, outage):
    settings = get_settings()

    # An earlier client filled the pool, so a working pool WOULD serve five of B's seven seeds.
    provider.stage = "A"
    await research(CLIENT_A, FakeModel())

    # Then the pool goes away.
    if outage == "tables_dropped":
        async with engine.begin() as conn:
            await conn.run_sync(KeywordPoolEntry.__table__.drop)
            await conn.run_sync(KeywordPoolFetch.__table__.drop)
    else:
        from app.services.shared_keyword_pool import store

        def unreachable():
            raise ConnectionError("the pool database is unreachable")

        monkeypatch.setattr(store, "AsyncSessionLocal", unreachable)

    provider.stage = "outage"
    outage_model = FakeModel()
    down = await research(CLIENT_B, outage_model)
    down_secondaries = await _phase_6_secondaries(down)

    # The run without a pool: the pool switch off, the same client and the same provider.
    monkeypatch.setattr(settings, "keyword_pool_enabled", False)
    provider.stage = "off"
    off_model = FakeModel()
    off = await research(CLIENT_B, off_model)
    off_secondaries = await _phase_6_secondaries(off)

    # Same provider calls: every seed fetched once, nothing reused, no place lookup.
    everything = Counter({s.lower(): 1 for s in CLIENT_B.services} | {"dental clinic": 1, "family dentist": 1})
    assert provider.fetched("outage") == provider.fetched("off") == everything
    assert provider.place_lookups("outage") == provider.place_lookups("off") == []
    # Same keywords, volumes and tags.
    assert _dataset(down) == _dataset(off)
    # Same industry terms, from the model each time, as when no pool exists.
    assert (down.terms.source, off.terms.source) == ("model", "model")
    assert down.terms.terms == off.terms.terms
    assert (outage_model.calls, off_model.calls) == (1, 1)
    # Nothing failed and no error code was added by the pool.
    assert down.result["provider_errors"] == off.result["provider_errors"]
    assert [r["status"] for r in down.result["service_runs"]] == [r["status"] for r in off.result["service_runs"]]
    assert {r["status"] for r in down.result["service_runs"]} == {"ok"}
    assert down.result["provider_expansion_unavailable"] is False
    # The report says what happened, once per seed, and claims no saving.
    totals = down.pool.report.as_dict()["totals"]
    assert totals["outcomes"] == {"pool_error": 7} and totals["calls_avoided"] == 0
    assert off.pool.report.as_dict()["totals"]["seeds"] == 0
    # Phase 6 shows the same secondary keywords whether or not the pool could be reached.
    assert down_secondaries == off_secondaries
    assert any(secondaries for _primary, secondaries in down_secondaries)
