"""Service-scoped keyword seeding.

Old flow (``run_search_demand``): one global list of seed roots covering every
service, one ``run_multi_mode_seeding`` call over all of them, and only *afterwards*
``build_service_seed_clusters`` guessing which service each seed belonged to by token
overlap. A seed carried no service, the seed cap was shared (so later services could
be starved), and a stray keyword was attached to whichever seed in the whole pool
looked closest.

New flow, per service:

    Service A -> generate_service_seeds(A) -> fetch keywords from ONLY A's seeds
    Service B -> generate_service_seeds(B) -> fetch keywords from ONLY B's seeds

* every seed is created with its owning service (``seed_targets[seed]["service"]``);
* every fetch is a separate ``run_multi_mode_seeding`` call whose input is exactly one
  service's seeds, so keywords can only come from that service's seeds;
* every cluster and keyword row is stamped with the service that produced it;
* the seed count and the phase dollar cap are each split fairly instead of one shared pool;
* one service failing does not stop the others;
* the service root is followed by the service's synonyms ("search engine
  optimization" for "SEO"), found by ``seed_synonyms`` and tagged
  ``seed_origin="synonym"``. They are extra seeds on top of the service's budget;
* each sub-service is a seed of its own (2026-10-27), never an alternate name of the
  parent. The service's own seeds stay the service name and its synonyms; the
  sub-services sit beside them with ``target_type="sub_service"`` and the parent
  recorded, catalog sub-services (operator-approved) first, then the ones the model
  suggested. Every keyword a sub-service seed returns carries ``sub_service`` as well
  as ``service``;
* the client's industry gets a plan of its own (``industry_seed_plan``): category terms
  a home page targets ("digital marketing agency"), tagged ``seed_origin="industry"``,
  fetched on top of the seed budget. It is not a service: its plan has the service id
  ``industry`` and its run is marked ``kind: "industry"``.

A seed *text* that two services both legitimately own (for example a business keyword
that mentions two selected services) is kept for both. Each service is an independent
pipeline, so nothing is de-duplicated across services.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger
from app.services.api_meter import (
    release_phase_amount,
    remaining_phase_budget_usd,
    reserve_phase_amount,
    service_budget_shares,
    service_research_budget,
)
from app.services.keyword_opportunity import is_stale_year_keyword
from app.services.keyword_relevance import compact_cleaning_audit, merge_cleaning_audits
from app.services.keyword_service_contract import INDUSTRY_ORIGIN
from app.services.keyword_seeding import clean_provider_seed
from app.services.service_prioritization import (
    _norm_key,
    _selection_keys,
    service_identity_key,
    term_matches_selected_services,
)
from app.services.service_scope import excluded_hits, resolve_scope, sub_allowed

log = get_logger("service_seeding")

OTHER_SERVICE_LABEL = "Other website topics"
OTHER_SERVICE_ID = "_other"
MAX_SEED_WORDS = 6
# A service's other names ("search engine optimization" for "SEO") carry this origin.
SYNONYM_ORIGIN = "synonym"
# Sub-service topics the model suggested for a service ("seo audit" for "SEO").
EXPANSION_ORIGIN = "subservice_expansion"
# Sub-services from the service catalog the operator approved ("Local SEO" for "SEO").
CATALOG_SUB_ORIGIN = "subservice_catalog"
SUB_SERVICE_ORIGINS = frozenset({CATALOG_SUB_ORIGIN, EXPANSION_ORIGIN})
# Seeds fetched on top of a service's share of the seed budget. Sub-service seeds
# are here too: the budget is raised by them instead of making them compete with
# the service's own seeds (operator decision, 2026-10-27). So are the industry seeds
# (``INDUSTRY_ORIGIN``), a layer of their own on top of every service.
EXTRA_ORIGINS = frozenset({SYNONYM_ORIGIN, *SUB_SERVICE_ORIGINS, INDUSTRY_ORIGIN})
# The industry layer's plan id. Its run carries ``kind: INDUSTRY_ORIGIN``.
INDUSTRY_SERVICE_ID = "industry"
# Share of the remaining phase dollar cap held for the industry plan before the rest is
# split equally among the services. Its 2 to 8 seeds are a small layer; an equal share
# would cut every service's dollars by 1/(n+1).
INDUSTRY_BUDGET_SHARE = 0.10
MAX_EXPANSION_SEEDS = 10

_TYPE_RANK = {"service": 0, "sub_service": 1, "keyword": 2, "page": 3}
# Between the service root and its sub-services.
_SYNONYM_RANK = 0.5
# Catalog sub-services come right after the root and its synonyms, in catalog order.
_CATALOG_SUB_RANK = 1.0
# After every seed the service already has (catalog sub-services are ranked last of
# those, as "seed"). Extra seeds are always fetched, so this order never decides the
# budget; it keeps the model's suggestions behind the service's own material.
_EXPANSION_RANK = 5.0


class ServiceIsolationError(RuntimeError):
    """A keyword fetch returned something that did not come from its own service's seeds."""


