"""An ordinary short word is not an acronym.

`_acronym_candidates` promoted ANY 2-5 letter alphabetic token from the page's
keyword/title to an acronym and upper-cased it everywhere, so
"conversion rate optimisation" rendered as "Conversion RATE Optimisation".

Length alone cannot separate "AEO" from "rate". The evidence that is actually
available is the casing in the source: a genuine acronym is written uppercase
("AEO", "HVAC", "CRO"), an ordinary word is not. Known acronyms stay forced from
_ACRONYM_FORCE, so a lower-cased slug like "seo-services" still yields "SEO".
"""

from __future__ import annotations

import pytest

from app.services.create_content import _acronym_candidates, _normalize_acronyms


# --- ordinary words must not be promoted ----------------------------------


@pytest.mark.parametrize(
    "word", ["rate", "cost", "plan", "team", "help", "guide", "best", "near", "page", "time"]
)
def test_an_ordinary_lowercase_word_is_not_an_acronym(word):
    mapping = _acronym_candidates(f"conversion {word} optimisation")
    assert word not in mapping, f"{word!r} was promoted to an acronym"


def test_the_reported_case_renders_correctly():
    assert (
        _normalize_acronyms(
            "Conversion Rate Optimisation Ecommerce",
            "conversion-rate-optimisation-ecommerce",
        )
        == "Conversion Rate Optimisation Ecommerce"
    )


def test_a_title_cased_word_is_not_an_acronym():
    mapping = _acronym_candidates("Conversion Rate Optimisation")
    assert "rate" not in mapping


# --- genuine acronyms must still work -------------------------------------


def test_a_known_acronym_is_forced_even_from_a_lowercase_slug():
    """Slugs are lower-cased, so known acronyms cannot rely on casing."""
    assert _normalize_acronyms("Seo Services Melbourne", "seo-services-melbourne") == (
        "SEO Services Melbourne"
    )
    assert _normalize_acronyms("Cro Audit", "cro-audit") == "CRO Audit"


@pytest.mark.parametrize("acronym", ["AEO", "HVAC", "EHR", "GEO", "PPC"])
def test_an_unknown_acronym_written_uppercase_is_detected(acronym):
    mapping = _acronym_candidates(f"{acronym} services australia")
    assert mapping.get(acronym.lower()) == acronym


def test_an_uppercase_acronym_is_applied_to_the_body():
    assert _normalize_acronyms("Our aeo service", "AEO services") == "Our AEO service"


def test_mixed_case_known_forms_survive():
    """SaaS is in the forced list and must not become SAAS."""
    assert _normalize_acronyms("Saas pricing", "saas-pricing") == "SaaS pricing"


def test_a_long_uppercase_word_is_not_treated_as_an_acronym():
    mapping = _acronym_candidates("MARKETING services")
    assert "marketing" not in mapping


def test_no_seed_means_only_the_known_list_applies():
    mapping = _acronym_candidates("")
    assert "seo" in mapping
    assert "rate" not in mapping
