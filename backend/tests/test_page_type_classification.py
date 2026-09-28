"""Page-type classification must read the keyword, not fall back to "service".

The committed classifier ended:

    if intent == "commercial":
        return "service"
    if weak and "/blog/" not in url and "/guides/" not in url:
        return "service"   # <- discards an EXPLICIT blog/guide/article label
    if weak:
        return weak[0]
    return "service"       # <- catch-all

Three separate paths to "service": a catch-all default, an override that threw
away an explicit content-type label whenever the URL didn't already contain
/blog/ or /guides/, and no branch at all for informational intent. Keyword-side
guide detection was only `startswith("how to")`, so "what is SEO" and "step by
step guide to google ads setup" matched nothing and defaulted to "service".

Order matters here: an informational phrasing has to beat a commercial noun.
"how to choose a digital marketing agency" contains "agency" but is a guide.
"""

from __future__ import annotations

import pytest

from app.services.create_content import resolve_page_type

# (keyword, intent, acceptable page types)
CASES = [
    ("how to choose a digital marketing agency", None, {"guide"}),
    ("what is SEO", None, {"guide", "blog"}),
    ("step by step guide to google ads setup", None, {"guide"}),
    ("google ads setup tutorial", None, {"guide"}),
    ("email marketing checklist", None, {"guide", "blog"}),
    ("seo tips for beginners", None, {"guide", "blog"}),
    ("SEO services in Bengaluru", None, {"service"}),
    ("email marketing agency melbourne", None, {"service"}),
    ("hire an seo consultant", None, {"service"}),
    ("best email marketing tools", None, {"listicle", "blog"}),
    ("google ads vs facebook ads", None, {"comparison"}),
    ("email marketing pricing", None, {"landing", "service"}),
]


@pytest.mark.parametrize("keyword,intent,expected", CASES)
def test_keyword_drives_the_page_type(keyword, intent, expected):
    got = resolve_page_type({"keyword": keyword, "intent": intent} if intent else {"keyword": keyword})
    assert got in expected, f"{keyword!r} -> {got!r}, expected one of {sorted(expected)}"


@pytest.mark.parametrize("keyword,_intent,expected", CASES)
def test_informational_intent_does_not_flip_a_service_to_a_guide(keyword, _intent, expected):
    """Intent is a tiebreaker, not an override of an explicit keyword signal."""
    got = resolve_page_type({"keyword": keyword, "intent": "informational"})
    if expected == {"service"}:
        assert got == "service", f"{keyword!r} -> {got!r}"


@pytest.mark.parametrize("keyword,_intent,expected", CASES)
def test_commercial_intent_does_not_flatten_a_guide_to_a_service(keyword, _intent, expected):
    """The reported bug: commercial intent collapsed everything to "service"."""
    got = resolve_page_type({"keyword": keyword, "intent": "commercial"})
    if "guide" in expected or "blog" in expected:
        assert got != "service", f"{keyword!r} -> {got!r}"


# --- explicit labels must survive -----------------------------------------


@pytest.mark.parametrize("label", ["blog", "guide", "article", "listicle"])
def test_an_explicit_content_type_is_never_overridden_by_service(label):
    """`weak and "/blog/" not in url` returned "service" and threw the label away."""
    got = resolve_page_type(
        {"keyword": "email marketing agency", "intent": "commercial", "content_type": label}
    )
    assert got != "service", f"explicit {label!r} was overridden -> {got!r}"


def test_an_explicit_service_label_still_wins():
    assert (
        resolve_page_type({"keyword": "what is seo", "page_type": "service"}) == "service"
    )


# --- edge cases ------------------------------------------------------------


def test_mixed_case_and_whitespace_are_handled():
    for raw in ("  WHAT IS SEO  ", "What Is Seo", "what is seo\n"):
        assert resolve_page_type({"keyword": raw}) in {"guide", "blog"}, raw


def test_mixed_case_service_keyword():
    assert resolve_page_type({"keyword": "  SEO Services In Bengaluru "}) == "service"


def test_empty_input_is_neutral_not_service():
    for src in ({}, None, {"keyword": ""}, {"keyword": "   "}):
        got = resolve_page_type(src)
        assert got != "service", f"{src!r} -> {got!r}"
        assert got == "page"


def test_an_unrecognised_keyword_is_neutral_not_service():
    """The catch-all default was the single biggest cause of the bug."""
    got = resolve_page_type({"keyword": "zxcv qwerty asdf"})
    assert got == "page", got


def test_a_url_signal_still_wins_over_the_keyword():
    assert resolve_page_type({"url": "/services/seo/", "keyword": "what is seo"}) == "service"
    assert resolve_page_type({"url": "/blog/what-is-seo/", "keyword": "seo services"}) == "blog"


def test_the_neutral_fallback_logs_a_warning():
    """A silent default is how this went unnoticed. It must say so — with the
    keyword, so the gap is diagnosable from the logs."""
    from unittest.mock import patch

    from app.services import create_content

    with patch.object(create_content.log, "warning") as warn:
        assert resolve_page_type({"keyword": "zxcv qwerty asdf"}) == "page"
    warn.assert_called_once()
    event, kwargs = warn.call_args[0][0], warn.call_args[1]
    assert event == "page_type_unclassified"
    assert kwargs["keyword"] == "zxcv qwerty asdf"
    assert kwargs["fallback"] == "page"


def test_a_successful_classification_logs_nothing():
    from unittest.mock import patch

    from app.services import create_content

    with patch.object(create_content.log, "warning") as warn:
        resolve_page_type({"keyword": "what is seo"})
        resolve_page_type({"keyword": "seo services in bengaluru"})
    warn.assert_not_called()
