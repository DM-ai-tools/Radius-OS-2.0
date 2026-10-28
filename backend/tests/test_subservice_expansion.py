"""Model-suggested sub-service seeds: each selected service is widened beyond the few
sub-services its catalog lists, without breaking scope, isolation, budget or relevance."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.keyword_relevance import build_relevance_context, evaluate_keyword
from app.services.service_seeding import (
    CATALOG_SUB_ORIGIN,
    EXPANSION_ORIGIN,
    generate_service_seeds,
    plan_service_seeds,
    related_seed_pairs,
    run_service_scoped_seeding,
    sub_service_report,
    subservice_context,
)
from app.services.subservice_expansion import (
    MAX_SUBSERVICES,
    SubserviceExpansionResolver,
    audit_entries,
    clean_subservices,
    operator_notice,
)
from app.services.subservice_expansion import generator
from app.services.subservice_expansion.prompts import EXAMPLES, SYSTEM_PROMPT, build_user_prompt


class FakeModel:
    """Answers per service and records every call."""

    def __init__(self, answers: dict[str, list[str]], *, delay: float = 0.0, fail: bool = False):
        self.answers = answers
        self.delay = delay
        self.fail = fail
        self.calls: list[dict] = []

    async def __call__(self, service, **kw):
        self.calls.append({"service": service, **kw})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("model down")
        return {"sub_services": self.answers.get(service, [])}


CATALOG = [
    {
        "id": "seo",
        "name": "SEO",
        "subservices": [
            {"id": "local seo", "name": "Local SEO"},
            {"id": "link building", "name": "Link Building"},
        ],
    },
    {
        "id": "meta ads",
        "name": "Meta Ads",
        "subservices": [
            {"id": "instagram ads", "name": "Instagram Ads"},
            {"id": "tiktok ads", "name": "TikTok Ads"},
        ],
    },
    {"id": "email marketing", "name": "Email Marketing", "subservices": []},
]
PRIORITIZATION = {
    "confirmed_at": "2026-10-01T00:00:00+00:00",
    "selected_service_ids": ["seo", "meta ads", "email marketing"],
    "service_catalog": CATALOG,
    "service_scope": {
        "seo": {"name": "SEO", "offered": ["Local SEO"], "excluded": ["Link Building"]},
        "meta ads": {"name": "Meta Ads", "offered": ["Instagram Ads"], "excluded": ["TikTok Ads"]},
    },
}


# ------------------------------------------------------------------ cleaning


def test_cleaning_keeps_real_subtopics_and_drops_the_rest():
    out = clean_subservices(
        "SEO",
        [
            "Technical SEO",
            "seo",  # the service itself
            "seo services",  # the service plus a filler word
            "local seo",  # already a sub-service
            "local seos",  # its plural
            "what is technical seo",  # a question
            "seo audit",
            "  technical seo ",  # repeat
            "seo for small local businesses in melbourne",  # over 5 words
            "keyword research",
        ],
        existing=["Local SEO"],
    )
    assert out == ["technical seo", "seo audit", "keyword research"]


def test_cleaning_caps_at_ten():
    phrases = [f"topic {chr(97 + i)}" for i in range(14)]
    assert len(clean_subservices("SEO", phrases)) == MAX_SUBSERVICES == 10


# ------------------------------------------------------------------ resolver


async def test_one_call_per_service_per_run_even_when_asked_concurrently():
    model = FakeModel({"SEO": ["technical seo", "seo audit"]}, delay=0.05)
    resolver = SubserviceExpansionResolver(ask=model)
    first, second, third = await asyncio.gather(
        resolver.expand("SEO"), resolver.expand("seo "), resolver.expand("SEO")
    )
    assert first.terms == ("technical seo", "seo audit")
    assert second.terms == third.terms == first.terms
    assert second.cached and third.cached
    assert len(model.calls) == 1 and resolver.llm_calls == 1
    again = await resolver.expand("SEO")
    assert again.cached and len(model.calls) == 1


async def test_the_question_carries_the_business_and_scope_context():
    model = FakeModel({"SEO": ["technical seo"]})
    existing, excluded = subservice_context("SEO", CATALOG, PRIORITIZATION)
    assert existing == ["Local SEO", "Link Building"] and excluded == ["Link Building"]
    resolver = SubserviceExpansionResolver(
        industry="digital marketing agency",
        market="Australia",
        existing={"SEO": existing},
        excluded={"SEO": excluded},
        other_services=["SEO", "Meta Ads"],
        ask=model,
    )
    await resolver.expand("SEO")
    call = model.calls[0]
    assert call["industry"] == "digital marketing agency" and call["market"] == "Australia"
    assert call["existing"] == ["Local SEO", "Link Building"]
    assert call["excluded"] == ["Link Building"]
    user = build_user_prompt("SEO", **{k: v for k, v in call.items() if k != "service"})
    assert "Sub-services it already has (do not repeat): Local SEO, Link Building" in user
    assert "does NOT offer (never suggest): Link Building" in user
    assert "Other services this business sells (not sub-topics of this one): Meta Ads" in user


@pytest.mark.parametrize(
    ("model", "status"),
    [
        (FakeModel({}, fail=True), "llm_failed"),
        (AsyncMock(return_value={"something_else": []}), "invalid_reply"),
        (FakeModel({"SEO": ["seo", "seo services"]}), "no_subservices"),
    ],
)
async def test_a_bad_answer_never_fails_the_run(model, status):
    with patch.object(generator, "_provider_ready", return_value=True):
        result = await SubserviceExpansionResolver(ask=model).expand("SEO")
    assert result.status == status and result.terms == ()


async def test_a_slow_model_times_out_and_is_not_waited_on_twice():
    model = FakeModel({"SEO": ["technical seo"]}, delay=1.0)
    resolver = SubserviceExpansionResolver(ask=model, timeout=0.05)
    assert (await resolver.expand("SEO")).status == "timeout"
    assert (await resolver.expand("SEO")).cached
    assert len(model.calls) == 1


async def test_a_limit_of_zero_turns_the_step_off():
    model = FakeModel({"SEO": ["technical seo"]})
    results = await SubserviceExpansionResolver(ask=model, limit=0).resolve_all(["SEO"])
    assert results["SEO"].status == "disabled" and not model.calls


async def test_the_real_llm_call_gets_the_prompt():
    llm = AsyncMock(return_value={"sub_services": ["technical seo", "seo audit"]})
    with patch.object(generator, "synthesize_json", llm):
        result = await SubserviceExpansionResolver(industry="agency").expand("SEO")
    assert result.terms == ("technical seo", "seo audit")
    system, user = llm.await_args.args
    assert system == SYSTEM_PROMPT and "Service: SEO" in user
    assert llm.await_args.kwargs["raise_on_error"] is True


def test_the_prompt_teaches_the_pattern_and_the_limits():
    assert len(EXAMPLES) == 8
    for service, phrases in EXAMPLES:
        assert f"{service} -> {', '.join(phrases)}" in SYSTEM_PROMPT
    assert dict(EXAMPLES)["SEO"][:3] == ("technical seo", "local seo", "ecommerce seo")
    for needle in (
        "specialisations, delivery formats, audience segments and topic verticals",
        "Never another name or synonym for the service itself",
        "never suggest one the business has said it does not offer",
        "Return 5 to 10 topics",
        "never more than 10",
        "at most 5 words",
        "any service in any",
    ):
        assert needle in SYSTEM_PROMPT, needle


# ------------------------------------------------------------------ seeding


def _plan(service, subtopics, *, pool=None, max_expansions=10):
    roots = list(pool or [])
    targets = {r.lower(): {"target": r, "target_type": "keyword"} for r in roots}
    return generate_service_seeds(
        service,
        pool_roots=roots,
        pool_targets=targets,
        catalog=CATALOG,
        prioritization=PRIORITIZATION,
        other_service_names=["SEO", "Meta Ads", "Email Marketing"],
        subtopics=subtopics,
        max_expansions=max_expansions,
    )


def test_topics_become_sub_service_seeds_after_the_catalog_ones():
    plan = _plan("SEO", ["technical seo", "seo audit"], pool=["seo melbourne"])
    # Root, then the catalog's offered sub-service, then the model's topics.
    assert plan.seeds == ["SEO", "Local SEO", "technical seo", "seo audit"]
    assert plan.catalog_subs_added == ["Local SEO"]
    assert plan.expansions_added == ["technical seo", "seo audit"]
    for seed, origin in (("local seo", CATALOG_SUB_ORIGIN), ("technical seo", EXPANSION_ORIGIN)):
        meta = plan.seed_targets[seed]
        assert meta["target_type"] == "sub_service"
        assert meta["parent_segment"] == "SEO" and meta["parent_service"] == "SEO"
        assert meta["seed_origin"] == origin
        assert meta["service"] == "SEO"
    assert "seo melbourne" not in {s.lower() for s in plan.seeds}
    # Excluded by the operator: never seeded, recorded as excluded.
    assert "link building" not in {s.lower() for s in plan.seeds}
    assert {"phrase": "Link Building", "reason": "excluded_by_operator"} in plan.catalog_subs_skipped


def test_topics_skip_catalog_scope_duplicates_and_other_services():
    plan = _plan(
        "SEO",
        ["link building", "instagram ads", "local seo", "seo melbourne", "meta ads", "technical seo"],
        pool=["seo melbourne"],
    )
    assert plan.expansions_added == ["technical seo"]
    reasons = {s["phrase"]: s["reason"] for s in plan.expansions_skipped}
    assert reasons == {
        "link building": "duplicate_of_catalog",  # the catalog's version wins (and is excluded)
        "instagram ads": "another_service_sub_service",
        "local seo": "duplicate_of_catalog",
        "seo melbourne": "location_qualifier",
        "meta ads": "another_service",
    }
    # The parent's own seeds are unchanged: the service name only.
    assert [s for s in plan.seeds if not plan.is_sub_service(s)] == ["SEO"]


def test_an_excluded_sub_service_is_never_brought_back():
    plan = _plan("Meta Ads", ["tiktok marketing", "facebook retargeting", "lookalike audiences"])
    assert not any("tiktok" in s.lower() for s in plan.seeds)
    assert {"phrase": "tiktok marketing", "reason": "excluded_by_operator"} in plan.expansions_skipped
    assert {"phrase": "TikTok Ads", "reason": "excluded_by_operator"} in plan.catalog_subs_skipped
    assert plan.seeds == ["Meta Ads", "Instagram Ads", "facebook retargeting", "lookalike audiences"]


def test_a_service_with_no_catalog_sub_services_gets_the_models_topics():
    plan = _plan("Email Marketing", ["welcome sequence", "abandoned cart emails", "klaviyo setup"])
    assert plan.seeds == ["Email Marketing", "welcome sequence", "abandoned cart emails", "klaviyo setup"]
    assert plan.catalog_subs_added == []


def test_the_cap_limits_how_many_model_topics_are_seeded():
    plan = _plan("Email Marketing", ["a b", "c d", "e f", "g h"], max_expansions=2)
    assert plan.expansions_added == ["a b", "c d"]
    assert plan.seeds == ["Email Marketing", "a b", "c d"]
    assert [s["reason"] for s in plan.expansions_skipped] == ["over_limit", "over_limit"]


def test_a_sub_service_is_seeded_under_one_parent_only():
    catalog = [
        {"id": "seo", "name": "SEO", "subservices": []},
        {"id": "content marketing", "name": "Content Marketing", "subservices": []},
    ]
    plans = plan_service_seeds(
        ["SEO", "Content Marketing"],
        pool_roots=[],
        pool_targets={},
        catalog=catalog,
        prioritization={},
        service_subtopics={"SEO": ["content audits"], "Content Marketing": ["content audits", "blog writing"]},
    )
    by_service = {p.service: p for p in plans}
    assert by_service["SEO"].expansions_added == ["content audits"]
    assert by_service["Content Marketing"].expansions_added == ["blog writing"]
    assert {"phrase": "content audits", "reason": "seeded_under:SEO"} in by_service["Content Marketing"].expansions_skipped


def test_plan_service_seeds_routes_topics_to_their_own_service():
    plans = plan_service_seeds(
        ["SEO", "Email Marketing"],
        pool_roots=[],
        pool_targets={},
        catalog=CATALOG,
        prioritization=PRIORITIZATION,
        service_subtopics={"SEO": ["technical seo"], "Email Marketing": ["welcome sequence"]},
    )
    by_service = {p.service: p for p in plans}
    assert by_service["SEO"].expansions_added == ["technical seo"]
    assert by_service["Email Marketing"].expansions_added == ["welcome sequence"]
    assert "welcome sequence" not in {s.lower() for s in by_service["SEO"].seeds}
    assert "technical seo" not in {s.lower() for s in by_service["Email Marketing"].seeds}


async def test_sub_service_seeds_are_fetched_on_top_of_the_budget_and_attributed():
    # SEO's own share is one seed; every sub-service seed must still be fetched.
    plan = _plan("SEO", ["technical seo", "seo audit"])
    fetched: list[list[str]] = []

    async def fetch(seeds, **kw):
        fetched.append(list(seeds))
        dataset = [
            {"keyword": f"{s} melbourne", "seed": s}
            for s in seeds
            if s.lower() != "seo audit"  # this one returns nothing
        ]
        return {"seed_clusters": [{"seed": s} for s in seeds], "keyword_dataset": dataset}

    merged = await run_service_scoped_seeding([plan], fetch=fetch, total_cap=1)
    assert fetched[0] == ["SEO", "Local SEO", "technical seo", "seo audit"]
    rows = {r["keyword"]: r for r in merged["keyword_dataset"]}
    assert "sub_service" not in rows["SEO melbourne"]
    assert rows["Local SEO melbourne"]["sub_service"] == "Local SEO"
    assert rows["Local SEO melbourne"]["sub_service_origin"] == "catalog"
    assert rows["technical seo melbourne"]["sub_service"] == "technical seo"
    assert rows["technical seo melbourne"]["sub_service_origin"] == "model"
    assert rows["technical seo melbourne"]["service"] == "SEO"
    run = merged["service_runs"][0]
    report = {r["seed"]: r for r in run["sub_service_seeds"]}
    assert report["Local SEO"]["status"] == "fetched" and report["Local SEO"]["keyword_count"] == 1
    assert report["seo audit"]["status"] == "fetched_no_keywords"
    assert run["sub_service_counts"] == {"catalog": 1, "model": 2, "skipped": 1}


def test_a_sub_service_not_fetched_is_reported_as_dropped_for_budget():
    plan = _plan("SEO", ["technical seo"])
    report = sub_service_report(plan, ["SEO", "Local SEO"], {"keyword_dataset": []})
    by_seed = {r["seed"]: r["status"] for r in report}
    assert by_seed == {"Local SEO": "fetched_no_keywords", "technical seo": "dropped_for_budget"}


def test_related_lookups_add_every_sub_service_seed_on_top_of_the_cap():
    plans = [_plan("SEO", ["technical seo"]), _plan("Email Marketing", ["welcome sequence"])]
    runs = [{"service": p.service, "seeds": p.seeds} for p in plans]
    pairs = related_seed_pairs(runs, plans, 2)
    assert pairs[:2] == [("SEO", "SEO"), ("Email Marketing", "Email Marketing")]
    assert set(pairs[2:]) == {("SEO", "Local SEO"), ("SEO", "technical seo"), ("Email Marketing", "welcome sequence")}


# ------------------------------------------------------------------ relevance


def test_keywords_from_a_topic_seed_are_kept_only_when_the_topic_is_known():
    def ctx(subtopics):
        return build_relevance_context(
            services=["Email Marketing"],
            cdd_keywords=[],
            page_terms=[],
            themes=[],
            seeds=["welcome sequence"],
            service_subtopics=subtopics,
        )

    keyword = "welcome sequence for new subscribers"
    keep, reason, _ = evaluate_keyword(keyword, ctx(["welcome sequence"]), match_class="phrase", seed="welcome sequence")
    assert keep, reason
    keep, reason, _ = evaluate_keyword(keyword, ctx([]), match_class="phrase", seed="welcome sequence")
    assert not keep, "without the topic the seed has no evidence and its keywords are rejected"


def test_a_topic_word_on_its_own_is_not_evidence():
    ctx = build_relevance_context(
        services=["Email Marketing"],
        cdd_keywords=[],
        page_terms=[],
        themes=[],
        seeds=["Email Marketing"],
        service_subtopics=["welcome sequence"],
    )
    keep, _, _ = evaluate_keyword("sequence diagram software", ctx, match_class="related", seed="Email Marketing")
    assert not keep


# ------------------------------------------------------------------ report


def test_audit_and_notice():
    plan = _plan("Meta Ads", ["tiktok marketing", "facebook retargeting"])
    results = {"Meta Ads": generator.ExpansionResult(service="Meta Ads", terms=("tiktok marketing", "facebook retargeting"))}
    entries = audit_entries(results, [plan])
    assert entries[0]["added"] == ["facebook retargeting"]
    assert {row["reason"] for row in entries[0]["skipped"]} == {"excluded_by_operator"}
    notice = operator_notice(entries) or ""
    assert "Meta Ads +1" in notice
    failed = [{"service": "SEO", "status": "timeout", "added": []}]
    assert operator_notice(failed) == "Sub-service seeds: SEO: model timed out, catalog sub-services only."


def test_sub_services_are_found_when_the_catalog_row_has_another_name():
    # Selected as "Website Development"; the catalog row is "Web Development". The
    # operator's scope decision for the service still finds its sub-services.
    catalog = [
        {
            "id": "web development",
            "name": "Web Development",
            "subservices": [
                {"id": "wordpress development", "name": "WordPress Development"},
                {"id": "website design", "name": "Website Design"},
            ],
        }
    ]
    prioritization = {
        "service_scope": {
            "website development": {
                "name": "Website Development",
                "offered": [{"id": "wordpress development", "name": "WordPress Development"}],
                "excluded": [{"id": "website design", "name": "Website Design"}],
            }
        }
    }
    plan = generate_service_seeds(
        "Website Development", pool_roots=[], pool_targets={}, catalog=catalog, prioritization=prioritization
    )
    assert plan.seeds == ["Website Development", "WordPress Development"]
    assert plan.service_id == "website development"
    assert {"phrase": "Website Design", "reason": "excluded_by_operator"} in plan.catalog_subs_skipped
