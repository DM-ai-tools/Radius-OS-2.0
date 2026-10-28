"""Secondary keywords on every URL-map row.

A row whose cluster gave it fewer than ``keyword_secondary_target`` secondary keywords
is topped up with variants of its primary keyword: the primary with a modifier in its
natural place ("best seo", "seo company"). A variant is kept only when it has a measured
search volume, and only when it passes the client's relevance and buyer-fit filters like
any other keyword. A row with no measured variant stays empty: nothing is filled in
unmeasured. A question, checklist, guide, or comparison gets no variants. A head
term that is only labelled informational still does.

Kept variants are appended as plain strings. Nothing on the row says where a secondary
keyword came from.

All the run's candidates go to DataForSEO in one batched search-volume call. A lookup is
one keyword with its measured volume, so it is place-free and is saved in the shared
keyword pool (``kind="lookup"``) for later clients, as long as it was measured at the
country level.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.config import get_settings
from app.integrations import dataforseo
from app.logging_config import get_logger
from app.services.shared_keyword_pool.location import client_places, is_location_specific
from app.services.shared_keyword_pool.store import SeedSave, load_seed, save_seeds_bulk, seed_key

log = get_logger("secondary_keywords")

SECONDARY_MODIFIERS: tuple[str, ...] = (
    "best",
    "top",
    "company",
    "agency",
    "firm",
    "services",
    "consultant",
    "provider",
    "cheap",
    "affordable",
)

# Where each modifier sits. "best seo" and "seo company" read naturally; "company seo" and
# "seo best" do not, and a modifier not listed here is placed after the primary.
PREFIX_MODIFIERS: frozenset[str] = frozenset({"best", "top", "cheap", "affordable"})
# A primary that already names a provider ("digital marketing agency") takes none of the
# suffix modifiers: "digital marketing agency company" is not a search.
_PROVIDER_NOUNS: frozenset[str] = frozenset(
    {
        "company",
        "companies",
        "agency",
        "agencies",
        "firm",
        "firms",
        "service",
        "services",
        "consultant",
        "consultants",
        "provider",
        "providers",
    }
)
# A head term is often labelled informational, and a SERP of "best" or "top" titles
# turns it into a blog or listicle. That label is not evidence the search is a
# question. The primary keyword is: a question word first, or one of these words
# as a whole word, means the row is informational whatever its intent label.
_QUESTION_WORDS: frozenset[str] = frozenset(
    {"what", "how", "why", "when", "who", "where", "which", "can", "does", "is", "are", "should"}
)
_INFORMATIONAL_WORDS: frozenset[str] = frozenset(
    {
        "checklist",
        "checklists",
        "tips",
        "course",
        "courses",
        "guide",
        "guides",
        "tutorial",
        "tutorials",
        "template",
        "templates",
        "example",
        "examples",
        "meaning",
        "definition",
        "ideas",
        "training",
    }
)
_CONTENT_INTENTS: frozenset[str] = frozenset({"informational"})
_BUYER_INTENTS: frozenset[str] = frozenset({"commercial", "transactional"})
_COMPARISON_PAGE_TYPES: frozenset[str] = frozenset({"comparison"})
# A primary keyword with one of these words is a comparison however the row is typed.
_COMPARISON_WORDS: frozenset[str] = frozenset(
    {"vs", "versus", "alternative", "alternatives", "compared", "comparison", "compare"}
)
_CONTENT_PAGE_TYPES: frozenset[str] = frozenset({"guide", "faq", "blog", "article", "listicle"})
_WORD_RE = re.compile(r"[a-z0-9]+")

# A row never carries more than this many secondary keywords (the URL map's own cap).
SECONDARY_ROW_CAP = 10
# The volume floor every keyword in the pipeline already has to meet.
_MIN_VOLUME = 10
# The keyword pool's kind for a one-keyword volume lookup (seed fetches are "seed").
POOL_KIND = "lookup"
_READ_CONCURRENCY = 8
_DEFAULT_LOCATION = 2840
_DEFAULT_LANGUAGE = "en"


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _stem(word: str) -> str:
    """Enough of a stem to see that "agencies" is "agency" and "services" is "service"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _contains_modifier(primary_words: Sequence[str], modifier: str) -> bool:
    """True when the primary already says this modifier (singular or plural)."""
    words = modifier.split()
    if len(words) == 1:
        return _stem(words[0]) in {_stem(word) for word in primary_words}
    padded = f" {' '.join(primary_words)} "
    return f" {modifier} " in padded


