"""Benchmarking: content sourcing, each dimension, and graceful degradation."""

from __future__ import annotations

from unittest.mock import patch

from app.services.content_benchmark import fetch as fetch_mod
from app.services.content_benchmark.dimensions import (
    compare_one,
    content_depth,
    heading_structure,
    keyword_coverage,
    onpage_seo,
    readability,
    topic_gaps,
)
from app.services.content_benchmark.fetch import (
    PageContent,
    from_cached_snapshots,
    from_pasted,
    gather_competitor_content,
    pick_best_page,
    word_count,
)
from app.services.content_benchmark.report import (
    prioritise_recommendations,
    run_benchmark,
    service_terms,
)

COMPETITORS = [
    {"id": "c1", "name": "King Kong", "url": "https://kingkong.co", "domain": "kingkong.co"},
    {"id": "c2", "name": "WebFX", "url": "https://webfx.com", "domain": "webfx.com"},
]

SNAPSHOTS = [
    {
        "name": "King Kong",
        "domain": "kingkong.co",
        "url": "https://kingkong.co",
        "pages": [
            {
                "url": "https://kingkong.co/seo-services/",
                "path": "/seo-services/",
                "title": "SEO Services Melbourne",
                "h1s": ["SEO Services"],
                "h2s": ["Technical audits", "Link building", "Local SEO"],
                "meta_description": "Award winning SEO.",
                "kind": "service",
            },
            {
                "url": "https://kingkong.co/blog/what-is-ppc/",
                "path": "/blog/what-is-ppc/",
                "title": "What is PPC",
                "h1s": ["What is PPC"],
                "h2s": [],
                "kind": "blog",
            },
        ],
    }
]

OURS = {
    "title": "SEO Services in Melbourne",
    "meta_title": "SEO Services in Melbourne",
    "meta_description": "We deliver technical SEO audits and local SEO for Melbourne businesses today.",
    "h1s": ["SEO Services in Melbourne"],
    "h2s": ["Technical audits", "What's included"],
    "page_markdown": (
        "We run technical audits for Melbourne businesses. "
        "Our team reviews crawling and indexing. We fix what blocks rankings. "
        "The work is practical and measured. We report every month. "
    ) * 12,
    "schema": {"@type": "Service"},
}


def _their_page(**kw) -> PageContent:
    base = dict(
        competitor_id="c1",
        name="King Kong",
        domain="kingkong.co",
        url="https://kingkong.co/seo-services/",
        source="live",
        title="SEO Services Melbourne",
        meta_description="Award winning SEO agency.",
        h1s=["SEO Services"],
        h2s=["Technical audits", "Link building", "Local SEO"],
        body_text=("We build links and run local SEO campaigns. " * 60),
        schema_types=["Organization"],
        depth_available=True,
    )
    base.update(kw)
    return PageContent(**base)


# --- sourcing --------------------------------------------------------------


def test_word_count_counts_words_not_characters():
    assert word_count("one two three") == 3
    assert word_count("") == 0
    assert word_count(None) == 0


def test_best_page_prefers_the_service_page_over_a_blog_post():
    page = pick_best_page(SNAPSHOTS[0], service_terms={"seo", "services"})
    assert page["path"] == "/seo-services/"


def test_cached_tier_returns_headings_but_no_depth():
    content = from_cached_snapshots(
        COMPETITORS[0], snapshots=SNAPSHOTS, service_terms={"seo", "services"}
    )
    assert content is not None
    assert content.source == "cached"
    assert content.h2s
    assert content.depth_available is False, "cached snapshots carry no body text"


def test_cached_tier_returns_none_for_an_uncrawled_competitor():
    assert from_cached_snapshots(
        COMPETITORS[1], snapshots=SNAPSHOTS, service_terms={"seo"}
    ) is None


def test_pasted_tier_parses_markdown_headings_and_enables_depth():
    content = from_pasted(COMPETITORS[0], "# Title\n\n## First\n\nBody words here.\n\n## Second\n")
    assert content.source == "pasted"
    assert content.title == "Title"
    assert content.h2s == ["First", "Second"]
    assert content.depth_available is True


async def test_live_failure_falls_back_to_the_cached_snapshot():
    async def dead_fetch(competitor, *, service_terms):
        out = PageContent(competitor_id="c1", name="King Kong", domain="kingkong.co")
        out.error = "timeout"
        return out

    with patch.object(fetch_mod, "from_live_fetch", dead_fetch):
        results = await gather_competitor_content(
            [COMPETITORS[0]], snapshots=SNAPSHOTS, service_terms={"seo"}, allow_live=True
        )
    assert results[0].source == "cached"
    assert results[0].error is None


