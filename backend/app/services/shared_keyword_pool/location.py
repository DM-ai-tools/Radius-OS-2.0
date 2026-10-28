"""Place detection for the keyword pool.

Only place-free keywords are saved for other clients to reuse, so one client's
city never lands in another client's results. ``is_location_specific`` decides
that, and ``client_places`` lists the places that belong to the current client.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any

from app.services.keyword_exclusions import _LOCAL_WORDS
from app.services.keyword_service_contract import _AU_CITIES, _FOREIGN_CITIES

# Words that qualify a keyword without changing what it is about. The URL-map
# qualifier logic and the secondary-keyword variants share this list.
# It holds single words and multi-word phrases ("near me", "how to"), so callers
# must match by phrase, not by single token.
MODIFIER_WORDS: frozenset[str] = frozenset(
    {
        "best",
        "top",
        "cost",
        "price",
        "pricing",
        "company",
        "companies",
        "agency",
        "agencies",
        "firm",
        "services",
        "service",
        "consultant",
        "provider",
        "cheap",
        "affordable",
        "near me",
        "vs",
        "how to",
        "what is",
    }
)

# Country names, with the short forms people type. This is the one list of countries.
# It covers every country the geography resolver falls back to when a focus is unmapped
# (it names the country as ``location_name``). A country is a place, so a keyword that
# names one ("seo india") is not shared; it is not one of the client's own places for
# the place lookup. Adjectives ("american", "indian") are not country names.
COUNTRY_NAMES: frozenset[str] = frozenset(
    {
        "australia",
        "new zealand",
        "united states",
        "usa",
        "united kingdom",
        "uk",
        "great britain",
        "england",
        "scotland",
        "wales",
        "canada",
        "ireland",
        "india",
        "singapore",
        "germany",
        "france",
        "united arab emirates",
        "uae",
    }
)

# Australian states and territories (spelled out; "wa", "sa", "nt" and "act"
# are also ordinary words). The city lists cover the rest.
_REGION_NAMES: frozenset[str] = frozenset(
    {
        "new south wales",
        "nsw",
        "victoria",
        "vic",
        "queensland",
        "qld",
        "western australia",
        "south australia",
        "tasmania",
        "tas",
        "northern territory",
        "australian capital territory",
    }
)

# The one adjective already treated as a place word ("australian seo").
_PLACE_ADJECTIVES: frozenset[str] = frozenset({"australian"})

_STATES_AND_COUNTRIES: frozenset[str] = _REGION_NAMES | COUNTRY_NAMES | _PLACE_ADJECTIVES

# Phrases that always point at the searcher's own location.
_ALWAYS_LOCAL: frozenset[str] = frozenset({"near me", "nearby"})

# Entries of the imported ``_LOCAL_WORDS`` that are not places. "local" alone
# ("local seo") names no place; a place beside it ("local seo melbourne") still counts.
_NOT_PLACE_WORDS: frozenset[str] = frozenset({"local"})

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalise(text: Any) -> str:
    """Lowercase, fold accents (u with an umlaut becomes u), and collapse punctuation and spacing."""
    decomposed = unicodedata.normalize("NFKD", str(text or "")).lower()
    folded = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_WORD_RE.sub(" ", folded).strip()


_PLACE_PHRASES: frozenset[str] = frozenset(
    phrase
    for phrase in map(
        _normalise,
        _AU_CITIES
        | _FOREIGN_CITIES
        | (_LOCAL_WORDS - _NOT_PLACE_WORDS)
        | _STATES_AND_COUNTRIES
        | _ALWAYS_LOCAL,
    )
    if phrase
)


def is_location_specific(keyword: str, places: Iterable[str] = ()) -> bool:
    """True when the keyword names a place, so it must not be shared across clients.

    Matches whole words and whole phrases on the normalised keyword, so "uk"
    does not fire inside "duke". ``places`` are the current client's own places.
    """
    words = _normalise(keyword).split()
    if not words:
        return False
    phrases = _PLACE_PHRASES
    own = {phrase for phrase in map(_normalise, places) if phrase}
    if own:
        phrases = phrases | own
    longest = max(len(phrase.split()) for phrase in phrases)
    for size in range(1, min(longest, len(words)) + 1):
        for start in range(len(words) - size + 1):
            if " ".join(words[start : start + size]) in phrases:
                return True
    return False


def is_country_name(place: str) -> bool:
    """True when ``place`` is a country, not somewhere inside the client's market."""
    return _normalise(place) in COUNTRY_NAMES


def is_region_name(place: str) -> bool:
    """True when ``place`` is a state or territory, a wider area than a city or suburb."""
    return _normalise(place) in _REGION_NAMES


def client_places(geo: Mapping[str, Any]) -> list[str]:
    """The client's own place names, normalised and de-duplicated in first-seen order.

    Reads ``city``, ``location_name`` and the comma-separated
    ``geographic_focus`` from the resolved geography. ``location_name`` is
    comma-joined ("Sydney,New South Wales,Australia"), so each part is a place.
    With neither a city nor a geographic focus, ``location_name`` is only the
    national default and the client has no places.
    """
    focus = geo.get("geographic_focus")
    focus_parts: list[Any] = []
    if isinstance(focus, str):
        focus_parts = focus.split(",")
    elif isinstance(focus, (list, tuple)):
        focus_parts = list(focus)
    city = geo.get("city")
    if not _normalise(city) and not any(_normalise(part) for part in focus_parts):
        return []
    raw: list[Any] = [city]
    raw.extend(str(geo.get("location_name") or "").split(","))
    raw.extend(focus_parts)
    places: list[str] = []
    for item in raw:
        place = _normalise(item)
        if place and place not in places:
            places.append(place)
    return places