def modifier_candidates(
    primary: str,
    existing: Iterable[str],
    modifiers: Iterable[str] = SECONDARY_MODIFIERS,
    *,
    places: Iterable[str] = (),
) -> list[str]:
    """The primary with each modifier in its natural place, in modifier order.

    ``best``, ``top``, ``cheap`` and ``affordable`` go before the primary ("best seo"); every
    other modifier goes after it ("seo company"). Left out: a candidate that names a place
    (built-in places and the client's own ``places``), an empty primary, a candidate equal
    to the primary or already among ``existing``, a modifier the primary already contains,
    every prefix modifier when the primary already starts with one ("top best seo"), every
    suffix modifier when the primary already names a provider (company, agency, firm,
    services, consultant, provider), and a candidate the provider would clean to different
    text (its volume would belong to another keyword).
    """
    base = _norm(primary)
    if not base:
        return []
    own_places = list(places)
    words = _WORD_RE.findall(base)
    names_a_provider = bool(set(words) & _PROVIDER_NOUNS)
    starts_with_prefix = bool(words) and words[0] in PREFIX_MODIFIERS
    skip = {_norm(item) for item in existing}
    skip.add(base)
    out: list[str] = []
    for modifier in modifiers:
        text = _norm(modifier)
        if not text or _contains_modifier(words, text):
            continue
        if text in PREFIX_MODIFIERS:
            if starts_with_prefix:
                continue
            candidate = f"{text} {base}"
        elif names_a_provider:
            continue
        else:
            candidate = f"{base} {text}"
        if (
            candidate in skip
            or candidate in out
            or dataforseo.sanitize_keyword(candidate) != candidate
            or is_location_specific(candidate, own_places)
        ):
            continue
        out.append(candidate)
    return out


def _informational_form(primary: str) -> bool:
    """True when the primary is a question or names a guide, checklist, course, or similar.

    A question word counts only as the first word ("what is seo", not "seo how to").
    An informational word counts only as a whole word ("seo guide", not "guided tours").
    """
    words = _WORD_RE.findall(_norm(primary))
    if not words:
        return False
    if words[0] in _QUESTION_WORDS:
        return True
    return bool(_INFORMATIONAL_WORDS & set(words))


def _wants_variants(entry: Mapping[str, Any]) -> bool:
    """False for a question, a checklist-style primary, or a comparison.

    A head term labelled informational is still topped up. The label is a frequent
    miss on service head terms, and a SERP can also mark the page a blog or listicle.
    Skipped: a primary that is itself a question or contains an informational word;
    a primary that holds a comparison word as a whole word ("seo vs sem"); a
    comparison page type unless the intent is commercial or transactional; and a
    guide, faq, blog, article, or listicle whose intent is present and is neither
    a buyer's nor informational (a navigational row on a blog). A row with no
    intent is skipped for a comparison page type and topped up for a content page type.
    """
    intent = _norm(entry.get("search_intent") or entry.get("intent"))
    page_type = _norm(entry.get("ia_page_type"))
    primary = _norm(entry.get("primary_keyword"))
    if _informational_form(primary):
        return False
    if _COMPARISON_WORDS & set(_WORD_RE.findall(primary)):
        return False
    if page_type in _COMPARISON_PAGE_TYPES and intent not in _BUYER_INTENTS:
        return False
    if (
        page_type in _CONTENT_PAGE_TYPES
        and intent
        and intent not in _BUYER_INTENTS
        and intent not in _CONTENT_INTENTS
    ):
        return False
    return True


def _current(entry: Mapping[str, Any]) -> list[str]:
    """The secondary keywords the reports show: the sheet column, else the cluster list."""
    for key in ("secondary_keywords_sheet", "secondary_keywords"):
        value = entry.get(key)
        if not isinstance(value, list):
            continue
        texts = []
        for item in value:
            text = " ".join(str((item.get("keyword") if isinstance(item, dict) else item) or "").split())
            if text:
                texts.append(text)
        if texts:
            return texts
    return []