async def test_a_competitor_failure_does_not_affect_the_others():
    async def half_dead(competitor, *, service_terms):
        if competitor["id"] == "c2":
            out = PageContent(competitor_id="c2", name="WebFX", domain="webfx.com")
            out.error = "fetch_failed:ConnectError"
            return out
        return _their_page()

    with patch.object(fetch_mod, "from_live_fetch", half_dead):
        results = await gather_competitor_content(
            COMPETITORS, snapshots=[], service_terms={"seo"}, allow_live=True
        )
    by_id = {r.competitor_id: r for r in results}
    assert by_id["c1"].ok is True
    assert by_id["c2"].ok is False
    assert by_id["c2"].error


async def test_an_unhandled_exception_in_one_fetch_is_contained():
    async def explode(competitor, *, service_terms):
        raise RuntimeError("boom")

    with patch.object(fetch_mod, "from_live_fetch", explode):
        results = await gather_competitor_content(
            [COMPETITORS[0]], snapshots=[], allow_live=True
        )
    assert results[0].ok is False
    assert "unhandled" in results[0].error


async def test_pasted_content_wins_over_a_live_fetch():
    async def should_not_run(competitor, *, service_terms):
        raise AssertionError("live fetch must not run when content was pasted")

    with patch.object(fetch_mod, "from_live_fetch", should_not_run):
        results = await gather_competitor_content(
            [COMPETITORS[0]], pasted={"c1": "# Pasted\n\nSome body."}, allow_live=True
        )
    assert results[0].source == "pasted"


# --- dimensions ------------------------------------------------------------


def test_keyword_coverage_reports_what_they_target_and_we_miss():
    result = keyword_coverage(
        OURS, _their_page(), target_keywords=["technical audits", "link building", "seo services"]
    )
    assert result.measurable
    assert "link building" in [g.lower() for g in result.gaps]
    assert result.recommendations


def test_keyword_coverage_abstains_without_target_keywords():
    result = keyword_coverage(OURS, _their_page(), target_keywords=[])
    assert result.measurable is False
    assert "No target keywords" in result.reason


def test_topic_gaps_finds_their_headings_we_do_not_cover():
    result = topic_gaps(OURS, _their_page())
    assert result.measurable
    assert any("link building" in g.lower() for g in result.gaps)
    assert not any("technical audits" in g.lower() for g in result.gaps), (
        "we do cover technical audits"
    )


def test_topic_gaps_abstains_when_no_competitor_headings_were_retrieved():
    result = topic_gaps(OURS, _their_page(h1s=[], h2s=[]))
    assert result.measurable is False


def test_content_depth_abstains_when_body_text_is_unavailable():
    result = content_depth(OURS, _their_page(depth_available=False, source="cached"))
    assert result.measurable is False
    assert "cached" in result.reason
    assert result.score is None, "must not score a cached page as zero words"


def test_content_depth_compares_word_counts_when_available():
    result = content_depth(OURS, _their_page())
    assert result.measurable
    assert isinstance(result.ours, int) and isinstance(result.theirs, int)


def test_a_much_longer_competitor_page_lowers_our_depth_score():
    short = content_depth({"page_markdown": "a few words only"}, _their_page())
    long = content_depth(OURS, _their_page())
    assert short.score < long.score


def test_heading_structure_flags_multiple_h1s():
    result = heading_structure({**OURS, "h1s": ["One", "Two"]}, _their_page())
    assert result.measurable
    assert any("exactly one" in r for r in result.recommendations)
    assert result.score <= 40


def test_heading_structure_flags_a_missing_h2_spine():
    result = heading_structure({"h1s": ["Only H1"], "page_markdown": "x"}, _their_page())
    assert any("no H2 spine" in r for r in result.recommendations)


def test_readability_abstains_on_a_short_draft():
    result = readability({"page_markdown": "too short"}, _their_page())
    assert result.measurable is False


def test_readability_abstains_when_competitor_depth_is_missing():
    result = readability(OURS, _their_page(depth_available=False, source="cached"))
    assert result.measurable is False
    assert "unavailable" in result.reason


def test_readability_compares_when_both_sides_have_text():
    result = readability(OURS, _their_page())
    assert result.measurable
    assert isinstance(result.ours, float)


def test_onpage_seo_flags_an_element_they_have_and_we_lack():
    bare = {"title": "T", "page_markdown": "words " * 60}
    result = onpage_seo(bare, _their_page())
    assert result.measurable
    assert "schema" in result.gaps
    assert any("schema" in r for r in result.recommendations)


def test_onpage_seo_flags_an_overlong_title():
    long_title = {**OURS, "meta_title": "x" * 75}
    result = onpage_seo(long_title, _their_page())
    assert any("truncate" in r for r in result.recommendations)


def test_compare_one_runs_every_dimension_and_never_raises():
    results = compare_one(OURS, _their_page(), target_keywords=["seo services"])
    assert len(results) == 6
    keys = {r.key for r in results}
    assert keys == {
        "keyword_coverage", "topic_gaps", "content_depth",
        "heading_structure", "readability", "onpage_seo",
    }