@dataclass
class ServiceSeedPlan:
    """One service's seeds, generated from that service alone."""

    service: str
    service_id: str
    seeds: list[str] = field(default_factory=list)
    seed_targets: dict[str, dict[str, Any]] = field(default_factory=dict)
    status: str = "planned"  # planned | no_seeds | seed_generation_failed
    error: str | None = None
    # Synonym seeds that made it in, and the ones skipped with a reason.
    synonyms_added: list[str] = field(default_factory=list)
    synonyms_skipped: list[dict[str, str]] = field(default_factory=list)
    # Model-suggested sub-service seeds that made it in, and the ones skipped.
    expansions_added: list[str] = field(default_factory=list)
    expansions_skipped: list[dict[str, str]] = field(default_factory=list)
    # Catalog sub-service seeds that made it in, and the ones skipped.
    catalog_subs_added: list[str] = field(default_factory=list)
    catalog_subs_skipped: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_other(self) -> bool:
        return self.service_id == OTHER_SERVICE_ID

    @property
    def is_industry(self) -> bool:
        """The industry layer's plan, not a client service."""
        return self.service_id == INDUSTRY_SERVICE_ID

    def is_extra(self, seed: str) -> bool:
        """Fetched on top of the service's share of the seed budget."""
        return (self.seed_targets.get(seed.lower()) or {}).get("seed_origin") in EXTRA_ORIGINS

    @property
    def synonym_seed_count(self) -> int:
        return sum(
            1
            for seed in self.seeds
            if (self.seed_targets.get(seed.lower()) or {}).get("seed_origin") == SYNONYM_ORIGIN
        )

    @property
    def extra_seed_count(self) -> int:
        return sum(1 for seed in self.seeds if self.is_extra(seed))

    def is_sub_service(self, seed: str) -> bool:
        return (self.seed_targets.get(seed.lower()) or {}).get("seed_origin") in SUB_SERVICE_ORIGINS

    @property
    def sub_service_seeds(self) -> list[str]:
        return [seed for seed in self.seeds if self.is_sub_service(seed)]


# --------------------------------------------------------------------- seed text


def normalize_seed_root(
    term: Any, *, is_brand: Callable[[str], bool] | None = None
) -> str | None:
    """The cleaned seed text for ``term``, or None when it is not usable as a seed.

    Mirrors the rules the old global ``_add_root`` applied, so a seed is accepted or
    rejected exactly as before.
    """
    raw = str(term or "").strip()
    cleaned = clean_provider_seed(raw) or raw
    key = cleaned.lower()
    if not cleaned or len(key) < 2 or is_stale_year_keyword(key):
        return None
    if len(cleaned.split()) > MAX_SEED_WORDS:
        return None
    # Seeds describe the client, never a competitor's brand.
    if is_brand is not None and is_brand(cleaned):
        return None
    return cleaned


# --------------------------------------------------------------------- ownership


def _catalog_row(catalog: list[dict[str, Any]], service: str) -> dict[str, Any] | None:
    keys = _selection_keys(service)
    for row in catalog or []:
        if isinstance(row, dict) and keys & _selection_keys(
            str(row.get("id") or ""), str(row.get("name") or "")
        ):
            return row
    # A service selected under a spelling that was since made canonical.
    for row in catalog or []:
        if isinstance(row, dict):
            aliases = (row.get("taxonomy") or {}).get("aliases") or []
            if keys & _selection_keys(*[str(a) for a in aliases]):
                return row
    return None