def _append(entry: dict[str, Any], key: str, additions: Sequence[str]) -> None:
    """Add plain strings to one list on the row, never past the row cap or twice."""
    existing = entry.get(key)
    merged = [item for item in existing if isinstance(item, str)] if isinstance(existing, list) else []
    seen = {_norm(item) for item in merged}
    for text in additions:
        if len(merged) >= SECONDARY_ROW_CAP:
            break
        if _norm(text) not in seen:
            merged.append(text)
            seen.add(_norm(text))
    entry[key] = merged


# ---------------------------------------------------------------- the keyword pool


def _pool_usable(location_code: int | None, country_location_code: int | None, language_code: str | None) -> bool:
    """The pool is used only for a real provider, a language, and a country-level location.

    Off when the pool is switched off, the provider is in sandbox (its data is fake and must
    never be saved), providers are mocked, or the lookup was not measured at the country
    level (a city's volumes are not the country's).
    """
    settings = get_settings()
    try:
        same_place = bool(location_code) and int(location_code) == int(country_location_code)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        same_place = False
    return bool(
        settings.keyword_pool_enabled
        and same_place
        and language_code
        and not getattr(settings, "dataforseo_sandbox", False)
        and not settings.use_mock_providers
    )


async def _read_cached(
    candidates: Sequence[str], *, location_code: int, language_code: str
) -> tuple[dict[str, dict], bool]:
    """Saved lookups: ({candidate: its saved row}, pool_still_ok)."""
    cached: dict[str, dict] = {}
    state = {"ok": True}
    gate = asyncio.Semaphore(_READ_CONCURRENCY)

    async def one(candidate: str) -> None:
        if not state["ok"]:
            return
        async with gate:
            try:
                key = seed_key(candidate)
                hit = None
                if key:
                    hit = await load_seed(
                        key,
                        kind=POOL_KIND,
                        location_code=location_code,
                        language_code=language_code,
                        min_volume=0,
                        limit=1,
                    )
            except Exception as exc:  # noqa: BLE001 - the pool is optional; measure without it
                state["ok"] = False
                log.warning("secondary_keywords_pool_error", stage="read", error=type(exc).__name__)
                return
        if hit is None:
            return
        # The pool also serves a fetch of the same service worded differently. A lookup is
        # one keyword, so only a saved row of this very keyword counts.
        for row in hit.rows:
            if _norm(row.get("keyword")) == candidate:
                cached[candidate] = row
                return

    await asyncio.gather(*(one(candidate) for candidate in candidates))
    return cached, state["ok"]


async def _save_lookups(
    fetched: Mapping[str, dict | None], *, location_code: int, language_code: str
) -> None:
    """Save each looked-up keyword as its own one-row lookup, all in one commit.

    Nothing here may raise: the measured volumes are in the caller's hands, and a lookup
    that cannot be built or saved is only left out of the pool.
    """
    items = []
    for candidate, row in fetched.items():
        try:
            key = seed_key(candidate)
            if key:
                # No row from the provider is saved as "looked up, nothing measured".
                rows = [dict(row) if row is not None else {"keyword": candidate, "volume": None}]
                items.append(SeedSave(key=key, seed_text=candidate, rows=rows))
        except Exception as exc:  # noqa: BLE001 - leave this one out of the pool
            log.warning("secondary_keywords_pool_error", stage="save", error=type(exc).__name__)
    try:
        await save_seeds_bulk(
            items,
            location_code=location_code,
            language_code=language_code,
            min_volume=0,
            limit=1,
            kind=POOL_KIND,
        )
    except Exception as exc:  # noqa: BLE001 - the volumes are in hand; only the save failed
        log.warning("secondary_keywords_pool_error", stage="save", error=type(exc).__name__)


