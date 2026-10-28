"""Place detection and the settings behind the cross-client keyword pool.

Only keywords that mention no place are saved for other clients to reuse, so
the place test decides what may leave one client's run. These tests pin the
words that make a keyword location specific, the client's own places, and the
defaults of the settings the pool, industry layer and secondary keywords read.
"""

from __future__ import annotations

from app.config import get_settings
from app.services.competitive_context import resolve_geographic_focus
from app.services.shared_keyword_pool.location import (
    COUNTRY_NAMES,
    MODIFIER_WORDS,
    client_places,
    is_country_name,
    is_location_specific,
)


def test_place_words_make_a_keyword_location_specific():
    assert is_location_specific("dentist melbourne")
    assert is_location_specific("dentist near me")
    assert is_location_specific("dentist toronto")
    assert not is_location_specific("teeth whitening cost")
    assert not is_location_specific("invisalign vs braces")
    assert not is_location_specific("teeth whitening")


def test_the_clients_own_places_count():
    assert is_location_specific("dentist in bondi", places=["bondi"])
    assert not is_location_specific("dentist in bondi")
    assert is_location_specific("seo australia", places=["australia"])


def test_client_places_come_from_the_geography():
    geo = {
        "city": "Perth",
        "location_name": "Western Australia",
        "geographic_focus": "Perth, Fremantle",
    }
    assert client_places(geo) == ["perth", "western australia", "fremantle"]
    assert client_places({}) == []


def test_settings_defaults():
    settings = get_settings()
    assert settings.keyword_pool_enabled is True
    assert settings.keyword_pool_ttl_days == 180
    assert settings.keyword_pool_place_cap == 5
    assert settings.keyword_industry_max_seeds == 8
    assert settings.keyword_industry_informational_share == 0.10
    assert settings.keyword_secondary_target == 5
    assert settings.keyword_secondary_lookup_cap == 1000


def test_modifier_words_include_the_owners_examples():
    for word in ("best", "company", "agency", "firm"):
        assert word in MODIFIER_WORDS


def test_place_words_match_whole_words_only():
    assert not is_location_specific("duke university dental plan")
    assert not is_location_specific("")
    assert is_location_specific("dentist nearby")
    assert is_location_specific("Dentist, Near Me!")


def test_client_places_from_the_real_resolver_output():
    # location_name comes back comma-joined ("Los Angeles,California,United States"):
    # each part is a place on its own, never the joined string.
    assert client_places(resolve_geographic_focus({"geographic_focus": "Los Angeles"})) == [
        "los angeles",
        "california",
        "united states",
    ]
    sydney = client_places(resolve_geographic_focus({"geographic_focus": "Sydney, Bondi"}))
    assert sydney == ["sydney", "new south wales", "australia", "bondi"]
    assert not any("," in place for place in sydney)
    assert is_location_specific("dentist in bondi", places=sydney)


def test_a_client_with_no_geography_has_no_places():
    # With no geography the resolver falls back to "United States" as a national
    # default. That is not the client's place, so it must not be listed.
    assert client_places(resolve_geographic_focus({"geographic_focus": ""})) == []
    assert client_places({"location_name": "United States"}) == []


def test_client_places_fold_spacing_and_deduplicate():
    geo = {
        "city": "Sydney",
        "location_name": "Sydney,New South Wales,Australia",
        "geographic_focus": "New  South Wales, sydney",
    }
    assert client_places(geo) == ["sydney", "new south wales", "australia"]


def test_a_multi_word_own_place_matches_as_a_phrase():
    assert is_location_specific("dentist in new farm", places=["new farm"])
    assert not is_location_specific("dentist in new farm")
    assert not is_location_specific("dentist farm", places=["new farm"])


def test_accents_are_folded_when_matching_places():
    assert is_location_specific("dentist zurich", places=["Z\u00fcrich"])
    assert is_location_specific("dentist Z\u00fcrich", places=["zurich"])
    assert client_places({"city": "Z\u00fcrich", "geographic_focus": "zurich"}) == ["zurich"]


def test_local_alone_is_not_a_place_but_a_place_beside_it_is():
    assert not is_location_specific("local seo")
    assert is_location_specific("local seo melbourne")


def test_a_country_name_makes_a_keyword_location_specific():
    for keyword in (
        "seo india",
        "dentist singapore",
        "seo uk",
        "web design germany",
        "seo france",
        "seo united arab emirates",
        "plumber usa",
    ):
        assert is_location_specific(keyword), keyword


def test_every_country_name_is_a_place():
    assert COUNTRY_NAMES
    for name in COUNTRY_NAMES:
        assert is_country_name(name)
        assert is_location_specific(name), name
        assert is_location_specific(f"seo {name}"), name


def test_country_names_match_whole_words_only():
    assert not is_location_specific("duke university dental plan")
    assert not is_location_specific("indiana dentist")
    assert not is_location_specific("francesca hair salon")
    assert not is_location_specific("germanic languages course")


def test_country_adjectives_are_not_country_names():
    for adjective in ("american", "indian", "british", "german", "french"):
        assert not is_country_name(adjective)
        assert not is_location_specific(f"{adjective} dentist")
