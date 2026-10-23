"""Primary services of the client's industry, with their sub-services.

The selection card used to pile together every service found on the client's site,
every competitor-derived candidate and every page candidate, which buried the few
services that actually matter. This asks an LLM for the industry's *primary* services
(a handful of broad service lines, such as SEO, PPC Management, Social Media
Marketing) and the specific sub-services beneath each (Local SEO, Google Ads
Management, Meta Ads, Facebook Ads, Instagram Ads, ...).

The model is also given everything we found for the client and says, for each primary
service and sub-service, which of those offerings it covers (copied exactly from the
list). That grounds the tick marks: a service or sub-service is pre-selected only when
it covers something the client demonstrably has. Everything else is an unticked
suggestion. Offerings the model could not place are reported, not dropped silently.

The call is cached for 30 days and time-boxed, so a slow or missing model never blocks
the card; without a usable reply the card falls back to the old site-derived list.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable
from typing import Any

from app.integrations.llm import synthesize_json
from app.logging_config import get_logger
from app.services.cache import cache_get, cache_set
from app.services.service_prioritization import (
    _is_document_stamp,
    _norm_key,
    service_identity_key,
)

log = get_logger("industry_services")

TAXONOMY_VERSION = "industry_services_v7"
CACHE_TTL_SECONDS = 30 * 24 * 3600
MAX_SERVICES = 12
MAX_SUBSERVICES = 10
MAX_OFFERINGS = 30
# The model reasons before it answers, and that reasoning counts against this budget.
# A small budget with many offerings left nothing for the answer: an empty reply.
LLM_MAX_TOKENS = 12000
MAX_NAME_CHARS = 60
DEFAULT_TIMEOUT_SECONDS = 120.0

SYSTEM_PROMPT = (
    "You are an industry analyst on a digital agency's research team. Given a "
    "business's industry and the offerings found for it, build its service taxonomy: "
    "the PRIMARY services and, under each, the specific sub-services.\n"
    "Definitions:\n"
    "- Service: a broad line of work customers hire this kind of business for, which "
    "owns several specific offerings (Roofing, SEO, Home Loan Broking).\n"
    "- Sub-service: one specific offering a customer can buy, belonging to exactly "
    "one service (Roof Repair under Roofing, Local SEO under SEO).\n"
    "- NOT services or sub-services: search phrasings (Roof Repairs, Best Roof "
    "Repair, Roof Repair Services, Roof Repair Near Me are all just Roof Repair); "
    "locations; customer types (First Home Buyers, Small Business, Dentists); "
    "languages (Hindi-speaking brokers); prices, quality or urgency claims "
    "(affordable, best, 24/7); blog or article titles.\n"
    "Work in this order:\n"
    "1. Decide what kind of business this really is from the label and the offerings "
    "together. The label can be vague or wrong (for example \"Content Team\").\n"
    "2. List the primary services a customer hires this kind of business for. Include "
    "the ones this client already sells and the other primary services of the same "
    "industry. The offerings found for the client are what they already sell, not "
    "the whole menu. Aim for that menu, usually 6 to 10 services. Specific channels, "
    "tools and deliverables are sub-services, not primary services.\n"
    "3. Under each, list the distinct sub-services a customer can buy in that line, "
    "including the ones this client does not sell yet. A line that only repeats the "
    "one or two pages already on the site is incomplete. Usually 5 to 8 per service, "
    "each concrete, purchasable and specific to THAT service. Leave out generic "
    "activities that apply to every service (reporting, analytics, strategy, consulting).\n"
    "4. Check every parent: a sub-service must be a kind of its parent service. Roof "
    "Repair belongs under Roofing, never Plumbing; Meta Ads under Social Media "
    "Marketing, not PPC Management; Content Marketing is not part of AI Consulting. "
    "Move anything that fails to the service it belongs to.\n"
    "5. Normalise names: the shortest plain name of the concept, at most 4 words, "
    "title case, singular for activities (Roof Repair, not Roof Repairs), no "
    "\"Services\"/\"Company\"/\"Agency\" suffix, no brands, no locations, no "
    "marketing words. Keep words that change what is sold (Metal Roofing, Emergency "
    "Plumbing, WordPress Development).\n"
    "6. Remove duplicates: one name per concept, each sub-service under one parent "
    "only. Keep genuinely different offerings apart (Roof Repair, Roof Replacement, "
    "Roof Installation and Roof Inspection are four sub-services).\n"
    "Example for a digital marketing agency: SEO (Local SEO, Technical SEO, On-Page "
    "SEO, Link Building, Ecommerce SEO, AI SEO), PPC Management (Google Ads "
    "Management, Microsoft Ads Management, Shopping Ads, Display Ads, YouTube Ads, "
    "Local Services Ads), Social Media Marketing (Meta Ads, LinkedIn Ads, TikTok "
    "Ads, Organic Social), Content Marketing (Blog Writing, Copywriting, Video "
    "Content), Email Marketing (Email Automation, Newsletter Marketing, Drip "
    "Campaigns), Conversion Rate Optimization (Landing Page Optimization, Website "
    "Optimization, Form Optimization), Web Design (Website Design, Website "
    "Development, Landing Pages). Apply the same fullness to any industry.\n"
    "Client offerings: place EVERY one. A broad offering becomes (or maps to) a "
    "primary service, a specific one becomes a sub-service. An offering that is a "
    "customer type or language (First Home Buyer, Hindi Brokers) goes in the `covers` "
    "of the service it qualifies, not in a sub-service of its own.\n"
    "For each primary service and each sub-service, `covers` lists the client "
    "offerings it represents, copied EXACTLY from the given list. Use [] when the "
    "client does not offer it. Never invent a client offering.\n"
    'Return JSON: {"services": [{"name": "...", "covers": ["..."], '
    '"subservices": [{"name": "...", "covers": ["..."]}]}]}.'
)


def _clean_name(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    name = " ".join(raw.split()).strip(" .-–—,;:")
    if len(name) < 2 or len(name) > MAX_NAME_CHARS or _is_document_stamp(name):
        return None
    return name


def _clean_covers(raw: Any, offerings: dict[str, str] | None) -> list[str]:
    """Offerings a node covers. Only names we actually gave the model count."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        key = service_identity_key(item)
        name = offerings.get(key) if offerings is not None else " ".join(item.split())
        if name and name not in out:
            out.append(name)
    return out