async def _measure(
    candidates: Sequence[str],
    *,
    location_code: int,
    language_code: str,
    country_location_code: int,
) -> tuple[dict[str, dict | None], list[str]]:
    """({candidate: provider row or None}, errors). Saved lookups first, one call for the rest."""
    use_pool = _pool_usable(location_code, country_location_code, language_code)
    cached: dict[str, dict] = {}
    if use_pool:
        cached, use_pool = await _read_cached(candidates, location_code=location_code, language_code=language_code)
    missing = [candidate for candidate in candidates if candidate not in cached]

    errors: list[str] = []
    found: dict[str, dict] = {}
    if missing:
        try:
            rows, errors = await dataforseo.search_volume(
                missing, location_code=location_code, language_code=language_code
            )
            errors = [str(error) for error in errors or []]
        except Exception as exc:  # noqa: BLE001 - a failed lookup leaves the rows as they were
            log.warning("secondary_keywords_lookup_failed", error=type(exc).__name__)
            rows, errors = [], [f"secondary_lookup_failed:{type(exc).__name__}"]
        by_text = {_norm(row.get("keyword")): row for row in rows or [] if isinstance(row, dict)}
        found = {c: by_text[c] for c in missing if c in by_text}
        if errors:
            log.warning("secondary_keywords_lookup_errors", errors=errors[:3])

    measured: dict[str, dict | None] = {**cached, **found}
    for candidate in missing:
        measured.setdefault(candidate, None)

    if use_pool and missing:
        # With provider errors a keyword that did not come back may be a failure, not "no
        # volume", so only what came back is saved then.
        to_save = {c: found.get(c) for c in missing if c in found or not errors}
        await _save_lookups(to_save, location_code=location_code, language_code=language_code)
    return measured, errors


# ------------------------------------------------------------------- the filters


def _passes_filters(
    rows: list[dict[str, Any]], primary: str, context: Any | None
) -> list[dict[str, Any]]:
    """The rows that pass the relevance and buyer-fit filters, ready for a commercial page."""
    if not rows:
        return []
    if context is not None:
        from app.services.keyword_relevance import filter_relevant_keywords

        kept, _excluded, _audit = filter_relevant_keywords(rows, context, source_label="secondary_variant")
        # A search the filters reroute to content or hold for review must not sit on a page.
        return [row for row in kept if row.get("commercial_target_eligible") is not False]
    # No context to judge relevance against: the hard rejects and buyer fit still apply,
    # with the row's own primary as the service.
    from app.services.keyword_exclusions import buyer_fit_reject, hard_reject_term, reroute_term

    return [
        row
        for row in rows
        if not hard_reject_term(row["keyword"])
        and not buyer_fit_reject(row["keyword"], services=[primary])
        and not reroute_term(row["keyword"], service_terms=[primary])
    ]


# ------------------------------------------------------------------- the top-up


