"""Seeds are generated per service and keywords are fetched per service.

    Service A -> generate seeds for A -> fetch keywords from ONLY A's seeds
    Service B -> generate seeds for B -> fetch keywords from ONLY B's seeds
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services import service_seeding as module
from app.services.keyword_clustering import build_service_seed_clusters
from app.services.keyword_seeding import flatten_dataset
from app.services.keyword_service_contract import partition_service_rejects
from app.services.service_scope import partition_scope_rejects
from app.services.service_seeding import (
    OTHER_SERVICE_LABEL,
    ServiceIsolationError,
    ServiceSeedPlan,
    allocate_seed_budget,
    flatten_service_plans,
    generate_service_seeds,
    plan_service_seeds,
    round_robin_seeds,
    run_service_scoped_seeding,
)

# --- helpers -----------------------------------------------------------------------


def _plan(service: str, seeds: list[str], **kw) -> ServiceSeedPlan:
    return ServiceSeedPlan(
        service=service,
        service_id=service.lower(),
        seeds=list(seeds),
        seed_targets={
            s.lower(): {"target": s, "target_type": "keyword", "service": service, "service_id": service.lower()}
            for s in seeds
        },
        **kw,
    )


class FakeFetch:
    """A stand-in for run_multi_mode_seeding that records exactly what it was given."""

    def __init__(self, *, fail_for: set[str] | None = None, delay: dict[str, float] | None = None):
        self.calls: list[dict] = []
        self.fail_for = fail_for or set()
        self.delay = delay or {}

    async def __call__(self, seeds, *, seed_targets=None, max_seeds=None, relevance_context=None, **kw):
        service = next(iter((seed_targets or {}).values()), {}).get("service", "?")
        self.calls.append(
            {
                "service": service,
                "seeds": list(seeds),
                "targets": dict(seed_targets or {}),
                "max_seeds": max_seeds,
                "ctx": relevance_context,
                "kw": kw,
            }
        )
        await asyncio.sleep(self.delay.get(service, 0))
        if service in self.fail_for:
            raise RuntimeError(f"provider down for {service}")
        clusters, dataset = [], []
        for seed in seeds:
            row = {"keyword": f"{seed} cost", "seed": seed, "match_class": "exact", "volume": 100}
            clusters.append(
                {
                    "seed": seed,
                    "target": seed,
                    "target_type": "keyword",
                    "exact": [dict(row)],
                    "phrase": [],
                    "related": [],
                    "broad": [],
                    "keyword_count": 1,
                }
            )
            dataset.append({**row, "target": seed, "target_type": "keyword"})
        return {
            "seeds": list(seeds),
            "seed_clusters": clusters,
            "keyword_dataset": dataset,
            "keyword_count": len(dataset),
            "class_counts": {"exact": len(dataset), "phrase": 0, "related": 0, "broad": 0},
            "target_type_counts": {"keyword": len(seeds)},
            "class_coverage": [],
            "seeds_with_all_classes": 0,
            "provider_errors": [],
            "providers": ["dataforseo"],
            "excluded_keywords": [],
            "keyword_cleaning": {},
        }


# --- the invariant, with deliberately distinct seeds ----------------------------------


async def test_each_service_is_fetched_from_only_its_own_seeds():
    a = _plan("Service A", ["apple-A", "banana-A"])
    b = _plan("Service B", ["apple-B", "banana-B"])
    fetch = FakeFetch()
    result = await run_service_scoped_seeding([a, b], fetch=fetch, total_cap=50)

    by_service = {c["service"]: c["seeds"] for c in fetch.calls}
    assert by_service["Service A"] == ["apple-A", "banana-A"]
    assert by_service["Service B"] == ["apple-B", "banana-B"]
    # never a combined list, in any call
    for call in fetch.calls:
        assert not ({"apple-A", "banana-A"} & set(call["seeds"]) and {"apple-B", "banana-B"} & set(call["seeds"]))
    assert len(fetch.calls) == 2

    # every keyword is attributed to the service whose seed produced it
    for row in result["keyword_dataset"]:
        suffix = row["seed"].rsplit("-", 1)[1]
        assert row["service"] == f"Service {suffix}"
    for cluster in result["seed_clusters"]:
        assert cluster["service"] == f"Service {cluster['seed'].rsplit('-', 1)[1]}"
    assert {r["service"] for r in result["service_runs"]} == {"Service A", "Service B"}


async def test_each_fetch_receives_only_its_own_seed_targets():
    a = _plan("Service A", ["apple-A", "banana-A"])
    b = _plan("Service B", ["apple-B", "banana-B"])
    fetch = FakeFetch()
    await run_service_scoped_seeding([a, b], fetch=fetch, total_cap=50)
    for call in fetch.calls:
        assert set(call["targets"]) == {s.lower() for s in call["seeds"]}
        assert {t["service"] for t in call["targets"].values()} == {call["service"]}


async def test_each_service_is_judged_against_its_own_relevance_context():
    a = _plan("Service A", ["apple-A"])
    b = _plan("Service B", ["apple-B"])
    fetch = FakeFetch()
    await run_service_scoped_seeding(
        [a, b], fetch=fetch, total_cap=50, relevance_context_for=lambda plan: f"ctx for {plan.service}"
    )
    assert {c["service"]: c["ctx"] for c in fetch.calls} == {
        "Service A": "ctx for Service A",
        "Service B": "ctx for Service B",
    }


async def test_fetch_kwargs_pass_through_to_every_service():
    fetch = FakeFetch()
    await run_service_scoped_seeding(
        [_plan("Service A", ["a1"]), _plan("Service B", ["b1"])],
        fetch=fetch,
        total_cap=10,
        country="au",
        min_volume=20,
        limit_per_mode=35,
        location_code=2036,
        language_code="en",
    )
    for call in fetch.calls:
        assert call["kw"] == {
            "country": "au",
            "min_volume": 20,
            "limit_per_mode": 35,
            "location_code": 2036,
            "language_code": "en",
        }


async def test_a_seed_text_two_services_both_own_is_kept_for_both():
    a = _plan("Service A", ["shared seed", "only-A"])
    b = _plan("Service B", ["shared seed", "only-B"])
    fetch = FakeFetch()
    result = await run_service_scoped_seeding([a, b], fetch=fetch, total_cap=50)
    assert [c["seeds"] for c in fetch.calls] == [["shared seed", "only-A"], ["shared seed", "only-B"]]
    shared = [r for r in result["keyword_dataset"] if r["seed"] == "shared seed"]
    assert {r["service"] for r in shared} == {"Service A", "Service B"}
    assert len(shared) == 2  # one per service, not collapsed into one


# --- zero seeds, failures ---------------------------------------------------------------


async def test_a_service_with_no_seeds_is_skipped_and_reported():
    a = _plan("Service A", ["apple-A"])
    empty = ServiceSeedPlan(service="Service C", service_id="service c", status="no_seeds")
    b = _plan("Service B", ["apple-B"])
    fetch = FakeFetch()
    result = await run_service_scoped_seeding([a, empty, b], fetch=fetch, total_cap=10)
    assert [c["service"] for c in fetch.calls] == ["Service A", "Service B"]
    runs = {r["service"]: r for r in result["service_runs"]}
    assert runs["Service C"]["status"] == "no_seeds" and runs["Service C"]["seed_count"] == 0
    assert runs["Service A"]["status"] == runs["Service B"]["status"] == "ok"


async def test_a_plan_that_is_planned_but_empty_is_reported_as_no_seeds():
    fetch = FakeFetch()
    empty = ServiceSeedPlan(service="Service C", service_id="service c", seeds=[])
    result = await run_service_scoped_seeding([_plan("Service A", ["a1"]), empty], fetch=fetch, total_cap=5)
    assert {r["service"]: r["status"] for r in result["service_runs"]}["Service C"] == "no_seeds"


async def test_one_service_failing_does_not_stop_the_others():
    a = _plan("Service A", ["apple-A"])
    b = _plan("Service B", ["apple-B"])
    c = _plan("Service C", ["apple-C"])
    fetch = FakeFetch(fail_for={"Service B"})
    result = await run_service_scoped_seeding([a, b, c], fetch=fetch, total_cap=10)
    assert {call["service"] for call in fetch.calls} == {"Service A", "Service B", "Service C"}
    assert {r["service"] for r in result["keyword_dataset"]} == {"Service A", "Service C"}
    runs = {r["service"]: r for r in result["service_runs"]}
    assert runs["Service B"]["status"] == "failed" and "provider down" in runs["Service B"]["error"]
    assert any(e.startswith("service_seeding_failed:Service B") for e in result["provider_errors"])


async def test_if_every_service_fails_the_error_surfaces_as_before():
    fetch = FakeFetch(fail_for={"Service A", "Service B"})
    with pytest.raises(RuntimeError, match="provider down"):
        await run_service_scoped_seeding(
            [_plan("Service A", ["a"]), _plan("Service B", ["b"])], fetch=fetch, total_cap=10
        )


async def test_seed_generation_failure_for_one_service_is_flagged_and_skipped():
    a = _plan("Service A", ["apple-A"])
    broken = ServiceSeedPlan(
        service="Service X", service_id="service x", status="seed_generation_failed", error="ValueError: bad"
    )
    result = await run_service_scoped_seeding([a, broken], fetch=FakeFetch(), total_cap=10)
    runs = {r["service"]: r for r in result["service_runs"]}
    assert runs["Service X"]["status"] == "seed_generation_failed"
    assert runs["Service A"]["status"] == "ok"
    assert any("Service X" in e for e in result["provider_errors"])


def test_plan_service_seeds_isolates_a_generation_failure():
    real = module.generate_service_seeds

    def flaky(service, **kw):
        if service == "Service B":
            raise ValueError("catalog row is corrupt")
        return real(service, **kw)

    pool = {"service a": {"target": "Service A", "target_type": "service"}, "service b": {"target": "Service B", "target_type": "service"}}
    with patch.object(module, "generate_service_seeds", flaky):
        plans = plan_service_seeds(
            ["Service A", "Service B"],
            pool_roots=["Service A", "Service B"],
            pool_targets=pool,
            catalog=[],
            prioritization={},
        )
    by = {p.service: p for p in plans}
    assert by["Service B"].status == "seed_generation_failed" and "corrupt" in by["Service B"].error
    assert by["Service B"].seeds == []
    assert by["Service A"].seeds == ["Service A"] and by["Service A"].status == "planned"


async def test_the_isolation_guard_rejects_a_result_from_another_services_seeds():
    class Leaky(FakeFetch):
        async def __call__(self, seeds, **kw):
            out = await super().__call__(seeds, **kw)
            out["seed_clusters"].append({"seed": "apple-B", "target": "apple-B", "exact": []})
            return out

    a = _plan("Service A", ["apple-A"])
    b = _plan("Service B", ["apple-B"])
    fetch = FakeFetch()
    # Service A's fetch smuggles in Service B's seed; Service B's is clean.
    async def mixed(seeds, **kw):
        out = await fetch(seeds, **kw)
        if seeds == ["apple-A"]:
            out["seed_clusters"].append({"seed": "apple-B", "target": "apple-B", "exact": []})
        return out

    result = await run_service_scoped_seeding([a, b], fetch=mixed, total_cap=10)
    runs = {r["service"]: r for r in result["service_runs"]}
    assert runs["Service A"]["status"] == "failed" and "not one of this service's seeds" in runs["Service A"]["error"]
    assert runs["Service B"]["status"] == "ok"
    assert {r["service"] for r in result["keyword_dataset"]} == {"Service B"}  # A's leaky result discarded
    assert issubclass(ServiceIsolationError, RuntimeError)


# --- concurrency -----------------------------------------------------------------------


async def test_concurrent_services_stay_isolated_and_the_output_order_is_stable():
    plans = [_plan(f"Service {c}", [f"apple-{c}", f"banana-{c}"]) for c in "ABCD"]
    delay = {"Service A": 0.08, "Service B": 0.01, "Service C": 0.05, "Service D": 0.0}
    fetch = FakeFetch(delay=delay)
    result = await run_service_scoped_seeding(plans, fetch=fetch, total_cap=100, service_concurrency=4)
    for call in fetch.calls:
        letter = call["service"][-1]
        assert call["seeds"] == [f"apple-{letter}", f"banana-{letter}"]
    # merged in plan order regardless of which task finished first
    assert [r["service"] for r in result["service_runs"]] == [p.service for p in plans]
    assert [c["seed"] for c in result["seed_clusters"]][:2] == ["apple-A", "banana-A"]


async def test_default_runs_services_one_at_a_time():
    active = 0
    peak = 0

    async def fetch(seeds, **kw):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return await FakeFetch()(seeds, **kw)

    await run_service_scoped_seeding(
        [_plan(f"Service {c}", [f"s-{c}"]) for c in "ABC"], fetch=fetch, total_cap=10
    )
    assert peak == 1


# --- the shared budget -------------------------------------------------------------------


def test_budget_gives_every_service_a_share():
    assert allocate_seed_budget([5, 5, 5], 6) == [2, 2, 2]
    assert allocate_seed_budget([1, 10, 10], 9) == [1, 4, 4]  # a small service hands back its share
    assert allocate_seed_budget([10, 10, 10, 10], 4) == [1, 1, 1, 1]
    assert allocate_seed_budget([10, 10, 10], 7) == [3, 2, 2]  # leftover to earlier services


def test_budget_never_starves_a_later_service_even_when_it_is_over_subscribed():
    assert allocate_seed_budget([10, 10, 10, 10], 2) == [1, 1, 1, 1]
    assert allocate_seed_budget([0, 4, 0, 4], 2) == [0, 1, 0, 1]
    assert allocate_seed_budget([], 5) == []
    assert allocate_seed_budget([3], 100) == [3]


async def test_the_cap_applies_per_service_and_keeps_the_service_root_first():
    plans = [_plan(f"Service {c}", [f"root-{c}", f"extra-{c}1", f"extra-{c}2"]) for c in "ABC"]
    fetch = FakeFetch()
    await run_service_scoped_seeding(plans, fetch=fetch, total_cap=3)
    assert [c["seeds"] for c in fetch.calls] == [["root-A"], ["root-B"], ["root-C"]]


def test_round_robin_spreads_a_cap_across_services():
    runs = [
        {"service": "A", "seeds": ["a1", "a2", "a3"]},
        {"service": "B", "seeds": ["b1", "b2"]},
        {"service": "C", "seeds": ["c1"]},
    ]
    assert round_robin_seeds(runs, 5) == [("A", "a1"), ("B", "b1"), ("C", "c1"), ("A", "a2"), ("B", "b2")]
    assert round_robin_seeds(runs, 99)[-1] == ("A", "a3")


# --- generating a service's seeds --------------------------------------------------------

POOL = {
    "seo": {"target": "SEO", "target_type": "service"},
    "meta ads": {"target": "Meta Ads", "target_type": "service"},
    "local seo": {"target": "Local SEO", "target_type": "sub_service", "parent_segment": "seo"},
    "seo melbourne": {"target": "seo melbourne", "target_type": "keyword"},
    "meta ads agency": {"target": "meta ads agency", "target_type": "keyword"},
    "facebook ads cost": {"target": "facebook ads cost", "target_type": "keyword"},
    "bridging loans": {"target": "bridging loans", "target_type": "keyword"},
}
POOL_ROOTS = ["SEO", "Meta Ads", "Local SEO", "seo melbourne", "meta ads agency", "facebook ads cost", "bridging loans"]
CATALOG = [
    {
        "id": "seo",
        "name": "SEO",
        "subservices": [
            {"id": "local seo", "name": "Local SEO"},
            {"id": "technical seo", "name": "Technical SEO"},
            {"id": "link building", "name": "Link Building"},
        ],
    },
    {
        "id": "meta ads",
        "name": "Meta Ads",
        "subservices": [{"id": "instagram ads", "name": "Instagram Ads"}, {"id": "tiktok ads", "name": "TikTok Ads"}],
    },
]
PRIO = {
    "confirmed_at": "x",
    "selected_service_ids": ["seo", "meta ads"],
    "selected_subservice_ids": [],
    "service_scope": {
        "seo": {"name": "SEO", "offered": ["Local SEO", "Technical SEO"], "excluded": ["Link Building"]},
        "meta ads": {"name": "Meta Ads", "offered": ["Instagram Ads"], "excluded": ["TikTok Ads"]},
    },
}


def _gen(service):
    return generate_service_seeds(
        service,
        pool_roots=POOL_ROOTS,
        pool_targets=POOL,
        catalog=CATALOG,
        prioritization=PRIO,
        other_service_names=["SEO", "Meta Ads"],
    )


def test_a_services_seeds_come_only_from_that_service():
    seo, meta = _gen("SEO"), _gen("Meta Ads")
    assert seo.seeds == ["SEO"]
    assert "seo melbourne" not in seo.seeds
    assert "Local SEO" not in seo.seeds
    assert "Technical SEO" not in seo.seeds
    assert "Link Building" not in seo.seeds
    assert meta.seeds == ["Meta Ads"]
    assert "meta ads agency" not in meta.seeds
    assert "facebook ads cost" not in meta.seeds
    assert "Instagram Ads" not in meta.seeds
    assert "TikTok Ads" not in meta.seeds
    # nothing crosses
    assert not ({"Meta Ads", "Instagram Ads", "meta ads agency", "facebook ads cost"} & set(seo.seeds))
    assert not ({"SEO", "Local SEO", "Technical SEO", "seo melbourne"} & set(meta.seeds))


def test_every_seed_remembers_its_service():
    for service in ("SEO", "Meta Ads"):
        plan = _gen(service)
        assert plan.seed_targets, "a plan must carry its seed targets"
        assert {t["service"] for t in plan.seed_targets.values()} == {service}
        assert set(plan.seed_targets) == {s.lower() for s in plan.seeds}


def test_a_keyword_that_names_two_services_is_owned_by_both_not_dropped():
    pool = {**POOL, "seo and meta ads agency": {"target": "seo and meta ads agency", "target_type": "keyword"}}
    roots = [*POOL_ROOTS, "seo and meta ads agency"]
    kw = {
        "pool_roots": roots,
        "pool_targets": pool,
        "catalog": CATALOG,
        "prioritization": PRIO,
        "other_service_names": ["SEO", "Meta Ads"],
    }
    assert "seo and meta ads agency" not in generate_service_seeds("SEO", **kw).seeds
    assert "seo and meta ads agency" not in generate_service_seeds("Meta Ads", **kw).seeds


def test_another_services_own_name_is_never_claimed_by_a_service_that_shares_a_word():
    pool = {
        "seo": {"target": "SEO", "target_type": "service"},
        "local seo melbourne": {"target": "Local SEO Melbourne", "target_type": "service"},
        "local seo melbourne keyword": {"target": "Local SEO Melbourne", "target_type": "keyword"},
    }
    roots = ["SEO", "Local SEO Melbourne", "Local SEO Melbourne"]
    seo = generate_service_seeds(
        "SEO", pool_roots=roots[:2], pool_targets=pool, catalog=[], prioritization={},
        other_service_names=["SEO", "Local SEO Melbourne"],
    )
    assert "Local SEO Melbourne" not in seo.seeds
    local = generate_service_seeds(
        "Local SEO Melbourne", pool_roots=roots[:2], pool_targets=pool, catalog=[], prioritization={},
        other_service_names=["SEO", "Local SEO Melbourne"],
    )
    assert local.seeds == ["Local SEO Melbourne"]


def test_a_service_that_yields_nothing_is_reported_as_no_seeds():
    plan = generate_service_seeds(
        "SEO", pool_roots=[], pool_targets={}, catalog=[], prioritization={}, is_brand=lambda _t: True
    )
    assert plan.seeds == [] and plan.status == "no_seeds"


def test_plan_service_seeds_covers_each_service_and_keeps_unowned_seeds_apart():
    plans = plan_service_seeds(
        ["SEO", "Meta Ads"],
        pool_roots=POOL_ROOTS,
        pool_targets=POOL,
        catalog=CATALOG,
        prioritization=PRIO,
    )
    by = {p.service: p for p in plans}
    assert set(by) == {"SEO", "Meta Ads"}
    assert by["SEO"].seeds == ["SEO"]
    assert by["Meta Ads"].seeds == ["Meta Ads"]
    assert "bridging loans" not in by["SEO"].seeds + by["Meta Ads"].seeds


def test_the_priority_service_is_planned_first():
    plans = plan_service_seeds(
        ["SEO", "Meta Ads"], pool_roots=POOL_ROOTS, pool_targets=POOL, catalog=CATALOG,
        prioritization=PRIO, top_service="meta ads",
    )
    assert [p.service for p in plans][:2] == ["Meta Ads", "SEO"]


def test_flatten_is_only_a_reporting_union():
    plans = plan_service_seeds(["SEO", "Meta Ads"], pool_roots=POOL_ROOTS, pool_targets=POOL, catalog=CATALOG, prioritization=PRIO)
    roots, targets = flatten_service_plans(plans)
    assert len(roots) == len({r.lower() for r in roots})
    assert set(targets) == {r.lower() for r in roots}


# --- using the real keyword fetcher ---------------------------------------------------------


async def test_the_real_fetcher_only_ever_expands_the_seeds_of_the_service_it_was_called_for():
    from app.services import keyword_seeding

    expanded: list[str] = []

    async def fake_expand(seed, **kw):
        expanded.append(seed)
        return (
            [
                {"keyword": seed, "seed": seed, "volume": 500, "match_class": "exact", "source": "dataforseo"},
                {"keyword": f"{seed} near me", "seed": seed, "volume": 300, "match_class": "phrase", "source": "dataforseo"},
            ],
            [],
        )

    a = _plan("Service A", ["apple-A", "banana-A"])
    b = _plan("Service B", ["apple-B", "banana-B"])
    with (
        patch.object(keyword_seeding, "expand_seed", fake_expand),
        patch.object(keyword_seeding.dataforseo, "search_volume", AsyncMock(return_value=([], []))),
    ):
        result = await run_service_scoped_seeding(
            [a, b], fetch=keyword_seeding.run_multi_mode_seeding, total_cap=50,
            location_code=2036, min_volume=10,
        )
    assert sorted(expanded) == ["apple-A", "apple-B", "banana-A", "banana-B"]
    for cluster in result["seed_clusters"]:
        letter = cluster["seed"][-1]
        assert cluster["service"] == f"Service {letter}"
        for cls in ("exact", "phrase"):
            for row in cluster[cls]:
                assert row["keyword"].startswith(cluster["seed"])  # only from its own seed
    flat = flatten_dataset(result["seed_clusters"])
    assert {r["service"] for r in flat} == {"Service A", "Service B"}
    assert all(r["keyword"].split()[0][-1] == r["service"][-1] for r in flat)


# --- the service survives into clustering and the gates --------------------------------------


def test_a_seed_keeps_the_service_that_generated_it_when_clusters_are_grouped():
    clusters = [
        {"seed": "seo tips", "target": "seo tips", "target_type": "keyword", "service": "PPC Management",
         "service_id": "ppc management", "exact": [{"keyword": "seo tips", "volume": 10}], "phrase": [], "related": [], "broad": []},
        {"seed": "ppc agency", "target": "ppc agency", "target_type": "keyword", "service": "SEO",
         "service_id": "seo", "exact": [{"keyword": "ppc agency", "volume": 10}], "phrase": [], "related": [], "broad": []},
    ]
    groups = build_service_seed_clusters(clusters, ["SEO", "PPC Management"])
    where = {s["seed"]: g["service"] for g in groups for s in g["seeds"]}
    # token overlap alone would have put these under the opposite services
    assert where == {"seo tips": "PPC Management", "ppc agency": "SEO"}
    assert {s["assignment_reason"] for g in groups for s in g["seeds"]} == {"assigned_service"}


def test_unowned_seeds_group_under_other_website_topics():
    clusters = [{"seed": "bridging loans", "target": "bridging loans", "target_type": "keyword",
                 "service": OTHER_SERVICE_LABEL, "service_id": "_other",
                 "exact": [{"keyword": "bridging loans", "volume": 10}], "phrase": [], "related": [], "broad": []}]
    groups = build_service_seed_clusters(clusters, ["SEO"])
    assert [g["service"] for g in groups if g["seeds"]] == [OTHER_SERVICE_LABEL]


def test_clusters_without_a_service_still_use_the_old_token_overlap_assignment():
    clusters = [{"seed": "seo melbourne", "target": "seo melbourne", "target_type": "keyword",
                 "exact": [{"keyword": "seo melbourne", "volume": 10}], "phrase": [], "related": [], "broad": []}]
    groups = build_service_seed_clusters(clusters, ["SEO", "Meta Ads"])
    assert [g["service"] for g in groups if g["seeds"]] == ["SEO"]


def test_flatten_keeps_the_same_seed_under_two_services():
    def cluster(service):
        return {"seed": "shared", "target": "shared", "target_type": "keyword", "service": service,
                "service_id": service.lower(), "exact": [{"keyword": "shared cost", "volume": 5}],
                "phrase": [], "related": [], "broad": []}

    flat = flatten_dataset([cluster("Service A"), cluster("Service B")])
    assert sorted(r["service"] for r in flat) == ["Service A", "Service B"]


def test_scope_enforcement_follows_the_owning_service_not_the_seed_text():
    prio = {"service_scope": {"social media marketing": {
        "name": "Social Media Marketing", "offered": ["Meta Ads"], "excluded": ["TikTok Ads"]}}}
    rows = [
        # a plain business keyword: its seed text names no service, but its OWNER does
        {"keyword": "tiktok ads agency", "target": "social strategy", "seed": "social strategy",
         "service": "Social Media Marketing"},
        {"keyword": "meta ads agency", "target": "social strategy", "seed": "social strategy",
         "service": "Social Media Marketing"},
    ]
    kept, dropped = partition_scope_rejects(rows, prio)
    assert [r["keyword"] for r in kept] == ["meta ads agency"]
    assert [r["keyword"] for r in dropped] == ["tiktok ads agency"]
    # without the owner the same rows slip through (the old behaviour)
    legacy = [{k: v for k, v in r.items() if k != "service"} for r in rows]
    assert len(partition_scope_rejects(legacy, prio)[1]) == 0


def test_the_service_contract_judges_a_keyword_against_its_owning_service():
    rows = [{"keyword": "google ai studio", "target": "ai tools", "seed": "ai tools", "service": "Google Ads/PPC Management"}]
    _kept, dropped = partition_service_rejects(rows)
    assert dropped and dropped[0]["originating_service"] == "Google Ads/PPC Management"
