"""Signals 1-6 and 11 in isolation, plus the context builder, weights and scoring."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.content_signals.base import SignalResult, is_enabled, weight_value
from app.services.content_signals.competitors import (
    collect_ai_competitors,
    collect_selected_competitors,
    split_competitors,
)
from app.services.content_signals.context import build_content_context, render_context_block
from app.services.content_signals.creative import collect_creative
from app.services.content_signals.framework import (
    available_frameworks,
    collect_framework,
    pick_framework,
)
from app.services.content_signals.funnel import collect_funnel, resolve_stage
from app.services.content_signals.past_performance import (
    collect_past_performance,
    covered_topics,
    format_track_record,
)
from app.services.content_signals.scoring import score_context
from app.services.content_signals.skills import available_skills, collect_skill, skill_for
from app.services.content_signals.weights import (
    DEFAULT_WEIGHTS,
    PRESETS,
    detect_conflicts,
    resolve_weights,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


# --- base ------------------------------------------------------------------


def test_weight_vocabulary():
    assert weight_value("high") > weight_value("medium") > weight_value("low") > 0
    assert weight_value("off") == 0
    assert weight_value(None) == weight_value("medium")
    assert weight_value("nonsense") == weight_value("medium")
    assert is_enabled("low") and not is_enabled("off")


# --- signal 1: past performance -------------------------------------------


def test_no_history_is_unavailable_and_says_so():
    r = collect_past_performance(keyword="seo services")
    assert r.available is False
    assert "No content history" in r.notes[0]


def test_covered_topics_dedupes_across_sources():
    covered = covered_topics(
        production={"pages": [{"keyword": "SEO Services", "url": "/seo"}]},
        publishing={"published": [{"keyword": "seo services", "url": "/seo"}]},
        architecture={"pages": [{"title": "Local SEO", "path": "/local-seo"}]},
    )
    keys = [c["topic_key"] for c in covered]
    assert len(keys) == len(set(keys))
    assert len(covered) == 2


def test_cannibalisation_clash_is_flagged_for_the_target_keyword():
    r = collect_past_performance(
        keyword="seo services",
        publishing={"published": [{"keyword": "SEO services", "url": "/seo-services"}]},
    )
    assert r.available
    assert r.data["cannibalisation_clash"] is not None
    assert any("CANNIBALISATION" in n for n in r.notes)
    assert any("duplicating existing page" in a for a in r.avoids)


def test_word_order_does_not_hide_a_duplicate():
    r = collect_past_performance(
        keyword="services seo melbourne",
        publishing={"published": [{"keyword": "melbourne seo services", "url": "/x"}]},
    )
    assert r.data["cannibalisation_clash"] is not None


def test_track_record_separates_kept_from_consolidated():
    record = format_track_record(
        {
            "pages": [
                {"page_type": "guide", "disposition": "keep", "words": 1200},
                {"page_type": "guide", "disposition": "keep", "words": 1400},
                {"page_type": "blog", "disposition": "consolidate"},
            ]
        }
    )
    assert record["worked"][0][0] == "guide"
    assert record["underperformed"][0][0] == "blog"
    assert record["median_words_of_kept_pages"] in (1200, 1400)


# --- signals 2 and 3: competitors -----------------------------------------


ROWS = [
    {"name": "WebFX", "url": "https://webfx.com", "source": "dataforseo", "confirmed": False},
    {"name": "King Kong", "url": "https://kingkong.co", "source": "manual", "confirmed": True},
    {"name": "Dup", "url": "https://webfx.com/x", "source": "dataforseo"},
]


def test_split_separates_operator_picks_from_ai_and_dedupes():
    selected, detected = split_competitors(ROWS)
    assert [c["domain"] for c in selected] == ["kingkong.co"]
    assert [c["domain"] for c in detected] == ["webfx.com"]


def test_selected_competitors_available_when_present():
    r = collect_selected_competitors(competitors=ROWS)
    assert r.available
    assert r.data["competitors"][0]["selected_by"] == "operator"


def test_ai_competitors_are_marked_secondary_when_operator_picks_exist():
    r = collect_ai_competitors(competitors=ROWS)
    assert r.available
    assert r.data["secondary_to_selected"] is True
    assert any("secondary" in n for n in r.notes)


def test_selected_only_mode_excludes_ai_competitors_entirely():
    r = collect_ai_competitors(competitors=ROWS, selected_only=True)
    assert r.available is False
    assert "selected_only" in r.notes[0]


def test_no_selected_competitors_is_unavailable_not_an_error():
    r = collect_selected_competitors(competitors=[ROWS[0]])
    assert r.available is False


def test_competitor_intel_comes_from_stored_phase5_data():
    r = collect_selected_competitors(
        competitors=ROWS,
        search_demand={
            "competitor_page_keywords": [
                {"domain": "kingkong.co", "keyword": "email marketing guide"}
            ],
            "keyword_dataset": [{"keyword": "crm integration", "gap_flag": True}],
        },
    )
    assert "email marketing guide" in r.data["harvested_topics"]
    assert "crm integration" in r.data["gap_keywords"]


def test_competitor_coverage_records_how_the_topic_is_covered():
    r = collect_selected_competitors(
        competitors=ROWS,
        search_demand={
            "competitor_page_keywords": [
                {
                    "domain": "kingkong.co",
                    "keyword": "email marketing guide",
                    "evidence_field": "h1",
                    "evidence_title": "Email marketing guide",
                    "page_kind": "guide",
                }
            ],
            "competitor_sites": [
                {
                    "domain": "kingkong.co",
                    "pages": [
                        {
                            "title": "Email sequences that convert",
                            "kind": "blog",
                            "h1s": ["How we write sequences"],
                        }
                    ],
                }
            ],
        },
    )
    how = {row["topic"]: row["how"] for row in r.data["coverage"]}
    assert "guide page" in how["email marketing guide"]
    assert "placed in h1" in how["email marketing guide"]
    assert how["Email sequences that convert"].startswith("blog page")


# --- signal 4: creative ---------------------------------------------------


def test_creative_off_by_default():
    r = collect_creative()
    assert r.available is False
    assert r.weight == "off"


def test_creative_on_labels_output_exploratory():
    r = collect_creative(enabled=True, keyword="email marketing")
    assert r.available
    assert r.data["label"] == "EXPLORATORY"
    assert r.data["directives"]


# --- signal 5: skills -----------------------------------------------------


def test_skill_files_exist_on_disk():
    assert "service-page" in available_skills()
    assert "how-to-guide" in available_skills()


@pytest.mark.parametrize(
    "page_type,expected",
    [("service", "service-page"), ("guide", "how-to-guide"), ("blog", "seo-blog-post"),
     ("location", "local-landing-page"), ("faq", "faq-page"), ("comparison", "comparison-post")],
)
def test_skill_selected_from_page_type(page_type, expected):
    stem, _ = skill_for(page_type)
    assert stem == expected


def test_skill_override_wins():
    stem, why = skill_for("service", override="faq-page")
    assert stem == "faq-page" and why == "operator override"


def test_unknown_page_type_falls_back_and_says_so():
    stem, why = skill_for("nonsense")
    assert stem == "seo-blog-post"
    assert "no skill mapped" in why


def test_collect_skill_loads_real_instructions():
    r = collect_skill(page_type="service")
    assert r.available
    assert "Service page" in r.data["instructions"]


# --- signal 6: frameworks -------------------------------------------------


def test_framework_files_exist_on_disk():
    for name in ("aida", "pas", "bab", "eeat", "paa", "skyscraper", "four_ps", "storybrand"):
        assert name in available_frameworks(), name


def test_auto_picks_pas_for_a_bofu_service_page_and_explains_why():
    name, why = pick_framework(page_type="service", funnel_stage="BOFU")
    assert name == "pas"
    assert "auto:" in why and "BOFU" in why


def test_auto_picks_a_question_structure_for_a_tofu_guide():
    name, _ = pick_framework(page_type="guide", funnel_stage="TOFU")
    assert name == "paa"


def test_operator_framework_choice_wins_over_auto():
    name, why = pick_framework(page_type="service", funnel_stage="BOFU", choice="storybrand")
    assert name == "storybrand" and why == "operator choice"


def test_unknown_framework_falls_back_with_a_reason():
    name, why = pick_framework(page_type="blog", funnel_stage="TOFU", choice="nope")
    assert name == "eeat" and "unknown framework" in why


def test_collect_framework_loads_real_instructions():
    r = collect_framework(page_type="service", funnel_stage="BOFU")
    assert r.available
    assert "Problem" in r.data["instructions"]


# --- signal 11: funnel ----------------------------------------------------


@pytest.mark.parametrize(
    "page_type,expected",
    [("service", "BOFU"), ("landing", "BOFU"), ("guide", "TOFU"), ("comparison", "MOFU")],
)
def test_page_type_pins_the_funnel_stage(page_type, expected):
    stage, why = resolve_stage(keyword="anything", page_type=page_type)
    assert stage == expected
    assert page_type.replace("_", " ") in why


def test_keyword_decides_when_page_type_is_neutral():
    stage, why = resolve_stage(keyword="what is email marketing", page_type="page")
    assert stage in ("TOFU", "MOFU", "BOFU")
    assert "keyword phrasing" in why


def test_cta_strength_matches_the_stage():
    assert collect_funnel(keyword="x", page_type="guide").data["cta_strength"] == "soft"
    assert collect_funnel(keyword="x", page_type="service").data["cta_strength"] == "strong"


# --- weights and conflicts -----------------------------------------------


def test_presets_cover_every_signal():
    for name, preset in PRESETS.items():
        assert set(preset) == set(DEFAULT_WEIGHTS), name


def test_creative_preset_raises_creative_and_lowers_history():
    weights, name = resolve_weights("creative")
    assert name == "creative"
    assert weights["creative"] == "high"
    assert weights["past_performance"] == "low"


def test_unknown_preset_falls_back_to_the_default():
    _weights, name = resolve_weights("banana")
    assert name == "data_driven"


def test_overrides_apply_on_top_of_a_preset():
    weights, _ = resolve_weights("local_growth", {"offers": "off"})
    assert weights["offers"] == "off"
    assert weights["reach"] == "high"


def test_the_heavier_signal_wins_a_conflict_and_it_is_recorded():
    blocker = SignalResult(
        name="past_performance", available=True, weight="low",
        avoids=["already covered: email marketing guide"],
    )
    pusher = SignalResult(
        name="competitors_selected", available=True, weight="high",
        suggests=["beat competitor angle: email marketing guide"],
    )
    conflicts = detect_conflicts([blocker, pusher])
    assert conflicts
    assert conflicts[0]["winner"] == "competitors_selected"
    assert "outweighs" in conflicts[0]["resolution"]


def test_equal_weights_are_left_unresolved_rather_than_broken_arbitrarily():
    blocker = SignalResult(
        name="past_performance", available=True, weight="high",
        avoids=["already covered: seo audit"],
    )
    pusher = SignalResult(
        name="competitors_ai", available=True, weight="high",
        suggests=["competitor topic to beat: seo audit"],
    )
    conflicts = detect_conflicts([blocker, pusher])
    assert conflicts[0]["winner"] is None
    assert "unresolved" in conflicts[0]["resolution"]


# --- scoring --------------------------------------------------------------


def test_structural_signals_do_not_inflate_the_score():
    results = [
        SignalResult(name="skill", available=True, weight="high"),
        SignalResult(name="framework", available=True, weight="high"),
        SignalResult(name="funnel", available=True, weight="high"),
    ]
    assert score_context(results)["score"] == 0


def test_score_rises_with_available_evidential_signals():
    thin = score_context([SignalResult(name="offers", available=False, weight="medium")])
    rich = score_context([SignalResult(name="offers", available=True, weight="medium")])
    assert rich["score"] > thin["score"]


def test_a_cannibalisation_clash_costs_points():
    clean = score_context(
        [SignalResult(name="past_performance", available=True, weight="medium")]
    )
    clashed = score_context(
        [
            SignalResult(
                name="past_performance", available=True, weight="medium",
                data={"cannibalisation_clash": {"url": "/x"}},
            )
        ]
    )
    assert clashed["score"] < clean["score"]
    assert clashed["penalties"]


def test_breakdown_names_every_signal():
    results = [
        SignalResult(name="offers", available=True, weight="medium"),
        SignalResult(name="reach", available=False, weight="low"),
    ]
    names = {b["signal"] for b in score_context(results)["breakdown"]}
    assert names == {"offers", "reach"}


# --- the context builder -------------------------------------------------


def test_context_builds_with_nothing_but_a_keyword():
    """Every signal missing must produce a usable context, not an exception."""
    ctx = build_content_context(keyword="email marketing melbourne", now=NOW)
    assert ctx["keyword"] == "email marketing melbourne"
    assert ctx["missing"], "missing signals should be listed"
    assert ctx["relevance"]["score"] >= 0
    assert "funnel" in ctx["signals"]


def test_a_signal_switched_off_is_not_collected():
    ctx = build_content_context(
        keyword="seo services",
        preset="custom",
        weight_overrides={"offers": "off"},
        commercial={"offers": [{"name": "X", "active": True}]},
        now=NOW,
    )
    assert "offers" not in ctx["signals"]


def test_a_failing_signal_is_isolated_not_fatal(monkeypatch):
    from app.services.content_signals import context as ctx_mod

    def boom(**kwargs):
        raise RuntimeError("signal exploded")

    monkeypatch.setattr(ctx_mod, "collect_reach", boom)
    ctx = build_content_context(keyword="seo services", now=NOW)
    assert ctx["signals"]["reach"]["available"] is False
    assert "Signal failed" in ctx["signals"]["reach"]["notes"][0]


def test_signal_order_is_heaviest_first():
    ctx = build_content_context(keyword="seo services", preset="data_driven", now=NOW)
    order = ctx["signal_order"]
    weights = [weight_value(ctx["signals"][n]["weight"]) for n in order]
    assert weights == sorted(weights, reverse=True)


def test_full_context_records_settings_for_reproducibility():
    ctx = build_content_context(
        keyword="seo services melbourne",
        page_type="service",
        preset="local_growth",
        skill_override="service-page",
        framework_choice="pas",
        selected_competitors_only=True,
        creative_mode=False,
        commercial={"business_model": "Services", "geographic_focus": "Melbourne, Australia"},
        now=NOW,
    )
    assert ctx["preset"] == "local_growth"
    assert ctx["settings"]["framework_choice"] == "pas"
    assert ctx["settings"]["selected_competitors_only"] is True
    assert ctx["version"] == "content_context_v1"
    assert ctx["funnel_stage"] == "BOFU"


def test_creative_mode_marks_the_whole_context_exploratory():
    ctx = build_content_context(keyword="x", preset="creative", creative_mode=True, now=NOW)
    assert ctx["exploratory"] is True
    assert "EXPLORATORY" in render_context_block(ctx)


def test_rendered_block_omits_missing_signals_and_never_invents():
    ctx = build_content_context(keyword="email marketing", now=NOW)
    block = render_context_block(ctx)
    assert "CONTENT CONTEXT" in block
    assert "unavailable signals" in block
    # Instruction bodies are injected separately, not dumped into the block.
    assert "# Skill:" not in block


def test_rendered_block_respects_the_char_cap():
    ctx = build_content_context(keyword="email marketing", page_type="service", now=NOW)
    assert len(render_context_block(ctx, max_chars=200)) <= 200


# --- integration with the writer -----------------------------------------


async def test_write_one_page_echoes_the_context_for_reproducibility():
    """The context and its explanation are saved with the piece."""
    from app.services.create_content import write_one_page

    ctx = build_content_context(
        keyword="seo services melbourne",
        page_type="service",
        preset="local_growth",
        commercial={"business_model": "Services", "geographic_focus": "Melbourne, Australia"},
        now=NOW,
    )
    draft = await write_one_page(
        brief={
            "keyword": "seo services melbourne",
            "title": "SEO Services Melbourne",
            "page_type": "service",
            "action": "create",
            "url": "/services/seo-melbourne/",
            "outline": [{"title": "What's included", "notes": ["Deliverables"]}],
            "preflight": {"url": "/services/seo-melbourne/", "parent": "/services/", "blockers": []},
        },
        client_name="Click Trends",
        use_llm=False,
        content_context=ctx,
    )
    assert draft["ok"] is True
    assert draft["content_context"]["version"] == "content_context_v1"
    why = draft["why_this_content"]
    assert why["funnel_stage"] == "BOFU"
    assert why["skill"] == "service-page"
    assert why["framework"] == "pas"
    assert why["summary"]
    assert draft["relevance"]["score"] >= 0


async def test_a_piece_without_a_context_is_unchanged():
    """The signal bundle is optional — existing callers keep working."""
    from app.services.create_content import write_one_page

    draft = await write_one_page(
        brief={
            "keyword": "seo services melbourne",
            "title": "SEO Services Melbourne",
            "page_type": "service",
            "action": "create",
            "url": "/services/seo-melbourne/",
            "outline": [{"title": "What's included", "notes": ["Deliverables"]}],
            "preflight": {"url": "/services/seo-melbourne/", "parent": "/services/", "blockers": []},
        },
        client_name="Click Trends",
        use_llm=False,
    )
    assert draft["ok"] is True
    assert "content_context" not in draft
    assert "why_this_content" not in draft


def test_exploratory_pieces_are_labelled_in_the_explanation():
    from app.services.content_signals.explain import explain_content_context

    ctx = build_content_context(
        keyword="email marketing", page_type="blog", preset="creative",
        creative_mode=True, now=NOW,
    )
    why = explain_content_context(ctx)
    assert why["exploratory"] is True
    assert "EXPLORATORY" in why["summary"]