def _catalog_row_for(
    catalog: list[dict[str, Any]], service: str, prioritization: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The catalog row for ``service``; when no row carries its name (a service
    selected as "Website Development" whose row is "Web Development"), the row whose
    sub-services the operator scoped for this service."""
    row = _catalog_row(catalog, service)
    if row is not None:
        return row
    found = resolve_scope(prioritization, service)
    if not found or not _selection_keys(service) & _selection_keys(found[0], found[1]["name"]):
        return None
    scoped: set[str] = set()
    for item in (*found[1]["offered"], *found[1]["excluded"]):
        name = item["name"] if isinstance(item, dict) else str(item)
        scoped |= _selection_keys(name)
    best, best_hits = None, 0
    for candidate in catalog or []:
        if not isinstance(candidate, dict):
            continue
        hits = sum(1 for n in _sub_names(candidate) if _selection_keys(n) & scoped)
        if hits > best_hits:
            best, best_hits = candidate, hits
    return best


def _sub_names(row: dict[str, Any] | None) -> list[str]:
    return [
        str(s.get("name") or s.get("id") or "")
        for s in (row or {}).get("subservices") or []
        if isinstance(s, dict) and str(s.get("name") or s.get("id") or "").strip()
    ]


def subservice_context(
    service: str, catalog: list[dict[str, Any]], prioritization: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """``(existing, excluded)`` sub-service names for one service, for the expansion
    question: what the catalog already lists for it, and what the operator excluded."""
    existing = _sub_names(_catalog_row_for(catalog, service, prioritization))
    found = resolve_scope(prioritization, service)
    excluded = [str(e.get("name") or "") for e in (found[1]["excluded"] if found else [])]
    return existing, [e for e in excluded if e]


def _keyword_qualifiers(row: dict[str, Any] | None, prioritization: dict[str, Any]) -> list[str]:
    """Offerings the taxonomy keeps as attributes of this service, not as services.

    "First Home Buyer" or "Hindi Brokers" is who or how the service is sold, so it is
    no longer a sub-service; it is still a search the client wants to rank for. Taken
    from the service row and from its offered sub-services.
    """
    if not row:
        return []
    out: list[str] = []
    found = list((row.get("taxonomy") or {}).get("keyword_qualifiers") or [])
    for sub in row.get("subservices") or []:
        if not isinstance(sub, dict):
            continue
        if not sub_allowed(prioritization, row.get("id"), sub.get("id"), str(sub.get("name") or "")):
            continue
        found.extend((sub.get("taxonomy") or {}).get("keyword_qualifiers") or [])
    for term in found:
        if isinstance(term, str) and term.strip() and term not in out:
            out.append(term)
    return out


_PLACE_WORDS = frozenset(
    {
        "near me", "australia", "australian", "melbourne", "sydney", "brisbane", "perth",
        "adelaide", "canberra", "hobart", "darwin", "gold coast", "geelong", "newcastle",
        "wollongong", "sunshine coast", "nsw", "vic", "qld", "wa", "sa", "tas", "act", "nt",
        "victoria", "queensland", "local area",
    }
)


def _is_place(text: str) -> bool:
    """True when the words are only a place (a city, state, country or "near me")."""
    rest = f" {_norm(text)} "
    for place in sorted(_PLACE_WORDS, key=len, reverse=True):
        rest = rest.replace(f" {place} ", " ")
    return bool(_norm(text)) and not rest.strip()


def seed_belongs_to(
    service: str,
    service_id: str,
    root: str,
    meta: dict[str, Any],
    *,
    sub_names: Iterable[str] = (),
    other_service_keys: set[str] | None = None,
) -> bool:
    """Does the candidate seed ``root`` belong to ``service``?

    * a service root belongs to the service of that exact name, and to no other;
    * a sub-service belongs to its parent service;
    * a page / keyword seed belongs to every service whose distinctive terms it
      contains (never to a service just because another service shares a word),
      unless it is exactly another service's own name.
    """
    ttype = str(meta.get("target_type") or "keyword")
    target = str(meta.get("target") or root)
    own_keys = _selection_keys(service, service_id)
    if ttype == "service":
        return bool(_selection_keys(target, root) & own_keys)
    if ttype == "sub_service":
        parent = str(meta.get("parent_segment") or "")
        if parent:
            return bool(_selection_keys(parent) & own_keys)
        return any(_selection_keys(target) & _selection_keys(s) for s in sub_names)
    if other_service_keys and service_identity_key(target) in other_service_keys:
        return False
    return term_matches_selected_services(target, [service])


# ------------------------------------------------------------------- generation


def generate_service_seeds(
    service: str,
    *,
    pool_roots: list[str],
    pool_targets: dict[str, dict[str, Any]],
    catalog: list[dict[str, Any]],
    prioritization: dict[str, Any],
    other_service_names: Iterable[str] = (),
    is_brand: Callable[[str], bool] | None = None,
    synonyms: Iterable[str] = (),
    subtopics: Iterable[str] = (),
    max_expansions: int = MAX_EXPANSION_SEEDS,
    claimed_sub_services: dict[str, str] | None = None,
) -> ServiceSeedPlan:
    """Seeds for ONE service, built from that service's own data.

    In order: the service name, the synonyms people use for that same service, the
    service's catalog sub-services the operator offers, then sub-service topics the
    model suggested (at most ``max_expansions``). Sub-services are seeds of their own
    with the parent recorded, never alternate names of the parent. Page titles and
    loose business keywords are not seeds. Nothing belonging to another service is
    added, and every seed records its service.

    ``claimed_sub_services`` maps a sub-service key to the service that already
    seeded it; ``plan_service_seeds`` shares one across services so a sub-service is
    seeded under one parent only.
    """
    # The service's id comes from a row that carries its name, as before; a row found
    # only through the scope supplies sub-services, never a different id.
    service_id = _norm_key(str((_catalog_row(catalog, service) or {}).get("id") or service))
    row = _catalog_row_for(catalog, service, prioritization)
    sub_names = [
        str(s.get("name") or s.get("id") or "")
        for s in (row or {}).get("subservices") or []
        if isinstance(s, dict)
    ]
    other_keys = {service_identity_key(n) for n in other_service_names} - {
        service_identity_key(service)
    }

    seeds: list[str] = []
    targets: dict[str, dict[str, Any]] = {}

    def add(root: str, meta: dict[str, Any]) -> None:
        key = root.lower()
        if not root or key in targets:
            return
        targets[key] = {**meta, "service": service, "service_id": service_id}
        seeds.append(root)

    # 1. the service itself. Prefer the pool's entry so cleaning is unchanged.
    own_root = next(
        (
            r
            for r in pool_roots
            if (pool_targets.get(r.lower()) or {}).get("target_type") == "service"
            and seed_belongs_to(service, service_id, r, pool_targets[r.lower()])
        ),
        None,
    )
    root_seeded = False
    if own_root is not None:
        add(own_root, pool_targets[own_root.lower()])
        root_seeded = True
    else:
        cleaned = normalize_seed_root(service, is_brand=is_brand)
        if cleaned:
            add(cleaned, {"target": service, "target_type": "service"})
            root_seeded = True

    # Page titles, sub-services, and business keywords stay out of the seed list.
    # The service's synonyms. Only once the service root is a seed, and only after
    #    everything above, so a seed the service already has always wins over a
    #    synonym. Each one is cleaned and validated exactly like the root; the sort
    #    below puts them right after it, in the model's order.
    added: list[str] = []
    skipped: list[dict[str, str]] = []
    sub_keys: set[str] = set()
    for name in sub_names:
        sub_keys |= _selection_keys(name)
    for phrase in synonyms:
        text = str(phrase or "").strip()
        if not text:
            continue
        if not root_seeded:
            skipped.append({"phrase": text, "reason": "service_not_seeded"})
            continue
        cleaned = normalize_seed_root(text, is_brand=is_brand)
        if not cleaned:
            skipped.append({"phrase": text, "reason": "invalid_seed"})
        elif cleaned.lower() in targets:
            skipped.append({"phrase": text, "reason": "duplicate"})
        elif _selection_keys(cleaned) & sub_keys:
            skipped.append({"phrase": text, "reason": "sub_service"})
        elif service_identity_key(cleaned) in other_keys:
            skipped.append({"phrase": text, "reason": "another_service"})
        else:
            add(
                cleaned,
                {
                    "target": service,
                    "target_type": "service",
                    "seed_origin": SYNONYM_ORIGIN,
                    "synonym_of": service,
                },
            )
            added.append(cleaned)

    # Sub-services: their own seeds, beside the service's. Catalog first (the
    # operator approved them), then the model's suggestions for discovery.
    claimed = claimed_sub_services if claimed_sub_services is not None else {}
    found_scope = resolve_scope(prioritization, service_id) or resolve_scope(prioritization, service)
    scope_entry = found_scope[1] if found_scope else None
    # The scope entry this service was decided under; the catalog row's id otherwise.
    scope_ref = found_scope[0] if found_scope else ((row or {}).get("id") or service_id)
    excluded_keys: set[str] = set()
    for item in (scope_entry or {}).get("excluded") or []:
        name = item["name"] if isinstance(item, dict) else str(item)
        excluded_keys |= _selection_keys(name)
    qualifier_keys: set[str] = set()
    for term in _keyword_qualifiers(row, prioritization):
        qualifier_keys |= _selection_keys(term)
    parent_keys = {k for seed in seeds for k in _selection_keys(seed)}
    parent_words = {w for seed in seeds for w in _norm(seed).split()}
    # Sub-services the catalog files under a different service.
    other_sub_keys: set[str] = set()
    for other_row in catalog or []:
        if not isinstance(other_row, dict) or other_row is row:
            continue
        for sub in other_row.get("subservices") or []:
            if isinstance(sub, dict):
                other_sub_keys |= _selection_keys(str(sub.get("name") or ""), str(sub.get("id") or ""))

    def _sub_reject(cleaned: str) -> str | None:
        """Why this sub-service text cannot be seeded under this service, or None."""
        keys = _selection_keys(cleaned)
        if cleaned.lower() in targets or keys & parent_keys:
            return "duplicate_of_service_seed"
        if service_identity_key(cleaned) in other_keys:
            return "another_service"
        if keys & other_sub_keys:
            return "another_service_sub_service"
        if keys & qualifier_keys:
            return "keyword_qualifier"
        # "seo melbourne" is the service in a place: a qualifier, not a sub-service.
        extra = [w for w in _norm(cleaned).split() if w not in parent_words]
        if extra and _is_place(" ".join(extra)):
            return "location_qualifier"
        owner = next((claimed[k] for k in keys if k in claimed), None)
        if owner is not None and owner != service:
            return f"seeded_under:{owner}"
        return None

    def _add_sub(cleaned: str, sub_name: str, origin: str) -> None:
        add(
            cleaned,
            {
                "target": sub_name,
                "target_type": "sub_service",
                # seed_belongs_to resolves a sub-service seed through its parent.
                "parent_segment": service,
                "parent_service": service,
                "sub_service": sub_name,
                "seed_origin": origin,
            },
        )
        for k in _selection_keys(cleaned):
            claimed.setdefault(k, service)

    catalog_added: list[str] = []
    catalog_skipped: list[dict[str, str]] = []
    catalog_keys: set[str] = set()
    for sub in (row or {}).get("subservices") or []:
        if not isinstance(sub, dict):
            continue
        name = str(sub.get("name") or sub.get("id") or "").strip()
        if not name:
            continue
        catalog_keys |= _selection_keys(name, str(sub.get("id") or ""))
        if not root_seeded:
            catalog_skipped.append({"phrase": name, "reason": "service_not_seeded"})
            continue
        if not sub_allowed(prioritization, scope_ref, sub.get("id"), name):
            reason = "excluded_by_operator" if _selection_keys(name) & excluded_keys else "not_in_scope"
            catalog_skipped.append({"phrase": name, "reason": reason})
            continue
        cleaned = normalize_seed_root(name, is_brand=is_brand)
        if not cleaned:
            catalog_skipped.append({"phrase": name, "reason": "invalid_seed"})
            continue
        why = _sub_reject(cleaned)
        if why:
            catalog_skipped.append({"phrase": name, "reason": why})
            continue
        _add_sub(cleaned, name, CATALOG_SUB_ORIGIN)
        catalog_added.append(cleaned)

    expanded: list[str] = []
    expansion_skipped: list[dict[str, str]] = []
    limit = max(0, int(max_expansions))
    for phrase in subtopics:
        text = str(phrase or "").strip()
        if not text:
            continue
        if not root_seeded:
            expansion_skipped.append({"phrase": text, "reason": "service_not_seeded"})
            continue
        if _selection_keys(text) & catalog_keys:
            # The catalog's version is the one seeded (or deliberately not seeded).
            expansion_skipped.append({"phrase": text, "reason": "duplicate_of_catalog"})
            continue
        if scope_entry is not None and (excluded_hits(text, scope_entry) or _selection_keys(text) & excluded_keys):
            expansion_skipped.append({"phrase": text, "reason": "excluded_by_operator"})
            continue
        cleaned = normalize_seed_root(text, is_brand=is_brand)
        if not cleaned:
            expansion_skipped.append({"phrase": text, "reason": "invalid_seed"})
            continue
        why = _sub_reject(cleaned)
        if why:
            expansion_skipped.append({"phrase": text, "reason": why})
            continue
        if len(expanded) >= limit:
            expansion_skipped.append({"phrase": text, "reason": "over_limit"})
            continue
        _add_sub(cleaned, text, EXPANSION_ORIGIN)
        expanded.append(cleaned)

    def _order(seed: str) -> tuple[float, int, str]:
        meta = targets[seed.lower()]
        if meta.get("seed_origin") == SYNONYM_ORIGIN:
            return (_SYNONYM_RANK, added.index(seed), seed.lower())
        if meta.get("seed_origin") == CATALOG_SUB_ORIGIN:
            return (_CATALOG_SUB_RANK, catalog_added.index(seed), seed.lower())
        if meta.get("seed_origin") == EXPANSION_ORIGIN:
            return (_EXPANSION_RANK, expanded.index(seed), seed.lower())
        return (
            _TYPE_RANK.get(str(meta.get("target_type") or "keyword"), 4),
            -len(seed.split()),
            seed.lower(),
        )

    seeds.sort(key=_order)
    return ServiceSeedPlan(
        service=service,
        service_id=service_id,
        seeds=seeds,
        seed_targets=targets,
        status="planned" if seeds else "no_seeds",
        synonyms_added=added,
        synonyms_skipped=skipped,
        expansions_added=expanded,
        expansions_skipped=expansion_skipped,
        catalog_subs_added=catalog_added,
        catalog_subs_skipped=catalog_skipped,
    )


def plan_service_seeds(
    services: list[str],
    *,
    pool_roots: list[str],
    pool_targets: dict[str, dict[str, Any]],
    catalog: list[dict[str, Any]],
    prioritization: dict[str, Any],
    top_service: str | None = None,
    is_brand: Callable[[str], bool] | None = None,
    service_synonyms: Mapping[str, Iterable[str]] | None = None,
    service_subtopics: Mapping[str, Iterable[str]] | None = None,
    max_expansions: int = MAX_EXPANSION_SEEDS,
) -> list[ServiceSeedPlan]:
    """One plan per service. A service whose generation fails gets an empty, flagged
    plan; the others are unaffected.

    The priority service goes first, so it also gets first call on any leftover budget.
    Page titles, business keywords, and terms that are not a selected service are
    not seeded.
    ``service_synonyms`` maps a service name to the synonyms ``seed_synonyms`` found
    for it, and ``service_subtopics`` to the sub-service topics ``subservice_expansion``
    found; a service missing from either is seeded without them.
    """
    synonyms_by_name = {
        _norm_key(str(name)): list(phrases or [])
        for name, phrases in (service_synonyms or {}).items()
    }
    subtopics_by_name = {
        _norm_key(str(name)): list(phrases or [])
        for name, phrases in (service_subtopics or {}).items()
    }
    names: list[str] = []
    seen: set[str] = set()
    for raw in services:
        name = str(raw or "").strip()
        if name and _norm_key(name) not in seen:
            seen.add(_norm_key(name))
            names.append(name)
    if top_service:
        wanted = _selection_keys(top_service)
        names.sort(key=lambda n: 0 if wanted & _selection_keys(n) else 1)

    plans: list[ServiceSeedPlan] = []
    # One sub-service is seeded under one parent: the first service (priority order)
    # to seed it owns it, and a later service records the skip.
    claimed_sub_services: dict[str, str] = {}
    for name in names:
        try:
            plan = generate_service_seeds(
                name,
                pool_roots=pool_roots,
                pool_targets=pool_targets,
                catalog=catalog,
                prioritization=prioritization,
                other_service_names=names,
                is_brand=is_brand,
                synonyms=synonyms_by_name.get(_norm_key(name), ()),
                subtopics=subtopics_by_name.get(_norm_key(name), ()),
                max_expansions=max_expansions,
                claimed_sub_services=claimed_sub_services,
            )
            if plan.catalog_subs_added or plan.catalog_subs_skipped:
                log.info(
                    "service_catalog_subservice_seeds",
                    service=name,
                    added=plan.catalog_subs_added,
                    skipped=plan.catalog_subs_skipped,
                )
            if plan.synonyms_added or plan.synonyms_skipped:
                log.info(
                    "service_synonym_seeds",
                    service=name,
                    added=plan.synonyms_added,
                    skipped=plan.synonyms_skipped,
                )
            if plan.expansions_added or plan.expansions_skipped:
                log.info(
                    "service_subservice_seeds",
                    service=name,
                    added=plan.expansions_added,
                    skipped=plan.expansions_skipped,
                )
            plans.append(plan)
        except Exception as exc:  # noqa: BLE001 — one service must not stop the others
            log.warning(
                "service_seed_generation_failed", service=name, error=str(exc)[:200]
            )
            plans.append(
                ServiceSeedPlan(
                    service=name,
                    service_id=_norm_key(name),
                    status="seed_generation_failed",
                    error=f"{type(exc).__name__}: {str(exc)[:160]}",
                )
            )

    return plans


def industry_seed_plan(industry: str, terms: Iterable[str]) -> ServiceSeedPlan:
    """The industry layer as a plan of its own: category terms for the client's industry
    ("digital marketing agency", "online marketing company") that a home page targets.

    It belongs to no client service. Its service id is ``industry``, every seed is tagged
    ``seed_origin="industry"`` and ``target_type="industry"``, and every seed is extra, so
    the layer is fetched on top of the seed budget. Each term is cleaned like any seed.
    """
    name = " ".join(str(industry or "").split())
    seeds: list[str] = []
    targets: dict[str, dict[str, Any]] = {}
    for term in terms:
        cleaned = normalize_seed_root(term)
        if not cleaned or cleaned.lower() in targets:
            continue
        targets[cleaned.lower()] = {
            "target": cleaned,
            "target_type": "industry",
            "seed_origin": INDUSTRY_ORIGIN,
            "service": name,
            "service_id": INDUSTRY_SERVICE_ID,
        }
        seeds.append(cleaned)
    return ServiceSeedPlan(
        service=name,
        service_id=INDUSTRY_SERVICE_ID,
        seeds=seeds,
        seed_targets=targets,
        status="planned" if seeds else "no_seeds",
    )


def is_industry_run(run: Mapping[str, Any]) -> bool:
    """True for the industry layer's run in ``service_runs``; every other run is a service."""
    return run.get("kind") == INDUSTRY_ORIGIN


# ----------------------------------------------------------------------- budget


def allocate_seed_budget(counts: list[int], total_cap: int) -> list[int]:
    """Split a seed budget across services.

    Every service with at least one seed gets at least one slot, so a later service is
    never starved by an earlier one. A service that needs less than its share hands the
    rest to the others. Leftover goes to earlier (higher priority) services first.
    """
    alloc = [0] * len(counts)
    wanting = [i for i, c in enumerate(counts) if c > 0]
    if not wanting:
        return alloc
    remaining = max(int(total_cap), len(wanting))
    for i in wanting:
        alloc[i] = 1
        remaining -= 1
    while remaining > 0:
        active = [i for i in wanting if alloc[i] < counts[i]]
        if not active:
            break
        share = max(1, remaining // len(active))
        for i in active:
            give = min(share, counts[i] - alloc[i], remaining)
            alloc[i] += give
            remaining -= give
            if remaining == 0:
                break
    return alloc


def extra_seeds_last(
    runs: list[dict[str, Any]], plans: list[ServiceSeedPlan]
) -> list[dict[str, Any]]:
    """The same runs, with each service's extra seeds (synonyms and model-suggested
    sub-service topics) moved to the end of its list.

    For later steps that take a fixed number of seeds across services (related-keyword
    lookups): an extra seed only takes a slot the service's own seeds leave free.
    """
    extra_keys = {
        (plan.service, key)
        for plan in plans
        for key, meta in plan.seed_targets.items()
        if meta.get("seed_origin") in EXTRA_ORIGINS
    }
    out: list[dict[str, Any]] = []
    for run in runs:
        seeds = list(run.get("seeds") or [])
        own = [s for s in seeds if (run.get("service"), s.lower()) not in extra_keys]
        extra = [s for s in seeds if (run.get("service"), s.lower()) in extra_keys]
        out.append({**run, "seeds": own + extra})
    return out


def related_seed_pairs(
    runs: list[dict[str, Any]], plans: list[ServiceSeedPlan], limit: int
) -> list[tuple[str, str]]:
    """``(service, seed)`` pairs for the related-keyword step.

    The service's own seeds and synonyms share ``limit`` in rotation, as before.
    Every fetched sub-service seed is added on top, one service at a time, so the
    budget is raised by exactly the sub-service seeds and none of them is starved by
    the cap (operator decision, 2026-10-27).
    """
    by_service = {plan.service: plan for plan in plans}
    base: list[dict[str, Any]] = []
    subs: list[dict[str, Any]] = []
    for run in extra_seeds_last(runs, plans):
        plan = by_service.get(run.get("service"))
        seeds = list(run.get("seeds") or [])
        sub = [x for x in seeds if plan is not None and plan.is_sub_service(x)]
        base.append({**run, "seeds": [x for x in seeds if x not in sub]})
        subs.append({**run, "seeds": sub})
    total = sum(len(r["seeds"]) for r in subs)
    return round_robin_seeds(base, limit) + round_robin_seeds(subs, total)


def round_robin_seeds(runs: list[dict[str, Any]], limit: int) -> list[tuple[str, str]]:
    """``(service, seed)`` pairs, one service at a time in turn, so a cap on a later
    per-seed step (related-keyword lookups) cannot be spent on the first service alone."""
    queues = [(r["service"], list(r.get("seeds") or [])) for r in runs]
    out: list[tuple[str, str]] = []
    i = 0
    while len(out) < limit and any(q for _, q in queues):
        service, queue = queues[i % len(queues)]
        if queue:
            out.append((service, queue.pop(0)))
        i += 1
    return out


# ------------------------------------------------------------------------ fetch


def _norm(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def _assert_isolated(
    plan: ServiceSeedPlan, seeds: list[str], result: dict[str, Any]
) -> None:
    allowed = {_norm(s) for s in seeds}
    for cluster in result.get("seed_clusters") or []:
        if _norm(cluster.get("seed")) not in allowed:
            raise ServiceIsolationError(
                f"{plan.service}: cluster for seed {cluster.get('seed')!r} is not one of "
                f"this service's seeds"
            )
    for row in result.get("keyword_dataset") or []:
        if row.get("seed") is not None and _norm(row.get("seed")) not in allowed:
            raise ServiceIsolationError(
                f"{plan.service}: keyword {row.get('keyword')!r} came from seed "
                f"{row.get('seed')!r}, which is not one of this service's seeds"
            )


def _sub_service_tag(plan: ServiceSeedPlan, seed: Any) -> dict[str, Any]:
    """``sub_service`` fields for an item that came from one of the plan's sub-service seeds."""
    meta = plan.seed_targets.get(_norm(seed)) or {}
    if meta.get("seed_origin") not in SUB_SERVICE_ORIGINS:
        return {}
    return {
        "sub_service": meta.get("sub_service") or meta.get("target"),
        "sub_service_origin": "catalog" if meta.get("seed_origin") == CATALOG_SUB_ORIGIN else "model",
        "parent_service": plan.service,
    }


def _stamp(plan: ServiceSeedPlan, result: dict[str, Any]) -> None:
    """Record the owning service on every cluster and keyword the service produced,
    and the sub-service too when the item came from a sub-service seed."""
    tag = {"service": plan.service, "service_id": plan.service_id}
    for key in ("seed_clusters", "keyword_dataset", "excluded_keywords"):
        for item in result.get(key) or []:
            if isinstance(item, dict):
                item.update(tag)
                item.update(_sub_service_tag(plan, item.get("seed")))


def sub_service_report(
    plan: ServiceSeedPlan, fetched_seeds: Iterable[str], result: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """One row per planned sub-service seed: fetched with keywords, fetched but
    returned nothing, or not fetched (budget, or the service failed)."""
    fetched = {_norm(s) for s in fetched_seeds}
    kept: dict[str, int] = {}
    for row in (result or {}).get("keyword_dataset") or []:
        if isinstance(row, dict):
            kept[_norm(row.get("seed"))] = kept.get(_norm(row.get("seed")), 0) + 1
    out: list[dict[str, Any]] = []
    for seed in plan.sub_service_seeds:
        tag = _sub_service_tag(plan, seed)
        key = _norm(seed)
        if key not in fetched:
            status = "dropped_for_budget"
        elif result is None:
            status = "service_fetch_failed"
        elif kept.get(key):
            status = "fetched"
        else:
            status = "fetched_no_keywords"
        out.append(
            {
                "seed": seed,
                "sub_service": tag.get("sub_service"),
                "origin": tag.get("sub_service_origin"),
                "status": status,
                "keyword_count": kept.get(key, 0),
            }
        )
    return out


FetchFn = Callable[..., Awaitable[dict[str, Any]]]


async def run_service_scoped_seeding(
    plans: list[ServiceSeedPlan],
    *,
    fetch: FetchFn,
    total_cap: int,
    relevance_context_for: Callable[[ServiceSeedPlan], Any] | None = None,
    service_concurrency: int = 1,
    **fetch_kwargs: Any,
) -> dict[str, Any]:
    """Fetch keywords for each service from that service's seeds only.

    ``fetch`` is ``run_multi_mode_seeding``. It is called once per service with just
    that service's seeds (and just their targets), so a keyword can only ever come from
    its own service's seeds. ``service_concurrency`` bounds how many services run at
    once (default 1: each service is already concurrent per seed, so this keeps the
    provider rate the old single call had). Every service is an isolated task that
    shares no mutable state with the others.

    The return value has the same shape ``run_multi_mode_seeding`` returned, plus
    ``service_runs`` (what happened per service). Clusters and keyword rows carry
    ``service`` / ``service_id``.

    Synonyms and sub-service seeds (catalog and model-suggested) are extra:
    ``total_cap`` is split over every other seed, and each service's extra seeds ride
    on top of its share, so the budget is raised by them rather than spent on them.
    Each run reports its sub-service seeds (``sub_service_seeds``): fetched with
    keywords, fetched with none, or not fetched and why.

    The industry plan (``industry_seed_plan``) has extra seeds only: it takes no share,
    fetches all of them, and its run is marked ``kind: "industry"``. It never counts as
    a service in the note, the failure codes or the every-service-failed check.
    """
    counts = [
        len(p.seeds) - p.extra_seed_count if p.status == "planned" else 0 for p in plans
    ]
    shares = allocate_seed_budget(counts, total_cap)
    # The dollar cap is split the same way as the seed count. Without this, the
    # first services to expand spend the whole phase budget and later services
    # come back with seeds but zero keywords.
    remaining = remaining_phase_budget_usd()
    dollar_for_plan: list[float | None] = [None] * len(plans)
    if remaining is not None:
        planned_idx = [i for i, p in enumerate(plans) if p.status == "planned" and p.seeds]
        industry_idx = [i for i in planned_idx if plans[i].is_industry]
        service_idx = [i for i in planned_idx if not plans[i].is_industry]
        # The industry plan takes a fixed slice first; the services split the rest.
        industry_amount = (
            int(round(remaining * INDUSTRY_BUDGET_SHARE * 10000)) / 10000 if industry_idx else 0.0
        )
        amounts = list(zip(service_idx, service_budget_shares(len(service_idx), remaining - industry_amount)))
        amounts += [(i, industry_amount) for i in industry_idx[:1]]
        for i, amount in amounts:
            # Reserve every share now. The first service to expand cannot spend
            # a share that belongs to a service still waiting.
            dollar_for_plan[i] = reserve_phase_amount(amount)
    gate = asyncio.Semaphore(max(1, int(service_concurrency)))

    def _pick(plan: ServiceSeedPlan, share: int) -> list[str]:
        """Every extra seed, plus the first ``share`` of the others, in plan order.

        The industry plan has only extra seeds, so it has no share and still fetches
        them all.
        """
        if share <= 0 and not plan.is_industry:
            return []
        picked: list[str] = []
        own = 0
        for seed in plan.seeds:
            if plan.is_extra(seed):
                picked.append(seed)
            elif own < share:
                picked.append(seed)
                own += 1
        return picked

    async def _run_one(
        plan: ServiceSeedPlan, share: int, dollar_cap: float | None
    ) -> dict[str, Any]:
        try:
            return await _run_service(plan, share, dollar_cap)
        finally:
            if dollar_cap:
                release_phase_amount(dollar_cap)

    async def _run_service(
        plan: ServiceSeedPlan, share: int, dollar_cap: float | None
    ) -> dict[str, Any]:
        run: dict[str, Any] = {
            "service": plan.service,
            "service_id": plan.service_id,
            "status": plan.status,
            "seeds": [],
            "seed_count": 0,
            "keyword_count": 0,
            "error": plan.error,
            "result": None,
        }
        if plan.is_industry:
            run["kind"] = INDUSTRY_ORIGIN
        seeds = _pick(plan, share)
        run["sub_service_counts"] = {
            "catalog": len(plan.catalog_subs_added),
            "model": len(plan.expansions_added),
            "skipped": len(plan.catalog_subs_skipped) + len(plan.expansions_skipped),
        }
        run["sub_service_skipped"] = [
            {**s, "origin": "catalog"} for s in plan.catalog_subs_skipped
        ] + [{**s, "origin": "model"} for s in plan.expansions_skipped]
        run["sub_service_seeds"] = sub_service_report(plan, seeds, None)
        if plan.status != "planned" or not seeds:
            if plan.status == "planned":
                run["status"] = "no_seeds"
            return run
        keys = {s.lower() for s in seeds}
        targets = {k: dict(v) for k, v in plan.seed_targets.items() if k in keys}
        run["seeds"] = seeds
        run["seed_count"] = len(seeds)
        async with gate:
            try:
                ctx = relevance_context_for(plan) if relevance_context_for else None

                async def _fetch() -> dict[str, Any]:
                    return await fetch(
                        seeds,
                        seed_targets=targets,
                        max_seeds=len(seeds),
                        relevance_context=ctx,
                        **fetch_kwargs,
                    )

                if dollar_cap is None:
                    result = await _fetch()
                else:
                    with service_research_budget(dollar_cap, phase_hold=dollar_cap):
                        result = await _fetch()
                _assert_isolated(plan, seeds, result)
                _stamp(plan, result)
            except Exception as exc:  # noqa: BLE001 — isolate the failure to this service
                log.warning(
                    "service_keyword_fetch_failed",
                    service=plan.service,
                    error=str(exc)[:200],
                )
                run["status"] = "failed"
                run["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
                run["exception"] = exc
                return run
        run["status"] = "ok"
        run["result"] = result
        run["sub_service_seeds"] = sub_service_report(plan, seeds, result)
        run["keyword_count"] = int(
            result.get("keyword_count") or len(result.get("keyword_dataset") or [])
        )
        # Visible per service: how its keywords' volume is known (measured /
        # estimated / unverified) and its funnel mix after any informational top-up.
        run["volume_coverage"] = result.get("volume_coverage") or {}
        run["funnel_counts"] = result.get("funnel_counts") or {}
        run["informational_topups"] = int(result.get("informational_topups") or 0)
        log.info(
            "service_keyword_coverage",
            service=plan.service,
            volume=run["volume_coverage"],
            funnel=run["funnel_counts"],
            informational_topups=run["informational_topups"],
        )
        return run

    runs = await asyncio.gather(
        *[_run_one(p, b, dollar_for_plan[i]) for i, (p, b) in enumerate(zip(plans, shares))]
    )

    # The industry layer cannot stand in for the services: when every service failed,
    # the run fails as before, whatever the industry fetch did.
    attempted = [r for r in runs if r["status"] in {"ok", "failed"} and not is_industry_run(r)]
    if attempted and all(r["status"] == "failed" for r in attempted):
        # Nothing worked at all: surface it the way the single call did.
        raise attempted[0]["exception"]
    return merge_service_runs(list(runs), fetch_kwargs.get("min_volume"))


def merge_service_runs(
    runs: list[dict[str, Any]], min_volume: int | None = None
) -> dict[str, Any]:
    """Combine per-service results into the single-call shape. Nothing is de-duplicated
    across services; every service keeps all of its own seeds and keywords."""
    good = [r for r in runs if r["status"] == "ok" and r["result"] is not None]
    seed_clusters: list[dict[str, Any]] = []
    dataset: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    counts = {"exact": 0, "phrase": 0, "related": 0, "broad": 0}
    target_type_counts: dict[str, int] = {}
    errors: set[str] = set()
    providers: list[str] = []
    seeds: list[str] = []
    audits: list[dict[str, Any] | None] = []
    volume_totals: dict[str, int] = {}
    funnel_totals: dict[str, int] = {}
    funnel_reports: list[dict[str, Any]] = []
    full = 0
    expansion_unavailable = False
    for run in good:
        res = run["result"]
        seed_clusters.extend(res.get("seed_clusters") or [])
        dataset.extend(res.get("keyword_dataset") or [])
        coverage.extend(res.get("class_coverage") or [])
        excluded.extend(res.get("excluded_keywords") or [])
        for cls, n in (res.get("class_counts") or {}).items():
            counts[cls] = counts.get(cls, 0) + int(n or 0)
        for tt, n in (res.get("target_type_counts") or {}).items():
            target_type_counts[tt] = target_type_counts.get(tt, 0) + int(n or 0)
        errors.update(str(e) for e in res.get("provider_errors") or [])
        for p in res.get("providers") or []:
            if p not in providers:
                providers.append(p)
        seeds.extend(res.get("seeds") or run["seeds"])
        audits.append(res.get("keyword_cleaning"))
        full += int(res.get("seeds_with_all_classes") or 0)
        for name, n in (res.get("volume_coverage") or {}).items():
            volume_totals[name] = volume_totals.get(name, 0) + int(n or 0)
        for name, n in (res.get("funnel_counts") or {}).items():
            funnel_totals[name] = funnel_totals.get(name, 0) + int(n or 0)
        for report in res.get("funnel_coverage") or []:
            funnel_reports.append({**report, "service": run["service"]})
        expansion_unavailable = expansion_unavailable or bool(
            res.get("provider_expansion_unavailable")
        )
    for run in runs:
        if run["status"] in {"failed", "seed_generation_failed"}:
            label = "industry_seeding_failed" if is_industry_run(run) else "service_seeding_failed"
            errors.add(f"{label}:{run['service']}:{str(run.get('error') or '')[:80]}")

    merged_audit = merge_cleaning_audits(*audits) if audits else {}
    service_runs = [
        {k: v for k, v in r.items() if k not in {"result", "exception"}} for r in runs
    ]
    # The note counts services; the industry layer is not one of them.
    service_count = sum(1 for r in runs if not is_industry_run(r))
    services_fetched = sum(1 for r in good if not is_industry_run(r))
    industry_fetched = any(is_industry_run(r) for r in good)
    return {
        "min_volume": min_volume,
        "seeds": seeds,
        "seed_clusters": seed_clusters,
        "keyword_dataset": dataset,
        "keyword_count": len(dataset),
        "class_counts": counts,
        "target_type_counts": target_type_counts,
        "class_coverage": coverage,
        "seeds_with_all_classes": full,
        "provider_errors": sorted(errors),
        "providers": providers,
        "provider_expansion_unavailable": expansion_unavailable,
        "provider_fallback_used": expansion_unavailable,
        "excluded_keywords": excluded,
        "keyword_cleaning": compact_cleaning_audit(merged_audit) or merged_audit,
        "volume_coverage": volume_totals,
        "funnel_counts": funnel_totals,
        "funnel_coverage": funnel_reports,
        "informational_topups": sum(1 for r in funnel_reports if r.get("topped_up")),
        "service_runs": service_runs,
        "note": (
            f"Per-service multi-mode seeding: {services_fetched} of {service_count} service(s) "
            f"fetched, each from its own seeds only ({len(seed_clusters)} seed root(s) in total)."
            + (" Industry-level seeds were fetched on top." if industry_fetched else "")
        ),
    }


def flatten_service_plans(
    plans: list[ServiceSeedPlan],
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """The flat union of every plan's seeds.

    Only for steps that *report on* or *measure* seeds (pool sizing, relevance
    evidence, notices). Nothing fetches keywords from this union; keyword fetching
    always goes through ``run_service_scoped_seeding`` with one plan at a time.
    """
    roots: list[str] = []
    targets: dict[str, dict[str, Any]] = {}
    for plan in plans:
        for seed in plan.seeds:
            key = seed.lower()
            if key not in targets:
                targets[key] = dict(plan.seed_targets.get(key) or {})
                roots.append(seed)
    return roots, targets