async def top_up_secondary_keywords(
    entries: list[dict],
    *,
    location_code: int,
    language_code: str,
    country_location_code: int,
    relevance_context: Any | None,
    places: Iterable[str] = (),
    target: int | None = None,
    cap: int | None = None,
) -> dict[str, Any]:
    """Fill ``secondary_keywords_sheet`` and ``secondary_keywords`` in place; return a report.

    Rows with fewer than ``target`` secondary keywords (default ``keyword_secondary_target``)
    get measured modifier variants of their primary keyword, up to ``SECONDARY_ROW_CAP`` in
    all. A question, checklist, guide, or comparison gets none. At most ``cap``
    candidates (default ``keyword_secondary_lookup_cap``) are looked up: for the rows with
    the fewest secondary keywords first, rows that only support another page (no dedicated
    URL) last. ``location_code`` is where the volume is measured; lookups are shared
    through the keyword pool only when it equals ``country_location_code``.

    The report: ``rows_topped_up`` (rows that gained a keyword), ``candidates`` (distinct
    keywords looked up), ``candidates_dropped_by_cap`` (distinct keywords left unlooked by
    the cap; a warning is logged when there are any), ``kept`` (keywords added),
    ``empty_rows`` (rows that still have no secondary keyword) and ``lookup_errors``.
    """
    settings = get_settings()
    target = int(settings.keyword_secondary_target if target is None else target)
    cap = int(settings.keyword_secondary_lookup_cap if cap is None else cap)
    rows = [entry for entry in entries if isinstance(entry, dict)]
    report: dict[str, Any] = {
        "rows_topped_up": 0,
        "candidates": 0,
        "candidates_dropped_by_cap": 0,
        "kept": 0,
        "empty_rows": 0,
        "lookup_errors": [],
    }

    # Another row's primary is that page's keyword; it is not a secondary of this one.
    primaries = {_norm(row.get("primary_keyword")) for row in rows} - {""}
    short = sorted(
        (
            (1 if row.get("dedicated_url") is False else 0, len(_current(row)), index, row)
            for index, row in enumerate(rows)
            if len(_current(row)) < target and _wants_variants(row)
        ),
        key=lambda item: item[:3],
    )
    wanted: dict[str, None] = {}
    left_out: set[str] = set()
    plan: list[tuple[dict, list[str]]] = []
    for _supporting, _count, _index, row in short:
        chosen: list[str] = []
        for candidate in modifier_candidates(
            str(row.get("primary_keyword") or ""), [*_current(row), *primaries], places=places
        ):
            if candidate not in wanted and len(wanted) >= cap:
                left_out.add(candidate)
                continue
            wanted[candidate] = None
            chosen.append(candidate)
        plan.append((row, chosen))
    report["candidates"] = len(wanted)
    report["candidates_dropped_by_cap"] = len(left_out - set(wanted))
    if report["candidates_dropped_by_cap"]:
        log.warning(
            "secondary_keywords_capped",
            dropped=report["candidates_dropped_by_cap"],
            looked_up=report["candidates"],
            cap=cap,
        )

    if wanted:
        measured, errors = await _measure(
            list(wanted),
            location_code=location_code,
            language_code=language_code,
            country_location_code=country_location_code,
        )
        report["lookup_errors"] = errors[:3]
        from app.services.keyword_seeding import volume_qualifies

        claimed: set[str] = set()
        for row, chosen in plan:
            primary = str(row.get("primary_keyword") or "")
            volumes = {
                candidate: (measured.get(candidate) or {}).get("volume")
                for candidate in chosen
                if candidate not in claimed
            }
            candidates = [
                {"keyword": candidate, "volume": volume, "seed": primary, "match_class": "phrase"}
                for candidate, volume in volumes.items()
                if volume_qualifies(volume, min_volume=_MIN_VOLUME)
            ]
            kept = _passes_filters(candidates, primary, relevance_context)
            # The busiest variants first; equal volumes keep the candidate order.
            kept.sort(key=lambda item: -float(item["volume"]))
            room = SECONDARY_ROW_CAP - len(_current(row))
            additions = [item["keyword"] for item in kept[: max(0, room)]]
            if not additions:
                continue
            for key in ("secondary_keywords_sheet", "secondary_keywords"):
                _append(row, key, additions)
            claimed.update(additions)
            report["rows_topped_up"] += 1
            report["kept"] += len(additions)

    report["empty_rows"] = sum(1 for row in rows if not _current(row))
    log.info("secondary_keywords_topup", **{k: v for k, v in report.items() if k != "lookup_errors"})
    return report


# --------------------------------------------------------------- Phase 6 wiring


def _texts(values: Any) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        text = " ".join(str((value.get("name") if isinstance(value, dict) else value) or "").split())
        if text and text not in out:
            out.append(text)
    return out


