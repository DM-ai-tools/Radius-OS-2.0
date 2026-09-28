"""Behavioural contract for the keyword relevance gate.

Three lists, two client fixtures. These describe what the gate SHOULD do, not
what it does today:

  MUST_REJECT   — the searcher is not this business's customer at all
                  (job seekers, students). Hard reject.
  MUST_REROUTE  — a real person, wrong intent: they want to do it themselves.
                  Keep for content, but never target a commercial/service page.
  MUST_KEEP     — a buyer. Must survive the gate.

All three lists are enforced as of the two-tier exclusion gate.
"""

from __future__ import annotations

import pytest

from app.services.keyword_relevance import build_relevance_context, evaluate_keyword


def dental_context():
    return build_relevance_context(
        services=[
            "General Dentistry",
            "Teeth Whitening",
            "Dental Implants",
            "Emergency Dental Care",
        ],
        cdd_keywords=["dentist melbourne", "family dentist"],
        competitor_names=["SmileCo Dental"],
        competitor_domains=["smileco.com.au"],
        brand_name="Northside Dental",
        domain="northsidedental.com.au",
    )


def bakery_context():
    return build_relevance_context(
        services=[
            "Sourdough Bread",
            "Custom Cakes",
            "Pastries",
            "Delivery",
        ],
        cdd_keywords=["artisan bakery", "order bread online"],
        competitor_names=["Crumb Brothers"],
        competitor_domains=["crumbbrothers.com"],
        brand_name="Crust and Co",
        domain="crustandco.com",
    )


# (keyword, seed, match_class, context factory)
MUST_REJECT = [
    ("free dental jobs", "dental implants", "broad", dental_context),
    ("dental assistant salary", "general dentistry", "broad", dental_context),
    ("dental school admission", "general dentistry", "broad", dental_context),
    ("dental hygienist careers", "general dentistry", "broad", dental_context),
    ("bakery jobs near me", "sourdough bread", "broad", bakery_context),
    ("baker salary", "sourdough bread", "broad", bakery_context),
]

# "baker salary" once rejected only because "baker" did not stem to "bakery", so
# it matched no evidence at all. Step 2b's lemma folding makes it match — it is
# now caught by the HARD_REJECT term list instead, which is the durable reason.

MUST_REROUTE = [
    ("diy teeth whitening", "teeth whitening", "broad", dental_context),
    ("bread recipe", "sourdough bread", "broad", bakery_context),
    ("how to bake sourdough at home", "sourdough bread", "broad", bakery_context),
    ("bread maker machine reviews", "sourdough bread", "broad", bakery_context),
]

MUST_KEEP = [
    ("emergency dentist near me", "emergency dental care", "phrase", dental_context),
    ("teeth whitening cost", "teeth whitening", "phrase", dental_context),
    ("dental implants price", "dental implants", "phrase", dental_context),
    ("dentist open saturday", "general dentistry", "broad", dental_context),
    ("sourdough bread delivery", "sourdough bread", "phrase", bakery_context),
    ("custom birthday cake", "custom cakes", "broad", bakery_context),
    ("order pastries online", "pastries", "broad", bakery_context),
]


def _evaluate(keyword: str, seed: str, match_class: str, ctx_factory):
    return evaluate_keyword(
        keyword,
        ctx_factory(),
        match_class=match_class,
        seed=seed,
    )


@pytest.mark.parametrize(("keyword", "seed", "match_class", "ctx"), MUST_REJECT)
def test_non_customer_keywords_are_rejected(keyword, seed, match_class, ctx):
    keep, reason, _evidence = _evaluate(keyword, seed, match_class, ctx)
    assert keep is False, f"{keyword!r} was kept as {reason!r}"


@pytest.mark.parametrize(("keyword", "seed", "match_class", "ctx"), MUST_REROUTE)
def test_do_it_yourself_keywords_are_not_commercially_targetable(
    keyword, seed, match_class, ctx
):
    keep, _reason, _evidence = _evaluate(keyword, seed, match_class, ctx)
    assert keep is True, f"{keyword!r} should be kept for content, not dropped"
    from app.services.keyword_relevance import is_commercially_targetable

    assert is_commercially_targetable(keyword) is False


@pytest.mark.parametrize(("keyword", "seed", "match_class", "ctx"), MUST_KEEP)
def test_buyer_intent_keywords_survive_the_gate(keyword, seed, match_class, ctx):
    keep, reason, _evidence = _evaluate(keyword, seed, match_class, ctx)
    assert keep is True, f"{keyword!r} was rejected as {reason!r}"
