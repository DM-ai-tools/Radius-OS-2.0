"""Primary, roles and labels are decided in one place, the same way on both paths.

Locks down the 2026-10-19 brief:
1. One decider for the primary; the cluster's primary and the member marked
   Primary never disagree; naming cannot re-pick the primary.
2. The primary is a scored decision, not the highest volume; an exact commercial
   service term can win with no volume reading, and the reason is recorded.
3. Secondary means a close variant (same intent and stage); supporting is a
   sub-angle or a different stage; counts follow merit.
4. One intent detector: provider labels validated, client brand navigational,
   signal words only in a buying sense.
5. Real middle-of-funnel signals; a price question is consideration.
6. Cluster intent/funnel from the member distribution, which is recorded; the
   mixed-intent flag survives.
7. Every label records its source, confidence and any disagreement.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services.keyword_cluster_naming import apply_cluster_names
from app.services.keyword_cluster_roles import (
    RoleContext,
    conflicting_intents,
    finalize_cluster,
    label_keyword,
)
from app.services.keyword_clustering import (
    _finalize_roles,
    build_clusters_deterministic,
    validate_clusters,
)
from app.services.keyword_llm_clustering import build_cluster_report_from_llm
from app.services.keyword_opportunity import (
    detect_funnel,
    detect_intent,
    funnel_with_evidence,
    intent_with_evidence,
    normalize_provider_intent,
)


def _row(keyword, volume=None, *, provider=None, difficulty=None, reason="seed_phrase", **extra):
    return {
        "keyword": keyword,
        "volume": volume,
        "difficulty": difficulty,
        "intent": provider,
        "provider_intent": provider,
        "intent_basis": "measured" if provider else "unmeasured",
        "relevance_reason": reason,
        **extra,
    }


def _ctx(rows, **kw):
    return RoleContext.build(rows, **kw)


def _primary_rows(cluster):
    return [m for m in cluster["keywords"] if m["role"] == "Primary"]


# --------------------------------------------------------------- 4. one detector


@pytest.mark.parametrize(
    ("keyword", "intent"),
    [
        ("how much does seo cost", "commercial"),  # a price question is research
        ("seo cost melbourne", "commercial"),  # "cost" alone is not a purchase
        ("seo pricing", "transactional"),
        ("hire seo consultant", "transactional"),
        ("best way to learn seo", "informational"),  # "best" in an educational sense
        ("best seo agency melbourne", "commercial"),
        ("seo costume", "informational"),  # whole words only: costume is not cost
        ("laptop seo", "informational"),  # "top" inside "laptop" is not "top"
        ("seo agency melbourne", "commercial"),
        ("client portal login", "navigational"),
    ],
)
def test_intent_signal_words_count_only_in_their_buying_sense(keyword, intent):
    assert detect_intent(keyword) == intent


def test_provider_intent_is_validated_not_passed_through():
    assert normalize_provider_intent("Commercial") == "commercial"
    assert normalize_provider_intent("branded") == "navigational"
    assert normalize_provider_intent("weird_label") is None
    # An unmappable provider label falls back to the heuristic, never the raw string.
    assert detect_intent("seo pricing", "weird_label") == "transactional"
    assert detect_intent("what is seo", "") == "informational"


def test_client_brand_tokens_make_a_query_navigational():
    assert detect_intent("clicktrends reviews", brand_tokens={"clicktrends"}) == "navigational"
    # Brand beats a provider label; the provenance records the disagreement.
    assert intent_with_evidence("clicktrends seo", "commercial", brand_tokens={"clicktrends"})[:2] == (
        "navigational",
        "heuristic",
    )
    labels = label_keyword(_row("clicktrends seo", provider="commercial"), brand_tokens={"clicktrends"})
    assert labels["intent"] == "navigational"
    assert {"source": "provider", "value": "commercial"} in labels["label_provenance"]["intent"]["disagreements"]


def test_clustering_has_no_second_intent_detector_for_labels():
    import app.services.keyword_clustering as kc
    import app.services.keyword_llm_clustering as kl

    assert not hasattr(kc, "_detect_intent")
    assert not hasattr(kl, "_detect_intent")


# --------------------------------------------------------------- 5. funnel


@pytest.mark.parametrize(
    "keyword",
    [
        "seo vs ppc",
        "semrush alternatives",
        "seo agency reviews",
        "is seo worth it",
        "how to choose an seo agency",
        "what to look for in an seo agency",
        "how much does seo cost",
        "best seo agency",
    ],
)
def test_consideration_queries_are_mofu_on_their_own_merits(keyword):
    stage, basis = funnel_with_evidence(keyword, "informational")
    assert stage == "MOFU"
    assert basis not in ("from_intent", "default")


def test_funnel_records_when_tofu_is_a_default_not_evidence():
    assert funnel_with_evidence("what is seo")[0] == "TOFU"
    assert funnel_with_evidence("what is seo")[1] == "educational"
    assert funnel_with_evidence("seo", "informational") == ("TOFU", "default")
    assert detect_funnel("seo pricing") == "BOFU"


# --------------------------------------------------------------- 7. provenance


def test_label_provenance_and_confidence():
    agree = label_keyword(_row("seo pricing", provider="transactional"))
    assert (agree["intent_source"], agree["intent_confidence"]) == ("provider", "high")
    assert agree["label_disagreement"] is False

    guess = label_keyword(_row("seo"))
    assert (guess["intent_source"], guess["intent_confidence"]) == ("heuristic", "low")
    assert (guess["funnel_source"], guess["funnel_confidence"]) == ("default", "low")

    clash = label_keyword(_row("seo pricing", provider="informational"), llm_intent="transactional")
    assert clash["intent"] == "informational" and clash["intent_source"] == "provider"
    assert clash["label_disagreement"] is True
    sources = {d["source"] for d in clash["label_provenance"]["intent"]["disagreements"]}
    assert sources == {"llm", "heuristic"}
    assert clash["intent_confidence"] == "medium"

    model_only = label_keyword(_row("seo audit checklist"), llm_intent="informational", llm_funnel="TOFU")
    assert model_only["intent_source"] == "llm"
    assert model_only["funnel_source"] == "heuristic" and model_only["funnel_confidence"] == "high"


# --------------------------------------------------------------- 2. scored primary


def test_exact_service_term_without_volume_can_win_and_says_why():
    rows = [
        _row("seo agency melbourne", None, reason="service_match"),
        _row("seo", 40000, difficulty=80),
        _row("seo services melbourne", 30, provider="commercial", difficulty=20),
    ]
    ctx = RoleContext.build(rows)
    ctx.services = ["SEO"]
    cluster = finalize_cluster({"keywords": [dict(r) for r in rows]}, ctx)
    sel = cluster["primary_selection"]
    assert cluster["primary_keyword"] in ("seo agency melbourne", "seo services melbourne")
    assert sel["exact_service_match"] == "SEO"
    assert "Exact match" in sel["reason"]
    # The 40,000/mo broad head did not win on volume alone.
    assert cluster["primary_keyword"] != "seo"


def test_unmeasured_exact_service_term_beats_measured_off_page_query():
    rows = [
        _row("seo services", None, reason="service_match"),
        _row("seo services pricing", 900, provider="commercial"),
        _row("seo services australia", 50, provider="commercial"),
    ]
    ctx = RoleContext.build(rows)
    ctx.services = ["SEO"]
    cluster = finalize_cluster({"keywords": [dict(r) for r in rows]}, ctx)
    assert cluster["primary_keyword"] in ("seo services", "seo services australia")
    if cluster["primary_keyword"] == "seo services":
        assert "unmeasured, not zero" in cluster["primary_selection"]["reason"]


def test_high_volume_keyword_with_the_wrong_intent_is_not_primary():
    rows = [
        _row("how much does seo cost", 5000, provider="commercial"),
        _row("seo guide", 300, provider="informational"),
        _row("seo basics", 200, provider="informational"),
        _row("seo tutorial", 150, provider="informational"),
    ]
    cluster = finalize_cluster({"keywords": [dict(r) for r in rows]}, RoleContext.build(rows))
    assert cluster["primary_selection"]["page_intent"] == "informational"
    assert cluster["primary_keyword"] != "how much does seo cost"
    assert "Chosen over \"how much does seo cost\"" in cluster["primary_selection"]["reason"]
    # 6. The cluster is labelled from its members, and the mix is recorded.
    assert cluster["intent"] == "informational"
    assert cluster["intent_distribution"]["counts"] == {"commercial": 1, "informational": 3}
    assert set(cluster["funnel_distribution"]["weighted_share"]) == {"MOFU", "TOFU"}


def test_cluster_intent_follows_members_not_just_the_primary():
    rows = [
        _row("seo services", 1000, provider="commercial", reason="service_match"),
        _row("what is seo", 50, provider="informational"),
        _row("seo guide", 40, provider="informational"),
        _row("seo basics", 30, provider="informational"),
        _row("seo tips", 20, provider="informational"),
        _row("seo tutorial", 10, provider="informational"),
    ]
    ctx = RoleContext.build(rows)
    ctx.services = ["SEO"]
    cluster = finalize_cluster({"keywords": [dict(r) for r in rows]}, ctx)
    assert cluster["primary_keyword"] == "seo services"
    assert cluster["keywords"][0]["intent"] == "commercial"
    # Five educational members outweigh one commercial primary.
    assert cluster["intent"] == "informational"
    assert cluster["funnel"] == "TOFU"
    share = cluster["intent_distribution"]["weighted_share"]
    assert share["informational"] > share["commercial"] > 0


# --------------------------------------------------------------- 3. roles on merit


def test_secondary_is_a_close_variant_and_counts_follow_merit():
    rows = [
        _row("seo agency melbourne", 500, provider="commercial", reason="service_match"),
        _row("seo company melbourne", 300, provider="commercial"),
        _row("seo services melbourne", 250, provider="commercial"),
        _row("seo agency melbourne for dentists", 20, provider="commercial"),
        _row("what is seo", 9000, provider="informational"),
    ]
    cluster = finalize_cluster({"keywords": [dict(r) for r in rows]}, RoleContext.build(rows))
    roles = {m["keyword"]: m["role"] for m in cluster["keywords"]}
    assert roles["seo agency melbourne"] == "Primary"
    assert roles["seo company melbourne"] == "Secondary"
    assert roles["seo services melbourne"] == "Secondary"
    assert roles["seo agency melbourne for dentists"] == "Supporting"  # narrower angle
    assert roles["what is seo"] == "Supporting"  # different stage, despite top volume
    assert cluster["role_counts"] == {"Primary": 1, "Secondary": 2, "Supporting": 2}
    assert all(m.get("role_reason") for m in cluster["keywords"])

    # Swapping one meaningful word is a different search, not a variant.
    swap = [_row("google business guide", 90, provider="informational"),
            _row("google business examples", 40, provider="informational")]
    swapped = finalize_cluster({"keywords": [dict(r) for r in swap]}, RoleContext.build(swap))
    assert swapped["role_counts"]["Secondary"] == 0

    lone = [_row("seo agency melbourne", 500, provider="commercial"), _row("seo tips", 40)]
    none = finalize_cluster({"keywords": [dict(r) for r in lone]}, RoleContext.build(lone))
    assert none["role_counts"]["Secondary"] == 0


def test_a_city_keyword_is_not_a_secondary_of_the_national_search():
    from app.services.keyword_clustering import clusters_one_search_each

    rows = [
        _row("brisbane seo company", 400, provider="commercial"),
        _row("brisbane seo agency", 200, provider="commercial"),
        _row("seo agency", 4400, provider="commercial"),
        _row("seo firm", 300, provider="commercial"),
    ]
    cluster = finalize_cluster({"keywords": [dict(r) for r in rows]}, RoleContext.build(rows))
    roles = {m["keyword"]: m["role"] for m in cluster["keywords"]}
    primary = cluster["primary_keyword"]
    if "brisbane" in primary:
        assert roles["brisbane seo agency"] == "Secondary"
        assert roles["seo agency"] == "Supporting"
    else:
        assert roles["seo agency"] == "Primary"
        assert roles["seo firm"] == "Secondary"
        assert roles["brisbane seo company"] == "Supporting"
    split = clusters_one_search_each(
        [{"name": "Brisbane SEO Company", "primary_keyword": "brisbane seo company", "keywords": rows, "funnel": "BOFU", "intent": "commercial"}]
    )
    groups = [{m["keyword"] for m in item["keywords"]} for item in split]
    assert any("seo agency" in group and "brisbane seo company" not in group for group in groups)
    assert any("brisbane seo company" in group and "seo agency" not in group for group in groups)


# --------------------------------------------------------------- 1. one primary


def _assert_one_primary(cluster):
    primaries = _primary_rows(cluster)
    assert len(primaries) == 1
    assert primaries[0]["keyword"] == cluster["primary_keyword"]
    assert cluster["keywords"][0] is primaries[0] or cluster["keywords"][0] == primaries[0]


def test_naming_cannot_re_pick_the_primary():
    rows = [_row("seo agency melbourne", 500, provider="commercial"), _row("seo company melbourne", 300, provider="commercial")]
    cluster = finalize_cluster({"name": "x", "keywords": [dict(r) for r in rows]}, RoleContext.build(rows))
    named = apply_cluster_names([cluster], {"clusters": [{"id": 1, "name": "Melbourne SEO", "primary_keyword": "seo company melbourne"}]})
    assert named[0]["name"] == "Melbourne SEO"
    assert named[0]["primary_keyword"] == cluster["primary_keyword"]
    _assert_one_primary(named[0])


def test_serp_merge_then_final_pass_leaves_exactly_one_primary():
    from app.services.keyword_serp_clustering import merge_clusters_by_serp_overlap

    a_rows = [_row("seo agency melbourne", 500, provider="commercial"), _row("seo company melbourne", 300, provider="commercial")]
    b_rows = [_row("seo services melbourne", 800, provider="commercial"), _row("seo consultant melbourne", 90, provider="commercial")]
    ctx = RoleContext.build(a_rows + b_rows)
    a = finalize_cluster({"name": "A", "keywords": [dict(r) for r in a_rows]}, ctx)
    b = finalize_cluster({"name": "B", "keywords": [dict(r) for r in b_rows]}, ctx)
    merged = merge_clusters_by_serp_overlap([a, b], [[a["primary_keyword"], b["primary_keyword"]]])
    assert len(merged) == 1
    assert len(_primary_rows(merged[0])) == 2  # what the merge alone leaves behind
    report = _finalize_roles({"clusters": merged, "content_roadmap": [{"cluster": "A", "target_keyword": "x"}]}, ctx)
    final = report["clusters"][0]
    _assert_one_primary(final)
    assert report["content_roadmap"][0]["target_keyword"] == final["primary_keyword"]
    # Keywords proven to share the SERP are same-page variants.
    other = next(m for m in final["keywords"] if m["keyword"] in (a["primary_keyword"], b["primary_keyword"]) and m["role"] != "Primary")
    assert other["role"] == "Secondary"
    assert "search results" in other["role_reason"]


def test_finalize_is_idempotent():
    rows = [
        _row("seo agency melbourne", 500, provider="commercial"),
        _row("what is seo", 9000),
        _row("seo company melbourne", None),
    ]
    ctx = RoleContext.build(rows)
    once = finalize_cluster({"keywords": [dict(r) for r in rows]}, ctx)
    twice = finalize_cluster(once, ctx)
    assert twice == once


# --------------------------------------------------------------- both paths agree


ROWS = [
    _row("seo agency melbourne", 500, provider="commercial", reason="service_match", parent_topic="seo melbourne"),
    _row("seo company melbourne", 300, provider="commercial", parent_topic="seo melbourne"),
    _row("seo services melbourne", None, parent_topic="seo melbourne"),
    _row("affordable seo melbourne", 40, provider="commercial", parent_topic="seo melbourne"),
]


def test_deterministic_and_model_paths_give_the_same_roles_and_labels():
    ctx = RoleContext.build(ROWS)
    det = build_clusters_deterministic([dict(r) for r in ROWS], role_context=ctx)
    det_by_cluster = {
        frozenset(m["keyword"] for m in c["keywords"]): c for c in det["clusters"]
    }
    payload = {
        "clusters": [
            {"name": "Model", "keywords": [{"keyword": k, "role": "Supporting"} for k in sorted(members)]}
            for members in det_by_cluster
        ]
    }
    llm = build_cluster_report_from_llm(payload, [dict(r) for r in ROWS], role_context=ctx)
    for c in llm["clusters"]:
        twin = det_by_cluster[frozenset(m["keyword"] for m in c["keywords"])]
        assert c["primary_keyword"] == twin["primary_keyword"]
        assert c["intent"] == twin["intent"] and c["funnel"] == twin["funnel"]
        assert c["role_counts"] == twin["role_counts"]
        fields = ("role", "intent", "funnel", "intent_source", "funnel_source", "intent_confidence")
        assert [{f: m[f] for f in fields} for m in c["keywords"]] == [
            {f: m[f] for f in fields} for m in twin["keywords"]
        ]


@pytest.mark.asyncio
async def test_full_runs_keep_keyword_accounting_and_one_primary_per_cluster():
    from app.services.keyword_clustering import run_keyword_clustering

    rows = [dict(r) for r in ROWS] + [_row("what is local seo", 700), _row("local seo checklist", 90)]
    report = await run_keyword_clustering(rows, within_deadline=False)
    assert report["keyword_accounting"]["balanced"] is True
    for cluster in report["clusters"]:
        _assert_one_primary(cluster)
        assert cluster["primary_selection"]["keyword"] == cluster["primary_keyword"]
        assert cluster["primary_selection"]["reason"]
    assert report["cluster_validation"]["issues"] is not None


# --------------------------------------------------------------- 6. mixed flag


def test_mixed_intent_flag_reads_the_member_distribution():
    rows = [
        _row("seo pricing", 900, provider="transactional"),
        _row("seo quote", 300, provider="transactional"),
        _row("what is seo", 800, provider="informational"),
        _row("seo guide", 600, provider="informational"),
    ]
    cluster = finalize_cluster({"name": "Mixed", "keywords": [dict(r) for r in rows]}, RoleContext.build(rows))
    assert conflicting_intents(cluster) == ["informational", "transactional"]
    issues = validate_clusters([cluster])["issues"]
    assert any(i["issue"] == "mixed_intent_cluster" for i in issues)

    clean = finalize_cluster({"name": "Clean", "keywords": [dict(r) for r in rows[:2]]}, RoleContext.build(rows))
    assert conflicting_intents(clean) == []


@pytest.mark.asyncio
async def test_model_path_does_not_let_naming_or_model_roles_pick_the_primary():
    from app.services.keyword_clustering import run_keyword_clustering

    payload = {
        "clusters": [
            {
                "name": "SEO Melbourne",
                "keywords": [
                    {"keyword": "affordable seo melbourne", "role": "Primary", "intent": "commercial"},
                    {"keyword": "seo agency melbourne", "role": "Supporting", "intent": "commercial"},
                    {"keyword": "seo company melbourne", "role": "Supporting", "intent": "commercial"},
                ],
            }
        ]
    }
    with patch("app.config.get_settings") as settings, patch(
        "app.services.keyword_llm_clustering.llm_cluster_keywords",
        new=AsyncMock(side_effect=lambda cleaned, **kw: build_cluster_report_from_llm(
            payload, cleaned, role_context=kw.get("role_context"))),
    ), patch(
        "app.services.keyword_cluster_naming.name_clusters",
        new=AsyncMock(side_effect=lambda cs, **_kw: apply_cluster_names(
            cs, {"clusters": [{"id": 1, "name": "Named", "primary_keyword": "affordable seo melbourne"}]})),
    ):
        settings.return_value.keyword_clustering_use_model = True
        report = await run_keyword_clustering([dict(r) for r in ROWS[:3]] + [dict(ROWS[3])], within_deadline=True)
    cluster = next(c for c in report["clusters"] if any(m["keyword"] == "seo agency melbourne" for m in c["keywords"]))
    _assert_one_primary(cluster)
    assert cluster["primary_keyword"] == "seo agency melbourne"
    model_labels = {m["keyword"]: m.get("llm_intent") for m in cluster["keywords"]}
    assert model_labels["seo agency melbourne"] == "commercial"


def test_difficulty_is_judged_against_the_sites_measured_strength():
    rows = [
        _row("seo agency melbourne", 500, provider="commercial", difficulty=55),
        _row("seo company melbourne", 200, provider="commercial", difficulty=20),
    ]
    weak = finalize_cluster({"keywords": [dict(r) for r in rows]}, RoleContext.build(rows, site_strength=15))
    strong = finalize_cluster({"keywords": [dict(r) for r in rows]}, RoleContext.build(rows, site_strength=60))
    assert weak["primary_keyword"] == "seo company melbourne"
    assert "harder to rank for (difficulty 55 vs 20)" in weak["primary_selection"]["reason"]
    assert strong["primary_keyword"] == "seo agency melbourne"
    assert "within reach for this site" in strong["primary_selection"]["reason"]
    assert strong["primary_selection"]["site_strength_known"] is True