def _dicts(values: Any) -> list[dict]:
    return [value for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _stored_industry(
    demand: Mapping[str, Any], commercial: Mapping[str, Any], marketing: Mapping[str, Any]
) -> str | None:
    seeding = demand.get("keyword_seeding") if isinstance(demand.get("keyword_seeding"), dict) else {}
    layer = seeding.get("industry_keywords") if isinstance(seeding.get("industry_keywords"), dict) else {}
    context = demand.get("topic_selection_context") if isinstance(demand.get("topic_selection_context"), dict) else {}
    intake = marketing.get("client_intake") if isinstance(marketing.get("client_intake"), dict) else {}
    for value in (
        layer.get("industry"),
        context.get("industry"),
        marketing.get("industry"),
        intake.get("industry"),
        commercial.get("industry"),
    ):
        text = " ".join(str(value or "").split())
        if text:
            return text
    return None


def relevance_context_from_stored(
    *,
    demand: Mapping[str, Any],
    commercial: Mapping[str, Any],
    marketing: Mapping[str, Any],
    website: Mapping[str, Any] | None,
    client_name: str,
    domain: str | None,
):
    """The Phase 5 relevance context, rebuilt from what Phase 5 and the profile stored.

    Same inputs and same branches as Phase 5's ``_build_ctx``: the services, CDD keywords,
    the website's page terms and themes (limited to the selected services once the
    selection is confirmed), the competitors, the brand, the industry, the geography, and
    the synonyms and sub-service topics that became seeds. The per-service seed lists are
    not stored; the services stand in as the seeds, which only matter when there is no
    business evidence at all.
    """
    from app.services.keyword_relevance import (
        build_relevance_context,
        page_terms_from_website,
        theme_terms_from_website,
    )
    from app.services.service_prioritization import is_prioritization_confirmed, term_matches_selected_services

    services = _texts(demand.get("services") or demand.get("products"))
    cdd_keywords = _texts(demand.get("cdd_keywords"))
    seeding = demand.get("keyword_seeding") if isinstance(demand.get("keyword_seeding"), dict) else {}
    synonyms = [t for entry in _dicts(seeding.get("seed_synonyms")) for t in _texts(entry.get("added"))]
    subtopics = [t for entry in _dicts(seeding.get("subservice_expansion")) for t in _texts(entry.get("added"))]
    for entry in _dicts(seeding.get("sub_service_seeding")):
        subtopics.extend(_texts([seed.get("seed") if isinstance(seed, dict) else seed for seed in entry.get("seeds") or []]))
    prioritization = dict(demand.get("service_prioritization") or commercial.get("service_prioritization") or {})

    common: dict[str, Any] = {
        "services": services,
        "cdd_keywords": cdd_keywords,
        "seeds": services,
        "competitor_names": _texts(demand.get("competitor_names")),
        "competitor_domains": _texts(demand.get("competitor_domains")),
        "brand_name": client_name,
        "domain": domain or None,
        "industry": _stored_industry(demand, commercial, marketing),
        "geography": str(demand.get("geographic_focus") or commercial.get("geographic_focus") or "") or None,
        "service_synonyms": synonyms,
        "service_subtopics": subtopics,
    }
    if is_prioritization_confirmed(prioritization) and services:
        return build_relevance_context(
            **common,
            page_terms=[t for t in page_terms_from_website(dict(website or {})) if term_matches_selected_services(t, services)],
            themes=[t for t in theme_terms_from_website(dict(website or {})) if term_matches_selected_services(t, services)],
        )
    return build_relevance_context(**common, website=dict(website or {}))


async def top_up_url_map_secondaries(
    url_map_report: Any,
    *,
    demand: Mapping[str, Any],
    commercial: Mapping[str, Any],
    marketing: Mapping[str, Any],
    website: Mapping[str, Any] | None,
    client_name: str,
    domain: str | None,
) -> dict[str, Any] | None:
    """Top up the rows of a Phase 6 URL map; ``None`` when ``url_map_report`` is not a map.

    The top-up is an addition to the map: whatever goes wrong here is logged and the rows
    keep the secondary keywords they had.
    """
    if not isinstance(url_map_report, dict):
        return None
    entries = [row for row in url_map_report.get("final_url_map") or [] if isinstance(row, dict)]
    if not entries:
        return {
            "rows_topped_up": 0,
            "candidates": 0,
            "candidates_dropped_by_cap": 0,
            "kept": 0,
            "empty_rows": 0,
            "lookup_errors": [],
        }
    try:
        from app.services.competitive_context import resolve_geographic_focus

        focus = demand.get("geographic_focus") or commercial.get("geographic_focus")
        intake = marketing.get("client_intake") if isinstance(marketing.get("client_intake"), dict) else {}
        geo = resolve_geographic_focus(
            {**commercial, "geographic_focus": focus} if focus else dict(commercial), intake, dict(marketing)
        )
        # Phase 5 measured every volume in the map at the country-level Labs location.
        country = int(demand.get("labs_location_code") or geo.get("labs_location_code") or _DEFAULT_LOCATION)
        context = relevance_context_from_stored(
            demand=demand,
            commercial=commercial,
            marketing=marketing,
            website=website,
            client_name=client_name,
            domain=domain,
        )
        return await top_up_secondary_keywords(
            entries,
            location_code=country,
            country_location_code=country,
            language_code=str(geo.get("language_code") or _DEFAULT_LANGUAGE),
            relevance_context=context,
            places=client_places(geo),
        )
    except Exception as exc:  # noqa: BLE001 - the rows keep what the cluster gave them
        log.warning("secondary_keywords_failed", error=f"{type(exc).__name__}: {str(exc)[:160]}")
        return {
            "rows_topped_up": 0,
            "candidates": 0,
            "candidates_dropped_by_cap": 0,
            "kept": 0,
            "empty_rows": sum(1 for row in entries if not _current(row)),
            "lookup_errors": [],
            "error": type(exc).__name__,
        }
