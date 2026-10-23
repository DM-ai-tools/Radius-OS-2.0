"""Model-suggested sub-service seeds: each selected service is widened beyond the few
sub-services its catalog lists, without breaking scope, isolation, budget or relevance."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.keyword_relevance import build_relevance_context, evaluate_keyword
from app.services.service_seeding import (
    EXPANSION_ORIGIN,
    generate_service_seeds,
    plan_service_seeds,
    run_service_scoped_seeding,
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


def test_topics_are_not_added_as_seeds():
    plan = _plan("SEO", ["technical seo", "seo audit"], pool=["seo melbourne"])
    assert plan.expansions_added == []
    assert {s["reason"] for s in plan.expansions_skipped} == {"sub_service"}
    lowered = {s.lower() for s in plan.seeds}
    assert "technical seo" not in lowered and "seo audit" not in lowered
    assert "local seo" not in lowered
    assert "seo melbourne" not in lowered
    assert "seo" in lowered


def test_topics_skip_catalog_scope_duplicates_and_other_services():
    plan = _plan(
        "SEO",
        [
            "link building",
            "instagram ads",
            "local seo",
            "seo melbourne",
            "meta ads",
            "technical seo",
        ],
        pool=["seo melbourne"],
    )
    assert plan.expansions_added == []
    assert {s.lower() for s in plan.seeds} == {"seo"}
    assert all(s["reason"] == "sub_service" for s in plan.expansions_skipped)


def test_an_excluded_sub_service_is_never_brought_back():
    plan = _plan("Meta Ads", ["tiktok marketing", "facebook retargeting", "lookalike audiences"])
    assert plan.expansions_added == []
    assert not any("tiktok" in s.lower() for s in plan.seeds)
    assert "facebook retargeting" not in {s.lower() for s in plan.seeds}


def test_a_service_with_no_catalog_sub_services_stays_the_service_name():
    plan = _plan("Email Marketing", ["welcome sequence", "abandoned cart emails", "klaviyo setup"])
    assert plan.expansions_added == []
    assert plan.seeds == ["Email Marketing"]


def test_the_cap_limits_how_many_are_seeded():
    plan = _plan("Email Marketing", ["a b", "c d", "e f", "g h"], max_expansions=2)
    assert plan.expansions_added == []
    assert plan.seeds == ["Email Marketing"]
    assert [s["reason"] for s in plan.expansions_skipped] == ["sub_service"] * 4


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
    assert by_service["SEO"].expansions_added == []
    assert by_service["Email Marketing"].expansions_added == []
    assert "technical seo" not in {s.lower() for s in by_service["SEO"].seeds}
    assert "welcome sequence" not in {s.lower() for s in by_service["SEO"].seeds}


async def test_expansions_ride_on_top_of_the_budget_without_breaking_the_share():
    # SEO has more own seeds than its share; every extra seed must still be fetched,
    # and exactly `share` of the service's own seeds.
    plan = _plan(
        "SEO",
        ["technical seo", "seo audit", "keyword research"],
        pool=["seo melbourne", "seo agency melbourne", "seo pricing"],
    )
    fetched: list[list[str]] = []

    async def fetch(seeds, **kw):
        fetched.append(list(seeds))
        return {"seed_clusters": [{"seed": s} for s in seeds], "keyword_dataset": []}

    await run_service_scoped_seeding([plan], fetch=fetch, total_cap=2)
    seeds = [s.lower() for s in fetched[0]]
    assert "technical seo" not in seeds and "seo audit" not in seeds
    assert "keyword research" not in seeds
    assert seeds == [s.lower() for s in plan.seeds][:2]


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
    assert entries[0]["added"] == []
    assert {row["reason"] for row in entries[0]["skipped"]} == {"sub_service"}
    notice = operator_notice(entries) or ""
    assert "+1" not in notice
    failed = [{"service": "SEO", "status": "timeout", "added": []}]
    assert operator_notice(failed) == "Sub-service seeds: SEO: model timed out, catalog sub-services only."