def normalize_taxonomy(raw: Any, offerings: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """The model's reply as ``[{"name", "covers", "subservices": [{"name", "covers"}]}]``.

    Tolerates a bare list and plain-string sub-services, drops junk, merges services
    that are one offering under two spellings, and caps the size so one bad reply
    cannot flood the card. When ``offerings`` is given, a ``covers`` entry that is not
    one of them is dropped, so the model cannot claim a client offering it was not
    shown.
    """
    if isinstance(raw, dict):
        raw = raw.get("services") or raw.get("primary_services") or raw.get("major_services")
    if not isinstance(raw, list):
        return []
    known = {service_identity_key(n): n for n in offerings} if offerings is not None else None
    out: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _clean_name(item.get("name"))
        if not name:
            continue
        key = service_identity_key(name)
        entry = by_key.get(key)
        if entry is None:
            if len(out) >= MAX_SERVICES:
                continue
            entry = {"name": name, "covers": [], "subservices": []}
            by_key[key] = entry
            out.append(entry)
        for cover in _clean_covers(item.get("covers"), known):
            if cover not in entry["covers"]:
                entry["covers"].append(cover)
        subs = item.get("subservices")
        if not isinstance(subs, list):
            continue
        have = {_norm_key(s["name"]): s for s in entry["subservices"]}
        have.setdefault(_norm_key(entry["name"]), {})
        for sub in subs:
            if isinstance(sub, str):
                sub = {"name": sub}
            if not isinstance(sub, dict):
                continue
            clean = _clean_name(sub.get("name"))
            if not clean:
                continue
            existing = have.get(_norm_key(clean))
            covers = _clean_covers(sub.get("covers"), known)
            if existing is not None:
                for cover in covers:
                    if cover not in existing.get("covers", []):
                        existing.setdefault("covers", []).append(cover)
                continue
            if len(entry["subservices"]) >= MAX_SUBSERVICES:
                continue
            node = {"name": clean, "covers": covers}
            entry["subservices"].append(node)
            have[_norm_key(clean)] = node
    return out


def offering_names(catalog: list[dict[str, Any]], services: Iterable[Any] = ()) -> list[str]:
    """Everything we know the client offers: its services, their sub-services and the
    brief's service list. The model places each one in the hierarchy."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        name = _clean_name(raw)
        if name and service_identity_key(name) not in seen and len(out) < MAX_OFFERINGS:
            seen.add(service_identity_key(name))
            out.append(name)

    rows = [r for r in catalog if isinstance(r, dict) and not r.get("candidate_new_service")]
    # Top-level offerings first, then their sub-services, so that when the list is
    # capped what gets dropped is the least important.
    for raw in services:
        add(raw)
    for row in rows:
        add(row.get("name"))
    for row in rows:
        for sub in row.get("subservices") or []:
            if isinstance(sub, dict):
                add(sub.get("name"))
    return out


def build_catalog_from_taxonomy(
    taxonomy: list[dict[str, Any]],
    site_catalog: list[dict[str, Any]],
    offerings: Iterable[str],
    *,
    report: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """The service catalog as primary services and sub-services.

    Returns ``(catalog, unplaced)``. The model's taxonomy is first made canonical
    (``service_taxonomy.refine_taxonomy``): names normalised, duplicates merged,
    sub-services under the service they belong to, attributes turned into metadata.
    A primary service or sub-service that covers a client offering is marked as the
    client's (selected / offered by default) and keeps the URL it already has; the rest
    are unticked industry suggestions. ``unplaced`` are offerings the model did not
    place anywhere. When ``report`` is given it is filled with what the refinement
    changed.
    """
    from app.services.service_taxonomy import refine_taxonomy

    taxonomy, changes = refine_taxonomy(taxonomy)
    if report is not None:
        report.update(changes)
    offering_list = list(offerings)
    # What we know about each client offering: where it came from and its URL.
    site_info: dict[str, dict[str, Any]] = {}
    for row in site_catalog:
        if not isinstance(row, dict) or row.get("candidate_new_service"):
            continue
        info = {"source": str(row.get("source") or "website"), "path": row.get("path")}
        site_info.setdefault(service_identity_key(str(row.get("name") or "")), info)
        for sub in row.get("subservices") or []:
            if isinstance(sub, dict):
                site_info.setdefault(
                    service_identity_key(str(sub.get("name") or "")),
                    {"source": str(sub.get("source") or "website"), "path": sub.get("path")},
                )

    def info_for(covers: list[str]) -> dict[str, Any] | None:
        for name in covers:
            found = site_info.get(service_identity_key(name))
            if found:
                return found
        return {"source": "cdd", "path": None} if covers else None

    covered: set[str] = set()
    rows: list[dict[str, Any]] = []
    for svc in taxonomy:
        subs = svc.get("subservices") or []
        all_covers = list(svc.get("covers") or []) + [
            c for sub in subs for c in (sub.get("covers") or [])
        ]
        covered.update(service_identity_key(c) for c in all_covers)
        client_has = bool(all_covers)
        svc_info = info_for(list(svc.get("covers") or []) or all_covers)
        row: dict[str, Any] = {
            "id": _norm_key(svc["name"]),
            "name": svc["name"],
            "source": svc_info["source"] if client_has and svc_info else "industry",
            "selected": client_has,
            "priority_tier": None,
            "candidate_new_service": not client_has,
            "subservices": [],
            "taxonomy": dict(svc.get("taxonomy") or {}),
        }
        if client_has and svc_info and svc_info.get("path"):
            row["path"] = svc_info["path"]
        for sub in subs:
            sub_covers = list(sub.get("covers") or [])
            sub_info = info_for(sub_covers)
            node: dict[str, Any] = {
                "id": _norm_key(sub["name"]),
                "name": sub["name"],
                "source": sub_info["source"] if sub_info else "industry",
                "selected": bool(sub_covers),
                "taxonomy": dict(sub.get("taxonomy") or {}),
            }
            if sub_info and sub_info.get("path"):
                node["path"] = sub_info["path"]
            row["subservices"].append(node)
        rows.append(row)

    # The client's own services first, then the suggestions.
    rows.sort(key=lambda r: 0 if not r["candidate_new_service"] else 1)

    # An offering is placed when it, or the service it sits under, was covered.
    unplaced: list[str] = []
    parent_of: dict[str, str] = {}
    for row in site_catalog:
        if not isinstance(row, dict) or row.get("candidate_new_service"):
            continue
        for sub in row.get("subservices") or []:
            if isinstance(sub, dict):
                parent_of[service_identity_key(str(sub.get("name") or ""))] = service_identity_key(
                    str(row.get("name") or "")
                )
    for name in offering_list:
        key = service_identity_key(name)
        if key in covered or parent_of.get(key) in covered:
            continue
        unplaced.append(name)

    if offering_list and not covered:
        # The model ignored the offerings entirely. Keep the client's own services
        # rather than show a list with none of them in it.
        have = {service_identity_key(r["name"]) for r in rows}
        for row in site_catalog:
            if (
                isinstance(row, dict)
                and not row.get("candidate_new_service")
                and service_identity_key(str(row.get("name") or "")) not in have
            ):
                rows.insert(0, dict(row))
        unplaced = []
    return rows, unplaced


def _cache_key(industry: str, offerings: list[str], geography: str | None) -> str:
    blob = json.dumps(
        {
            "v": TAXONOMY_VERSION,
            "industry": _norm_key(industry),
            "offerings": sorted({service_identity_key(s) for s in offerings}),
            "geo": _norm_key(geography or ""),
        },
        sort_keys=True,
    )
    return f"industry_taxonomy:{hashlib.sha1(blob.encode('utf-8')).hexdigest()[:24]}"


async def generate_industry_taxonomy(
    industry: str | None,
    *,
    existing_services: Iterable[Any] = (),
    geography: str | None = None,
    use_cache: bool = True,
) -> list[dict[str, Any]]:
    """Ask the model for the industry's primary services and their sub-services.

    ``existing_services`` are the client's offerings; the model places each of them.
    Returns ``[]`` — never raises — when there is no industry, no model, or no usable
    reply. An unusable reply is not cached, so the next load tries again.
    """
    text = " ".join(str(industry or "").split())
    if not text:
        return []
    offerings: list[str] = []
    seen: set[str] = set()
    for raw in existing_services:
        name = _clean_name(raw)
        if name and service_identity_key(name) not in seen and len(offerings) < MAX_OFFERINGS:
            seen.add(service_identity_key(name))
            offerings.append(name)
    key = _cache_key(text, offerings, geography)
    if use_cache:
        cached = normalize_taxonomy(await cache_get(key), offerings)
        if cached:
            return cached

    lines = [f"Industry: {text}"]
    if geography:
        lines.append(f"Where the business operates: {geography}")
    if offerings:
        lines.append("Offerings found for this client:")
        lines.extend(f"- {name}" for name in offerings)
    else:
        lines.append("No offerings are known for this client yet, so every `covers` is [].")
    try:
        reply = await synthesize_json(SYSTEM_PROMPT, "\n".join(lines), max_tokens=LLM_MAX_TOKENS)
    except Exception as exc:  # noqa: BLE001 — a failed suggestion must not break the card
        log.warning("industry_taxonomy_failed", error=str(exc)[:200])
        return []
    taxonomy = normalize_taxonomy(reply, offerings)
    if not taxonomy:
        log.warning("industry_taxonomy_empty", industry=text[:80])
        return []
    if use_cache:
        await cache_set(key, taxonomy, CACHE_TTL_SECONDS)
    return taxonomy


async def taxonomy_with_status(
    industry: str | None,
    *,
    existing_services: Iterable[Any] = (),
    geography: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[dict[str, Any]], str]:
    """``(taxonomy, status)``. Never raises, and says why when the list is empty.

    ``status`` is ``ok``, ``no_industry``, ``timeout`` or ``failed``, so the card can
    tell the operator why it is showing the site-derived list instead of silently
    falling back.
    """
    if not " ".join(str(industry or "").split()):
        return [], "no_industry"
    try:
        taxonomy = await asyncio.wait_for(
            generate_industry_taxonomy(
                industry, existing_services=existing_services, geography=geography
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning("industry_taxonomy_timeout", industry=str(industry or "")[:80])
        return [], "timeout"
    except Exception as exc:  # noqa: BLE001
        log.warning("industry_taxonomy_error", error=str(exc)[:200])
        return [], "failed"
    return (taxonomy, "ok") if taxonomy else ([], "failed")


async def taxonomy_for_client(
    industry: str | None,
    *,
    existing_services: Iterable[Any] = (),
    geography: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """The taxonomy only; empty on any failure. See ``taxonomy_with_status``."""
    taxonomy, _status = await taxonomy_with_status(
        industry, existing_services=existing_services, geography=geography, timeout=timeout
    )
    return taxonomy


async def taxonomy_for_pack(
    *,
    industry: str | None,
    services: Iterable[Any],
    website: dict[str, Any] | None,
    page_seeds: list[dict[str, Any]] | None,
    geography: str | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """``(taxonomy, status)`` for a selection card, told everything the client offers."""
    from app.services.service_prioritization import build_client_service_catalog

    site = build_client_service_catalog(services=[], website=website, page_seeds=page_seeds)
    return await taxonomy_with_status(
        industry,
        existing_services=offering_names(site, services),
        geography=geography,
    )