def test_a_dimension_that_raises_is_reported_not_propagated():
    def boom(ours, theirs, **kw):
        raise ValueError("nope")

    with patch("app.services.content_benchmark.dimensions.DIMENSIONS", (boom,)):
        results = compare_one(OURS, _their_page())
    assert results[0].measurable is False
    assert "Dimension failed" in results[0].reason


# --- report ----------------------------------------------------------------


def test_service_terms_drops_stopwords():
    assert "the" not in service_terms("The SEO Services", ["for you"])
    assert "seo" in service_terms("The SEO Services")


async def test_report_with_no_competitors_says_so_and_does_not_fail():
    report = await run_benchmark(
        service_id="seo", service_name="SEO", our_content=OURS, competitors=[]
    )
    assert report["competitors"] == []
    assert report["overall"] is None
    assert any("No competitors are selected" in n for n in report["notes"])


async def test_report_scores_each_competitor_and_overall():
    async def ok(competitor, *, service_terms):
        return _their_page(competitor_id=competitor["id"], name=competitor["name"])

    with patch.object(fetch_mod, "from_live_fetch", ok):
        report = await run_benchmark(
            service_id="seo", service_name="SEO Services", our_content=OURS,
            competitors=COMPETITORS, target_keywords=["seo services", "link building"],
        )
    assert len(report["competitors"]) == 2
    assert all(r["score"] is not None for r in report["competitors"])
    assert report["overall"]["score"] is not None
    assert report["overall"]["competitors_compared"] == 2
    assert report["overall"]["verdict"]
    assert report["recommendations"]
    assert report["gaps"]


async def test_one_failed_competitor_degrades_that_row_only():
    async def half(competitor, *, service_terms):
        if competitor["id"] == "c2":
            out = PageContent(competitor_id="c2", name="WebFX", domain="webfx.com")
            out.error = "timeout"
            return out
        return _their_page()

    with patch.object(fetch_mod, "from_live_fetch", half):
        report = await run_benchmark(
            service_id="seo", service_name="SEO Services", our_content=OURS,
            competitors=COMPETITORS, target_keywords=["seo services"],
        )
    rows = {r["competitor_id"]: r for r in report["competitors"]}
    assert rows["c1"]["ok"] is True and rows["c1"]["dimensions"]
    assert rows["c2"]["ok"] is False and rows["c2"]["verdict"] == "content unavailable"
    assert report["overall"]["competitors_compared"] == 1
    assert report["overall"]["competitors_failed"] == 1
    assert any("could not be retrieved" in n for n in report["notes"])


async def test_report_notes_when_no_depth_was_available_anywhere():
    with patch.object(fetch_mod, "from_live_fetch", None), patch.object(
        fetch_mod, "from_cached_snapshots",
        lambda c, *, snapshots, service_terms: from_cached_snapshots(
            c, snapshots=SNAPSHOTS, service_terms=service_terms
        ),
    ):
        report = await run_benchmark(
            service_id="seo", service_name="SEO Services", our_content=OURS,
            competitors=[COMPETITORS[0]], snapshots=SNAPSHOTS, allow_live=False,
        )
    assert any("depth and readability were skipped" in n for n in report["notes"])


async def test_report_without_our_content_still_surfaces_their_gaps():
    async def ok(competitor, *, service_terms):
        return _their_page()

    with patch.object(fetch_mod, "from_live_fetch", ok):
        report = await run_benchmark(
            service_id="seo", service_name="SEO Services", our_content=None,
            competitors=[COMPETITORS[0]], target_keywords=["seo services"],
        )
    assert any("No generated content" in n for n in report["notes"])
    assert report["gaps"], "their topics are still worth showing"


def test_recommendations_are_ranked_and_deduplicated():
    per_competitor = [
        {
            "ok": True, "name": "A",
            "dimensions": [
                {"key": "topic_gaps", "measurable": True, "score": 10,
                 "recommendations": ["Add a section on link building"], "gaps": []},
                {"key": "readability", "measurable": True, "score": 90,
                 "recommendations": ["Trim sentence length"], "gaps": []},
            ],
        },
        {
            "ok": True, "name": "B",
            "dimensions": [
                {"key": "topic_gaps", "measurable": True, "score": 20,
                 "recommendations": ["Add a section on link building"], "gaps": []},
            ],
        },
    ]
    recs = prioritise_recommendations(per_competitor)
    assert recs[0]["recommendation"] == "Add a section on link building"
    assert recs[0]["priority"] == 1
    assert recs[0]["raised_by"] == ["A", "B"], "de-duplicated, both attributed"
    assert len(recs) == 2


def test_failed_competitors_contribute_no_recommendations():
    assert prioritise_recommendations([{"ok": False, "dimensions": []}]) == []
