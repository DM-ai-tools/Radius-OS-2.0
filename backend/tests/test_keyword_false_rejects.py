"""Buyer queries the cleansing rules used to drop, and free-checker queries they used to keep."""

from __future__ import annotations

import pytest

from app.services.keyword_exclusions import buyer_fit_reject
from app.services.keyword_service_contract import _acronym_homonym

BUYER_QUERIES = (
    "ppc ads",
    "ppc advertising",
    "seo advertising",
    "cheapest ppc advertising",
    "seo help",
    "seo reseller",
    "seo outsourcing",
    "seo campaign",
    "ppc campaign",
    "advanced seo",
    "seo analysis",
    "seo competitor analysis",
    "seo coach",
)

NOT_BUYERS = (
    "park seo joon",
    "seo kang joon",
    "seo ye ji",
    "define seo",
    "seo meaning",
)

FREE_CHECKERS = (
    "google rank test",
    "google ranking test online",
    "google local ranking check",
    "test my website seo",
    "email deliverability test",
)


@pytest.mark.parametrize("keyword", BUYER_QUERIES)
def test_a_buyer_query_is_not_called_an_acronym_homonym(keyword):
    assert _acronym_homonym(keyword) is False


@pytest.mark.parametrize("keyword", NOT_BUYERS)
def test_names_and_dictionary_lookups_are_still_homonyms(keyword):
    assert _acronym_homonym(keyword) is True


@pytest.mark.parametrize("keyword", FREE_CHECKERS)
def test_free_checker_queries_are_dropped_for_an_agency(keyword):
    hit = buyer_fit_reject(
        keyword,
        industry="digital marketing agency",
        services=["SEO", "PPC Management", "Email Marketing"],
        geography="Australia",
    )
    assert hit is not None and hit[0] == "tool_seeker", keyword


@pytest.mark.parametrize("keyword", ("seo testing services", "seo agency melbourne", "ppc management cost"))
def test_agency_queries_are_not_called_tool_seekers(keyword):
    assert (
        buyer_fit_reject(
            keyword,
            industry="digital marketing agency",
            services=["SEO", "PPC Management"],
            geography="Australia",
        )
        is None
    )
