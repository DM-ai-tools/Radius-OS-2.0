"""Phase 5 — Search Demand: seed → cluster (intent/funnel) → sitemap classify → draft new topics."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import load_skill, load_skill_file
from app.config import get_settings
from app.integrations import dataforseo
from app.integrations.llm import extract_domain
from app.logging_config import get_logger
from app.models import (
    AgentJob,
    Client,
    DiscoveryResponse,
    FindingsLedger,
)
from app.services.agent_handoff import blocked_events, consume_events, handoff_events
from app.services.agent_runtime import get_profile, supersede_pending_findings
from app.services.audit import log_event
from app.services.competitive_context import (
    competitor_context_blob,
    local_seed_variants,
    resolve_competitors,
    resolve_geographic_focus,
)
from app.services.competitive_context import (
    competitor_domains as domains_from_comps,
)
from app.services.competitive_context import (
    competitor_names as names_from_comps,
)
from app.services.competitor_keyword_intel import (
    merge_competitor_rows_into_cluster_input,
    refresh_gap_flags,
)
from app.services.create_topic import (
    format_audience_label,
    run_create_topic_for_new_clusters,
    topics_from_plan,
)
from app.services.keyword_clustering import (
    build_service_seed_clusters,
    clusters_for_cdp,
    run_keyword_clustering,
)
from app.services.keyword_opportunity import (
    build_topics,
    detect_funnel,
    detect_intent,
    enrich_service_clusters_with_competitors,
    intent_balance,
    intent_balance_warnings,
    is_stale_year_keyword,
    merge_keyword_metrics,
    prune_redundant_acronym_seeds,
    rank_opportunities,
    select_topics_from_service_clusters,
    stamp_keyword_intent,
)
from app.services.keyword_relevance import (
    build_relevance_context,
    compact_cleaning_audit,
    competitor_brand_blocklist,
    filter_relevant_keywords,
    is_competitor_brand_term,
    merge_cleaning_audits,
    page_terms_from_website,
    theme_terms_from_website,
    website_resource_blobs,
)
from app.services.keyword_pool import (
    resolve_client_pool_target,
    resolve_keyword_pool_limits,
)
from app.services.keyword_rejection_log import record_keyword_rejections
from app.services.keyword_metrics import measured_rows_for_pool
from app.services.keyword_seed_identification import identify_multiple_seed_clusters
from app.services.keyword_seeding import (
    clean_provider_seed,
    filter_seed_clusters,
    flatten_dataset,
    run_multi_mode_seeding,
    summarize_keyword_data_quality,
)
from app.services.live_site_scan import scan_live_site
from app.services.role_skills import required_role_for
from app.services.query_variants import QueryVariantResolver
from app.services.seed_synonyms import SeedSynonymResolver, audit_entries, operator_notice
from app.services.subservice_expansion import audit_entries as expansion_audit_entries
from app.services.subservice_expansion import operator_notice as expansion_notice
from app.services.service_prioritization import service_identity_key
from app.services.service_labs_research import (
    collect_competitor_keywords,
    partition_keywords_by_service,
)
from app.services.service_seeding import (
    flatten_service_plans,
    plan_service_seeds,
    round_robin_seeds,
    run_service_scoped_seeding,
    extra_seeds_last,
)
from app.services.url_mapping import classify_clusters_against_sitemap

log = get_logger("search_demand")

# Research depth offered on the service-selection card. Each preset drives both
# the keyword pool target and how many seed roots Phase 5 will expand — the root
# cap is the real bottleneck, so depth that did not move it would be cosmetic.
KEYWORD_DEPTH_PRESETS = [
    {"id": "focused", "label": "Focused", "keyword_pool_target": 1500, "max_seed_roots": 6},
    {"id": "standard", "label": "Standard", "keyword_pool_target": 4500, "max_seed_roots": 16},
    {"id": "deep", "label": "Deep", "keyword_pool_target": 8000, "max_seed_roots": 24},
]


def _authority_for_ranking(
    website: dict[str, Any], competitive: dict[str, Any]
) -> tuple[float | None, str]:
    """Provider Domain Rating only. Homepage estimates are not used as authority."""
    pack = (
        website.get("backlink_summary")
        or website.get("backlinks")
        or website.get("authority")
        or {}
    )
    bl = pack.get("summary") if isinstance(pack, dict) and isinstance(pack.get("summary"), dict) else pack
    if not isinstance(bl, dict):
        bl = {}
    source = str(bl.get("source") or "")
    provider_name = str(bl.get("provider") or "")
    if (
        source == "heuristic"
        or bl.get("authority_is_estimate")
        or provider_name == "live_site_signals"
    ):
        return None, "heuristic"
    measured = bl.get("authority_score")
    if measured is None:
        measured = bl.get("domain_rating")
    if measured is not None and (
        source == "provider" or provider_name in ("ahrefs", "semrush", "moz")
    ):
        try:
            return float(measured), "provider"
        except (TypeError, ValueError):
            return None, "provider"
    baseline = competitive.get("client_baseline_maturity")
    if baseline is not None:
        try:
            return float(baseline), "baseline"
        except (TypeError, ValueError):
            return None, "baseline"
    return None, "unknown"


def max_seed_roots_for_pool_target(pool_target: int) -> int:
    """Seed-root cap for a pool target, taken from the nearest depth preset.

    Phase 5 used a hard cap of 8 roots regardless of pool target, so a bigger
    pool only bought more keywords per root. Depth has to move this number for
    the choice to mean anything.
    """
    target = int(pool_target or 0)
    nearest = min(
        KEYWORD_DEPTH_PRESETS,
        key=lambda p: abs(int(p["keyword_pool_target"]) - target),
    )
    return int(nearest["max_seed_roots"])


def filter_merged_to_seed_pool(
    merged: list[dict[str, Any]],
    *,
    seed_kw_set: set[str],
    competitor_kw_set: set[str],
) -> list[dict[str, Any]]:
    """Narrow merged provider rows to the Phase 5 keyword pool.

    Keeps seeded keywords, competitor gap signals, and keywords harvested from
    competitor pages. Membership is checked against the harvested keyword set
    because ``merge_keyword_metrics`` rebuilds each row and does not carry the
    originating ``source`` through.
    """
    if not seed_kw_set:
        return merged
    return [
        row
        for row in merged
        if _norm_kw(row.get("keyword")) in seed_kw_set
        or row.get("gap_flag")
        or _norm_kw(row.get("keyword")) in competitor_kw_set
    ]


def stored_competitor_page_intel(commercial: dict[str, Any]) -> dict[str, Any]:
    """Read back the competitor page harvest from the service-selection turn."""
    intel = commercial.get("competitor_keyword_intel")
    if not isinstance(intel, dict):
        return {"keywords": [], "snapshots": [], "stats": {}, "collected_at": None}
    return {
        "keywords": [k for k in (intel.get("keywords") or []) if isinstance(k, dict)],
        "snapshots": [s for s in (intel.get("snapshots") or []) if isinstance(s, dict)],
        "stats": dict(intel.get("stats") or {}),
        "collected_at": intel.get("collected_at"),
    }


def scope_competitor_keywords_to_services(
    rows: list[dict[str, Any]] | None,
    services: list[str],
) -> list[dict[str, Any]]:
    """Keep competitor page keywords that match the confirmed service selection."""
    from app.services.service_prioritization import term_matches_selected_services

    if not services:
        return [r for r in (rows or []) if isinstance(r, dict)]
    return [
        r
        for r in (rows or [])
        if isinstance(r, dict)
        and term_matches_selected_services(str(r.get("keyword") or ""), services)
    ]


def _split_keywords(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw)
    parts = []
    for chunk in text.replace(";", ",").replace("\n", ",").split(","):
        c = chunk.strip()
        if c:
            parts.append(c)
    return parts


def verified_keywords_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows that carry an actual relevance verdict.

    Quarantined rows stay in the reported dataset so an operator can see what
    could not be checked, but they must not reach opportunity ranking, SERP
    spend, clustering or topic drafting — an unverified keyword that becomes a
    cluster's primary keyword is exactly the failure this gate exists to stop.
    """
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and not row.get("relevance_unverified")
        and row.get("relevance_reason") != "relevance_unverified"
    ]


def _norm_kw(value: Any) -> str:
    """Normalize keywords for set membership comparisons."""
    return " ".join(str(value or "").split()).strip().lower()


def _products_list(commercial: dict, marketing: dict, intake: dict, cdd: dict | None = None) -> list[str]:
    cdd = cdd or {}
    raw_lists = [
        commercial.get("products"),
        commercial.get("products_for_promotion"),
        marketing.get("products"),
        marketing.get("products_for_promotion"),
        intake.get("products"),
        intake.get("products_for_promotion"),
        cdd.get("products"),
        cdd.get("products_for_promotion"),
    ]
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_lists:
        items = _split_keywords(raw) if not isinstance(raw, list) else [
            str(x).strip() for x in raw if str(x).strip()
        ]
        for item in items:
            # "Web Development" and "Website Development" are one offering.
            key = service_identity_key(item)
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _primary_client_services(
    commercial: dict,
    marketing: dict,
    intake: dict,
    cdd: dict | None = None,
) -> list[str]:
    """Top client services for the prioritization card — CDD `products` only.

    Falls back to `products_for_promotion` when the top-services field is empty.
    Does not union every marketing/intake list (that flooded the card).
    """
    cdd = cdd or {}
    primary_sources = [
        commercial.get("products"),
        marketing.get("products"),
        intake.get("products"),
        cdd.get("products"),
    ]
    promo_sources = [
        commercial.get("products_for_promotion"),
        marketing.get("products_for_promotion"),
        intake.get("products_for_promotion"),
        cdd.get("products_for_promotion"),
    ]

    def _collect(raw_lists: list[Any]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in raw_lists:
            items = (
                _split_keywords(raw)
                if not isinstance(raw, list)
                else [str(x).strip() for x in raw if str(x).strip()]
            )
            for item in items:
                key = service_identity_key(item)
                if key not in seen:
                    seen.add(key)
                    out.append(item)
        return out

    primary = _collect(primary_sources)
    return primary or _collect(promo_sources)


def _page_target_seeds(website: dict[str, Any]) -> list[dict[str, Any]]:
    """Core targeting terms for each existing service / sub-service page (Phase 3)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    _service_roots = {
        "service",
        "services",
        "solution",
        "solutions",
        "offerings",
        "products",
        "product",
        "what-we-do",
    }

    def _path_segments(path: str) -> list[str]:
        return [s for s in path.replace("//", "/").strip("/").split("/") if s]

    def _parent_segment(path: str, page_type: str) -> str | None:
        segs = _path_segments(path)
        if page_type != "sub_service" or len(segs) < 2:
            return None
        if segs[0].lower() in _service_roots and len(segs) >= 3:
            return segs[1]
        return segs[0]

    def _term_from_page(page: dict[str, Any]) -> str | None:
        # Name the page from its own title or URL, never from a CDD field.
        title = str(page.get("title") or "").strip()
        if title and len(title) <= 70 and title.lower() not in ("home", "services"):
            return title
        path = str(page.get("path") or page.get("url") or "").strip()
        seg = _path_segments(path)
        if seg:
            last = seg[-1].replace("-", " ").replace("_", " ").strip()
            if last and len(last) > 2 and last.lower() not in _service_roots:
                return last
        return None

    def _seed_page_type(ptype: str, cluster: str) -> str:
        kind = ptype.strip().lower().replace("-", "_")
        group = cluster.strip().lower()
        if kind in {"sub_service", "subservice"} or group == "sub_services":
            return "sub_service"
        if kind in {"service", "services", "core_service"} or group in {"services", "service"}:
            return "service"
        if kind in {"hub", "service_hub"} or group == "service_hub":
            return "hub"
        return kind or "page"

    def _add(page: dict[str, Any], term: str | None) -> None:
        t = str(term or "").strip()
        key = t.lower()
        if not t or key in seen:
            return
        seen.add(key)
        path = str(page.get("path") or page.get("url") or "").strip()
        ptype = _seed_page_type(str(page.get("page_type") or ""), str(page.get("cluster") or ""))
        out.append(
            {
                "term": t,
                "page_type": ptype or "page",
                "path": path,
                "parent_segment": _parent_segment(path, ptype),
            }
        )

    for blob in website_resource_blobs(website):
        pages = blob.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict):
                    continue
                ptype = _seed_page_type(
                    str(page.get("page_type") or ""), str(page.get("cluster") or "")
                )
                if ptype in ("service", "sub_service"):
                    _add(page, _term_from_page(page))
        hier = blob.get("page_hierarchy")
        if isinstance(hier, list):
            for row in hier:
                if not isinstance(row, dict):
                    continue
                if str(row.get("cluster") or "") in ("services", "sub_services", "service_hub"):
                    _add(row, _term_from_page(row))
    return out


# Descriptive competitor fields that read as market language. ``tier_name`` is
# deliberately absent: "Strong Direct Competitors" is the tier model's label, and
# seeding on it sent a classification word to keyword research.
_COMPETITOR_THEME_FIELDS = ("positioning", "notes")


def _competitor_theme_seeds(
    competitive: dict[str, Any],
    *,
    seen: set[str],
    is_brand: Callable[[str], bool],
) -> list[str]:
    """Seed phrases drawn from how competitors describe themselves.

    Never the competitor's brand name, never a stale-year phrase, never one that is
    already a seed, and only from descriptive fields (see ``_COMPETITOR_THEME_FIELDS``).
    """
    taken = set(seen)
    out: list[str] = []
    for card in (
        competitive.get("scorecards"),
        competitive.get("competitors"),
        competitive.get("tier_overview"),
    ):
        if not isinstance(card, list):
            continue
        for row in card[:8]:
            if not isinstance(row, dict):
                continue
            for key in _COMPETITOR_THEME_FIELDS:
                phrase = " ".join(str(row.get(key) or "").split()[:6]).strip()
                if (
                    phrase
                    and len(phrase) > 3
                    and not is_stale_year_keyword(phrase)
                    and phrase.lower() not in taken
                    and not is_brand(phrase)
                ):
                    out.append(phrase)
                    taken.add(phrase.lower())
    return out


def _service_seed_variants(services: list[str]) -> list[str]:
    """Expand CDD services into specific seed queries (not ultra-generic heads alone)."""
    extras: list[str] = []
    templates = (
        "{s}",
        "best {s}",
        "{s} services",
        "{s} company",
        "{s} pricing",
        "hire {s}",
        "{s} near me",
        "how to choose {s}",
    )
    for s in services[:8]:
        s = s.strip()
        if not s or len(s) < 2:
            continue
        for tmpl in templates:
            extras.append(tmpl.format(s=s))
    return extras


async def _load_cdd_fields(db: AsyncSession, client_id: UUID) -> dict[str, Any]:
    """Pull CDD / discovery answers for keywords + services (operator-provided ground truth)."""
    keys = (
        "business_keywords",
        "products",
        "products_for_promotion",
        "geographic_focus",
        "known_competitors",
        "target_demographic",
        "positioning",
    )
    rows = (
        await db.execute(
            select(DiscoveryResponse)
            .where(
                DiscoveryResponse.client_id == client_id,
                DiscoveryResponse.field_key.in_(keys),
            )
            .order_by(DiscoveryResponse.created_at.desc())
        )
    ).scalars().all()
    out: dict[str, Any] = {}
    for row in rows:
        if row.field_key in out:
            continue
        val = (row.field_value or {}).get("value") if isinstance(row.field_value, dict) else row.field_value
        if val not in (None, "", [], {}):
            out[row.field_key] = val
    return out


def _retrieve_exception(task: asyncio.Future[Any]) -> None:
    """Mark a prefetch task's exception as seen; it is re-raised where it is awaited."""
    if not task.cancelled():
        task.exception()


class _Prefetch:
    """Provider calls started early so their waiting overlaps other work.

    Phase 5 used to make every provider call one after another, and spent most of
    its time waiting. A call whose inputs are known early is started here, and its
    result is collected at the call's original place in the flow, so everything
    downstream is processed in the original order and the report is unchanged.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Future[Any]] = {}

    def start(self, name: str, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        task.add_done_callback(_retrieve_exception)
        self._tasks[name] = task

    def has(self, name: str) -> bool:
        return name in self._tasks

    async def result(self, name: str, *, timeout: float | None = None) -> Any:
        """The call's result, waited for at most ``timeout`` seconds from now.

        A timeout cancels the call and raises ``asyncio.TimeoutError``, exactly as
        the original ``asyncio.wait_for`` around the call did.
        """
        task = self._tasks.pop(name)
        if timeout is None:
            return await task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            task.cancel()
            raise

    def discard(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None:
            task.cancel()

    def discard_all(self) -> None:
        for name in list(self._tasks):
            self.discard(name)


async def _harvest_competitor_page_keywords(
    *,
    client: Client,
    profile: Any,
    commercial: dict[str, Any],
    services: list[str],
    cdd_keywords: list[str],
    website: dict[str, Any],
    competitors: list[dict[str, Any]],
    competitor_labels: list[str],
    competitor_domains: list[str],
    seeds: list[str],
    location_code: int,
) -> dict[str, Any]:
    """Crawl approved competitors' pages for keyword targets.

    Runs on the confirmed keyword turn, scoped to the selected services, alongside
    keyword expansion. It changes nothing itself: the caller stores the result with
    ``_store_competitor_page_intel`` when it collects it, so the profile is only
    ever written from the main flow.
    """
    from app.services.competitor_keyword_intel import collect_competitor_page_keywords

    harvest_ctx = build_relevance_context(
        services=services,
        cdd_keywords=cdd_keywords,
        website=website,
        seeds=seeds,
        competitor_names=competitor_labels,
        competitor_domains=competitor_domains,
        brand_name=client.display_name,
        domain=extract_domain(client.primary_url) or None,
        industry=client.industry,
    )
    return await collect_competitor_page_keywords(
        competitors=competitors,
        relevance_context=harvest_ctx,
        location_code=location_code,
    )


def _store_competitor_page_intel(
    profile: Any, commercial: dict[str, Any], intel: dict[str, Any]
) -> None:
    """Keep the harvest on the client profile for later runs and phases."""
    stored = {
        "keywords": list(intel.get("keywords") or []),
        "snapshots": list(intel.get("snapshots") or []),
        "stats": dict(intel.get("stats") or {}),
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }
    # Reassign the JSON column so SQLAlchemy sees the mutation.
    profile.commercial_scope = {
        **(profile.commercial_scope or {}),
        "competitor_keyword_intel": stored,
    }
    commercial["competitor_keyword_intel"] = stored


async def run_search_demand(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("search_demand")
    _ = load_skill("seo_dataforseo")
    _ = load_skill_file("create-topic")
    _ = load_skill_file("keyword-clustering")
    _ = user_id

    events: list[dict] = []
    # The chat turn is cancelled at the agent budget. A cancel before the
    # profile write drops the whole run, so later steps stop while there is
    # still time to cluster and save.
    from app.orchestration.pipeline import agent_timeout_seconds

    phase_budget = agent_timeout_seconds("search_demand")
    phase_started = time.monotonic()

    def _seconds_left() -> float:
        return phase_budget - (time.monotonic() - phase_started)

    # Seconds since the run started, at the end of each stage. Logged and saved on
    # the summary so a slow run shows where its time went.
    stage_timings: dict[str, float] = {}

    def _mark(stage: str) -> None:
        stage_timings[stage] = round(time.monotonic() - phase_started, 1)

    phase_deadline = phase_started + max(60, phase_budget - 240)
    profile = await get_profile(db, client.id)

    if profile.competitor_status != "complete":
        events.extend(
            blocked_events(
                "search_demand",
                "Approve Phase 4 Competitor Landscape first so keyword research uses the locked competitor set.",
                route_to="competitor_market_agent",
            )
        )
        return events

    commercial = dict(profile.commercial_scope or {})
    marketing = dict(profile.marketing_context or {})
    intake = dict(marketing.get("client_intake") or {})
    from app.services.site_sitemap import (
        ensure_website_sitemap,
        sitemap_is_truncated,
        sitemap_pages,
        sitemap_urls,
    )

    # Derive a working sitemap copy for this run only. Never write it back onto
    # website_situation_summary — that pack is the signed-off Phase 3 output.
    website = ensure_website_sitemap(
        dict(profile.website_situation_summary or {}),
        primary_url=client.primary_url,
    )
    competitive = dict(profile.competitive_landscape_summary or {})
    cdd = await _load_cdd_fields(db, client.id)

    has_cdd = bool(
        cdd.get("business_keywords")
        or cdd.get("products")
        or cdd.get("products_for_promotion")
        or intake
        or marketing.get("business_keywords")
        or commercial.get("business_keywords")
    )
    if profile.discovery_status not in ("complete", "pending_signoff") and not has_cdd:
        events.extend(
            blocked_events(
                "search_demand",
                "Seeds from CDD / Discovery answers (keywords, services, geo). Upload CDD and complete Discovery first.",
                route_to="discovery_agent",
            )
        )
        return events

    events.extend(
        consume_events(
            "search_demand",
            pack_notes=[
                f"discovery={profile.discovery_status}",
                f"competitors={profile.competitor_status}",
                f"cdd_keywords={len(_split_keywords(cdd.get('business_keywords')))}",
            ],
        )
    )

    profile.search_demand_status = "in_progress"
    events.append(
        {
            "type": "system_notice",
            "content": "Running Search Demand — multi-mode seeding (exact / phrase / related / broad, volume > 10) via Ahrefs + DataForSEO…",
        }
    )

    # CDD business keywords first, then commercial/intake
    seeds = _split_keywords(
        cdd.get("business_keywords")
        or commercial.get("business_keywords")
        or intake.get("business_keywords")
        or marketing.get("business_keywords")
    )
    cdd_keywords = list(seeds)
    products = _products_list(commercial, marketing, intake, cdd)
    services = products[:]  # alias for clarity in reports

    # Service-specific seeds from CDD products / promotion list
    for extra in _service_seed_variants(services):
        seeds.append(extra)

    if not seeds and services:
        seeds = services[:8]
    if not seeds and client.industry:
        seeds = [client.industry, f"{client.industry} services"]
    if not seeds:
        seeds = [client.display_name]

    # Website themes as extra seeds (draft + approved nested audit payloads)
    for blob in website_resource_blobs(website):
        for key in ("top_pages", "themes", "target_keywords"):
            val = blob.get(key)
            if isinstance(val, list):
                for item in val[:5]:
                    if isinstance(item, dict):
                        for fk in ("keyword", "title", "theme"):
                            if item.get(fk):
                                seeds.append(str(item[fk])[:80])
                    elif item:
                        seeds.append(str(item)[:80])

    # Competitor blog titles as gap seed hints (service-overlap only)
    # filled after competitor_sites fetch — placeholder list for later merge
    competitor_title_seeds: list[str] = []

    geo = resolve_geographic_focus(
        {**commercial, **({"geographic_focus": cdd["geographic_focus"]} if cdd.get("geographic_focus") else {})},
        intake,
        marketing,
    )
    country = str(geo.get("country") or "us")
    location_code = int(geo.get("location_code") or 2840)
    labs_location_code = int(geo.get("labs_location_code") or 2840)
    language_code = str(geo.get("language_code") or "en")

    for extra in local_seed_variants(seeds, geo):
        seeds.append(extra)

    # Dedupe seeds — prefer longer/specific seeds first
    seeds = sorted(
        {s.strip(): s.strip() for s in seeds if s and str(s).strip()}.values(),
        key=lambda s: (-len(s.split()), s.lower()),
    )
    seen: set[str] = set()
    clean_seeds: list[str] = []
    for s in seeds:
        n = s.strip().lower()
        if n and n not in seen and len(n) > 1 and not is_stale_year_keyword(n):
            seen.add(n)
            clean_seeds.append(s.strip())
    seeds = prune_redundant_acronym_seeds(clean_seeds)[:22]

    domain = extract_domain(client.primary_url) or ""

    competitors = await resolve_competitors(
        db,
        client.id,
        competitive_summary=competitive,
        marketing=marketing,
        limit=8,
        prefer_confirmed=True,
    )
    competitor_domains = domains_from_comps(competitors)
    competitor_labels = names_from_comps(competitors)

    from app.services.service_prioritization import (
        apply_service_selection,
        build_client_service_catalog,
        build_prioritization_pack,
        cap_seed_roots_preserving_selected_services,
        discover_competitor_service_trees,
        is_prioritization_confirmed,
        merge_competitor_subservices,
        merge_saved_catalog,
        prioritize_top_service_roots,
        term_matches_selected_services,
        top_priority_service_id,
    )

    prioritization = dict(commercial.get("service_prioritization") or {})
    if not is_prioritization_confirmed(prioritization):
        # Fast gate: selection card only. Competitor tree crawl + page harvest
        # used to run here and made "pick services" feel like a full research
        # turn (45s+). They now run after confirm, scoped to the selection.
        from app.services.industry_services import taxonomy_for_pack

        card_services = _primary_client_services(commercial, marketing, intake, cdd)
        card_seeds = _page_target_seeds(website)
        # The industry's major services and sub-services from the model, as opt-in
        # suggestions next to what the site revealed. Cached and time-boxed.
        events.append(
            {
                "type": "system_notice",
                "content": "Looking up the primary services and sub-services for this industry…",
            }
        )
        industry_taxonomy, industry_status = await taxonomy_for_pack(
            industry=client.industry or intake.get("industry") or marketing.get("industry"),
            services=card_services,
            website=website,
            page_seeds=card_seeds,
            geography=str(
                intake.get("geographic_focus")
                or marketing.get("geographic_focus")
                or commercial.get("geographic_focus")
                or ""
            )
            or None,
        )
        pack = build_prioritization_pack(
            client_name=client.display_name,
            services=card_services,
            website=website,
            page_seeds=card_seeds,
            competitors=competitors,
            competitor_trees=[],
            existing=prioritization,
            industry_taxonomy=industry_taxonomy,
            industry_status=industry_status,
        )
        profile.search_demand_status = "awaiting_service_selection"
        card = {
            "card_type": "service_prioritization",
            "title": pack["title"],
            "agent_key": "search_demand",
            "actions": ["confirm"],
            "required_role": required_role_for("search_demand"),
            **pack,
            "competitor_page_keywords": [],
            "competitor_page_stats": {
                "deferred": True,
                "note": (
                    "Competitor page crawl and keyword harvest run after you "
                    "confirm services — scoped to the selection."
                ),
            },
            "keyword_pool_target": resolve_client_pool_target(
                commercial=commercial,
                marketing=marketing,
                settings=get_settings(),
            ),
            "keyword_depth_presets": KEYWORD_DEPTH_PRESETS,
        }
        events.append(
            {
                "type": "agent_message",
                "agent_key": "search_demand",
                "content": (
                    "Select which services and sub-services to target (80/20) and mark "
                    "the #1 priority service. Confirm — then Search Demand crawls "
                    "competitors for those services, pulls DataForSEO, and builds the "
                    "keyword report."
                ),
            }
        )
        events.append({"type": "structured_card", "payload": card})
        events.append({"type": "checkpoint", "payload": card})
        events.append(
            {
                "type": "phase_status",
                "payload": {"search_demand_status": "awaiting_service_selection"},
            }
        )
        return events

    competitor_brand_full, competitor_brand_tokens = competitor_brand_blocklist(
        competitor_labels,
        competitor_domains,
        protected_phrases=[*services, *cdd_keywords],
    )

    def _is_competitor_brand(term: str) -> bool:
        return is_competitor_brand_term(
            term,
            brand_full=competitor_brand_full,
            brand_tokens=competitor_brand_tokens,
        )

    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Geo: {geo.get('location_name')} "
                f"(focus={geo.get('geographic_focus') or 'unset'}, "
                f"code={location_code}, labs={labs_location_code}). "
                f"CDD keywords: {len(cdd_keywords)}; services: {', '.join(services[:5]) or 'none'}. "
                f"Competitors: {', '.join(competitor_labels[:6]) or 'none yet'}."
            ),
        }
    )

    # Competitor page keywords are collected after service confirm (not on the
    # fast selection gate). Prefer any stored harvest, then always refresh for
    # the selected services so ranking pages match what the operator picked.
    page_intel = stored_competitor_page_intel(commercial)
    # Seeds for a fresh competitor-page harvest; None means no harvest this run.
    harvest_seeds: list[str] | None = None
    competitor_page_keywords = list(page_intel.get("keywords") or [])
    competitor_sites: list[dict[str, Any]] = list(page_intel.get("snapshots") or [])
    if competitor_page_keywords:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Found {len(competitor_page_keywords)} stored competitor page "
                    f"keyword(s) from {len(competitor_sites)} site(s) — will scope "
                    "to selected services and refresh the harvest."
                ),
            }
        )
    for phrase in _competitor_theme_seeds(
        competitive, seen=seen, is_brand=_is_competitor_brand
    ):
        competitor_title_seeds.append(phrase)
        seeds.append(phrase)
        seen.add(phrase.lower())
    seeds = seeds[:24]

    job = AgentJob(
        session_id=session_id,
        agent_key="search_demand",
        job_type="keyword_research",
        status="running",
    )
    db.add(job)
    await db.flush()
    # Research runs for many minutes after this. Commit so the profile row
    # is not locked for the whole run — the keywords save times out at 15s
    # while it waits on that lock.
    await db.commit()

    providers_used: list[str] = []
    provider_errors: list[str] = []
    ahrefs_rows: list[dict[str, Any]] = []
    dfs_rows: list[dict[str, Any]] = []

    # Ahrefs multi-mode seeding: expand EVERY target root (each service / product /
    # page / business keyword), not just CDD keywords. Each root -> exact/phrase/related/broad.
    seed_roots: list[str] = []
    seed_targets: dict[str, dict[str, Any]] = {}

    def _add_root(term: str, target_type: str, **meta: Any) -> None:
        raw = str(term or "").strip()
        t = clean_provider_seed(raw) or raw
        n = t.lower()
        if not t or len(n) < 2 or is_stale_year_keyword(n):
            return
        if len(t.split()) > 6:
            return
        # Never seed on a competitor's brand name — seeds describe the client.
        if _is_competitor_brand(t):
            return
        if n not in seed_targets:
            # Keep the original CDD/catalog label as target for service roots so
            # selection IDs (which preserve punctuation) still match after clean.
            target_label = raw if target_type == "service" else t
            entry: dict[str, Any] = {"target": target_label, "target_type": target_type}
            for key, value in meta.items():
                if value not in (None, ""):
                    entry[key] = value
            seed_targets[n] = entry
            seed_roots.append(t)

    # Skip template-generated variants as ROOTS (Ahrefs expansion regenerates them).
    _tmpl_prefix = ("best ", "hire ", "how to choose ", "top ", "cheap ", "affordable ")
    _tmpl_suffix = (" services", " company", " pricing", " near me", " cost", " quote")

    def _is_template_variant(term: str) -> bool:
        t = str(term or "").strip().lower()
        core = {s.strip().lower() for s in services} | {k.strip().lower() for k in cdd_keywords}
        if t in core:
            return False
        return t.startswith(_tmpl_prefix) or t.endswith(_tmpl_suffix)

    # 1) Every service / product (each individually) — primary targeting roots
    for svc in services:
        _add_root(svc, "service")
    # 2) Every CDD business keyword
    for kw in cdd_keywords:
        _add_root(kw, "keyword")
    # 3) Every existing service / sub-service page (Phase 3 hierarchy)
    for page_seed in _page_target_seeds(website):
        term = str(page_seed.get("term") or "").strip()
        ptype = str(page_seed.get("page_type") or "")
        target_type = "sub_service" if ptype == "sub_service" else "page"
        _add_root(
            term,
            target_type,
            page_type=ptype,
            page_path=page_seed.get("path"),
            parent_segment=page_seed.get("parent_segment"),
        )
    # 4) Remaining discovered seeds (website themes) — skip templated variants
    for s in seeds:
        if not _is_template_variant(s):
            _add_root(s, "keyword")

    # Prioritize services + CDD keywords first, then pages, then rest
    seed_roots = sorted(
        seed_roots,
        key=lambda s: (
            {"service": 0, "sub_service": 1, "keyword": 2, "page": 3}.get(
                seed_targets.get(s.lower(), {}).get("target_type", "keyword"), 4
            ),
            -len(s.split()),
            s.lower(),
        ),
    )

    # Research depth chosen at the selection gate decides how many roots expand.
    pool_target = resolve_client_pool_target(
        commercial=commercial,
        marketing=marketing,
        settings=get_settings(),
    )
    max_seed_roots = max_seed_roots_for_pool_target(pool_target)

    services, seed_targets = apply_service_selection(services, seed_targets, prioritization)
    # Selection may resolve catalog names that were not in the raw products list
    # — ensure every confirmed service still has an expansion root.
    for svc in services:
        _add_root(svc, "service")
    seed_roots = [r for r in seed_roots if r.lower() in seed_targets]
    # Candidate terms (service names, business keywords, pages, themes) BEFORE the
    # shared cap. Each service generates its own seeds from this pool and gets its own
    # budget; the pool itself is never sent to the keyword provider.
    candidate_roots = list(seed_roots)
    candidate_targets = {k: dict(v) for k, v in seed_targets.items()}
    seed_roots = cap_seed_roots_preserving_selected_services(
        prioritize_top_service_roots(seed_roots, seed_targets, prioritization),
        seed_targets,
        selected_services=services,
        max_seed_roots=max_seed_roots,
    )
    # After the selection gate, CDD keywords / website themes for unselected
    # offerings (SEO, email, …) must not keep feeding expansion or relevance.
    if is_prioritization_confirmed(prioritization) and services:
        cdd_keywords = [
            k for k in cdd_keywords if term_matches_selected_services(k, services)
        ]
        products = [p for p in products if term_matches_selected_services(p, services)] or list(
            services
        )
        # Scope any prior harvest, then crawl competitor pages for the confirmed
        # services so ranking keywords + DataForSEO cover what was selected.
        before_scope = len(competitor_page_keywords)
        competitor_page_keywords = scope_competitor_keywords_to_services(
            competitor_page_keywords, services
        )
        if before_scope and len(competitor_page_keywords) < before_scope:
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        f"Scoped stored competitor page keywords to selected services: "
                        f"{len(competitor_page_keywords)} of {before_scope} kept."
                    ),
                }
            )
        if competitors:
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        "Harvesting competitor pages for the confirmed services "
                        "(titles, H1s, paths) alongside DataForSEO expansion."
                    ),
                }
            )
            # The crawl takes up to 90 s and nothing reads its keywords until after
            # the relevance gate, so it runs alongside expansion (started with the
            # other prefetches below) instead of in front of it.
            harvest_seeds = list(seed_roots or services)

    service_catalog = build_client_service_catalog(
        services=[],
        website=website,
        page_seeds=_page_target_seeds(website),
    )
    if not service_catalog:
        service_catalog = [
            row
            for row in (prioritization.get("service_catalog") or [])
            if isinstance(row, dict)
        ]
    # The rebuild above only knows the website. Keep the services and sub-services the
    # operator picked from the industry suggestions, or they vanish before keyword
    # research and the site architecture that reads this catalog.
    service_catalog = merge_saved_catalog(
        service_catalog,
        prioritization.get("service_catalog"),
        selected_service_ids=list(prioritization.get("selected_service_ids") or []),
    )
    trees = list(prioritization.get("competitor_trees") or [])
    if not trees and not prioritization.get("service_catalog"):
        try:
            trees = await asyncio.wait_for(
                discover_competitor_service_trees(
                    competitors,
                    max_competitors=8,
                    max_urls_each=20,
                ),
                timeout=45,
            )
        except asyncio.TimeoutError:
            log.warning(
                "competitor_service_trees_timeout",
                client_id=str(client.id),
                stage="post_selection",
            )
            trees = []
    if trees:
        service_catalog = merge_competitor_subservices(
            service_catalog,
            competitor_tree=None,
            competitor_trees=trees,
            adopt_service_ids=list(
                prioritization.get("adopt_competitor_service_ids") or []
            ),
        )
    # Persist the exact selected hierarchy so Site Architecture does not have
    # to rebuild CDD and competitor services through separate paths.
    prioritization["service_catalog"] = service_catalog
    # Each service is also searched by its other names ("search engine optimization"
    # for "SEO"). Sub-services stay under the service and are not separate seeds.
    seed_market = str(geo.get("location_name") or geo.get("geographic_focus") or "") or None
    max_expansions = 0
    synonym_results = await SeedSynonymResolver(
        industry=client.industry,
        market=seed_market,
        other_services=services,
    ).resolve_all(services)
    expansion_results: dict = {}
    synonym_terms = [s for result in synonym_results.values() for s in result.synonyms]
    # Seeds are generated PER SERVICE: the service root, its synonyms, and the
    # candidate keywords / pages / themes that belong to it. Sub-services are not seeds.
    # `seed_roots` and `seed_targets` below are only the flat union, for the steps that
    # report on or measure seeds; keywords are fetched per service from that service's
    # own seeds.
    service_plans = plan_service_seeds(
        services,
        pool_roots=[r for r in candidate_roots if len(str(r).split()) <= 6],
        pool_targets=candidate_targets,
        catalog=service_catalog,
        prioritization=prioritization,
        top_service=top_priority_service_id(prioritization),
        is_brand=_is_competitor_brand,
        service_synonyms={name: result.synonyms for name, result in synonym_results.items()},
        service_subtopics={name: result.terms for name, result in expansion_results.items()},
        max_expansions=max_expansions,
    )
    synonym_audit = audit_entries(synonym_results, service_plans)
    synonym_notice = operator_notice(synonym_audit)
    if synonym_notice:
        events.append({"type": "system_notice", "content": synonym_notice})
    expansion_audit = expansion_audit_entries(expansion_results, service_plans)
    subservice_notice = expansion_notice(expansion_audit)
    if subservice_notice:
        events.append({"type": "system_notice", "content": subservice_notice})
    # Only topics that became seeds count as relevance evidence; one the scope or the
    # catalog turned away must not widen the gate.
    subtopic_terms = [t for plan in service_plans for t in plan.expansions_added]
    seed_roots, seed_targets = flatten_service_plans(service_plans)

    pool_limits = resolve_keyword_pool_limits(
        pool_target=pool_target,
        # The union is no longer pre-capped; sizing still assumes the capped count.
        seed_count=min(len(seed_roots), max_seed_roots),
        settings=get_settings(),
    )
    top_service = top_priority_service_id(prioritization)
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Keyword pool target: {pool_target} "
                f"(roots≤{max_seed_roots}, "
                f"seeds≤{pool_limits['max_seeds']}, "
                f"{pool_limits['limit_per_mode']}/mode, "
                f"{pool_limits['keyword_relevance_max_per_seed']}/seed after relevance)."
                + (f" #1 priority service: {top_service}." if top_service else "")
            ),
        }
    )

    def _build_ctx(ctx_seeds: list[str]):
        """The relevance context for one set of seeds (a single service's, or the union)."""
        if is_prioritization_confirmed(prioritization) and services:
            scoped_pages = [
                t
                for t in page_terms_from_website(website)
                if term_matches_selected_services(t, services)
            ]
            scoped_themes = [
                t
                for t in theme_terms_from_website(website)
                if term_matches_selected_services(t, services)
            ]
            return build_relevance_context(
                services=services,
                cdd_keywords=cdd_keywords,
                page_terms=scoped_pages,
                themes=scoped_themes,
                seeds=ctx_seeds,
                competitor_names=competitor_labels,
                competitor_domains=competitor_domains,
                brand_name=client.display_name,
                domain=domain or None,
                industry=client.industry,
                geography=str(geo.get("geographic_focus") or "") or None,
                service_synonyms=synonym_terms,
                service_subtopics=subtopic_terms,
            )
        return build_relevance_context(
            services=services,
            cdd_keywords=cdd_keywords,
            website=website,
            seeds=ctx_seeds,
            competitor_names=competitor_labels,
            competitor_domains=competitor_domains,
            brand_name=client.display_name,
            domain=domain or None,
            industry=client.industry,
            geography=str(geo.get("geographic_focus") or "") or None,
            service_synonyms=synonym_terms,
            service_subtopics=subtopic_terms,
        )

    # The union context only answers "is there any business evidence at all?".
    relevance_ctx = _build_ctx(seed_roots)

    # Nothing on file to judge relevance against. Expanding here would spend
    # provider budget on a keyword set no gate can check, so stop before the
    # spend and send the operator back to fill the profile in.
    if not relevance_ctx.has_business_evidence:
        log.warning("search_demand_blocked_no_business_evidence", client_id=str(client.id))
        profile.search_demand_status = "blocked_no_business_evidence"
        # The job row was opened before the profile could be checked; close it
        # rather than leaving a run stuck in "running" forever.
        job.status = "failed"
        job.error_detail = "no_business_evidence"
        job.completed_at = datetime.now(timezone.utc)
        events.extend(
            blocked_events(
                "search_demand",
                (
                    "No services, CDD keywords or website themes are on file for "
                    f"{client.display_name}, so no keyword can be checked for relevance. "
                    "Complete the Discovery profile (services / business keywords) — "
                    "blank fields are AI-drafted on questionnaire submit — then approve "
                    "Phase 1 and run keyword research again."
                ),
                route_to="discovery_agent",
            )
        )
        events.append(
            {
                "type": "phase_status",
                "payload": {"search_demand_status": profile.search_demand_status},
            }
        )
        return events

    # Provider calls that need neither the seeded keywords nor the relevance gate
    # start now and run alongside them: the competitor-page harvest, the client's
    # own rankings, competitor keywords, the keyword gap and competitor service
    # trees. Each result is still collected and processed at its original place
    # below, so the report is the same; only the waiting overlaps.
    min_volume = int(get_settings().keyword_expand_min_volume or 10)
    prefetch = _Prefetch()
    if harvest_seeds is not None:
        prefetch.start(
            "harvest",
            _harvest_competitor_page_keywords(
                client=client,
                profile=profile,
                commercial=commercial,
                services=services,
                cdd_keywords=cdd_keywords,
                website=website,
                competitors=competitors,
                competitor_labels=competitor_labels,
                competitor_domains=competitor_domains,
                seeds=harvest_seeds,
                location_code=labs_location_code,
            ),
        )
    if domain:
        prefetch.start(
            "ranked_keywords",
            dataforseo.ranked_keywords(
                domain,
                location_code=labs_location_code,
                language_code=language_code,
                limit=50,
                min_volume=min_volume,
            ),
        )
        prefetch.start(
            "keywords_for_site",
            dataforseo.keywords_for_site(
                domain,
                location_code=labs_location_code,
                language_code=language_code,
                limit=50,
                min_volume=min_volume,
            ),
        )
    prefetch.start(
        "competitor_keywords",
        collect_competitor_keywords(
            client_domain=domain,
            competitor_domains=competitor_domains,
            location_code=labs_location_code,
            language_code=language_code,
            min_volume=min_volume,
            limit=40,
        ),
    )
    if competitor_domains:
        prefetch.start(
            "keyword_gap",
            dataforseo.keyword_gap(
                domain or competitor_domains[0],
                competitor_domains,
                location_code=labs_location_code,
                language_code=language_code,
            ),
        )
    if is_prioritization_confirmed(prioritization) and not prioritization.get("competitor_trees"):
        prefetch.start(
            "competitor_trees",
            discover_competitor_service_trees(competitors, max_competitors=8, max_urls_each=20),
        )

    # Keywords are fetched per service, from that service's own seeds only. The seed
    # budget is split across services (every service gets a share), each service is
    # judged against a relevance context built from ITS seeds, and one service failing
    # does not stop the others. Three services expand at once; the shared DataForSEO
    # gate keeps every concurrent call under the account's simultaneous-request limit.
    seeding = await run_service_scoped_seeding(
        service_plans,
        fetch=run_multi_mode_seeding,
        total_cap=min(max_seed_roots, pool_limits["max_seeds"]),
        relevance_context_for=lambda plan: _build_ctx(plan.seeds),
        service_concurrency=3,
        country=country,
        min_volume=min_volume,
        limit_per_mode=pool_limits["limit_per_mode"],
        location_code=labs_location_code,
        language_code=language_code,
        # Question/research variants per seed widen each pull toward informational
        # keywords; one resolver per run so every service shares its cache.
        query_variants=QueryVariantResolver(
            industry=client.industry, market=seed_market, language=language_code
        ),
    )
    seeding["seed_synonyms"] = synonym_audit
    seeding["subservice_expansion"] = expansion_audit
    _mark("expansion")

    # Related-keyword lookups need only the seeds each service actually fetched, so
    # they start now and run alongside the relevance gate below.
    _dfs_sem = asyncio.Semaphore(4)

    async def _related(service: str, seed: str) -> tuple[list[dict[str, Any]], list[str]]:
        async with _dfs_sem:
            rows, errs = await dataforseo.related_keywords(
                clean_provider_seed(seed),
                limit=35,
                location_code=labs_location_code,
                language_code=language_code,
            )
            for row in rows:
                if isinstance(row, dict):
                    row["service"] = service
                    row["seed"] = seed
            return rows, errs

    related_pairs = round_robin_seeds(
        extra_seeds_last(seeding.get("service_runs") or [], service_plans), max_seed_roots
    ) or [("", r) for r in seed_roots[:max_seed_roots]]
    if related_pairs:
        prefetch.start(
            "related",
            asyncio.gather(
                *[_related(svc, s) for svc, s in related_pairs],
                return_exceptions=True,
            ),
        )
    events.append(
        {
            "type": "system_notice",
            "content": "Seeded per service: "
            + "; ".join(
                f"{r['service']} {r['seed_count']} seed(s), {r['keyword_count']} keyword(s)"
                + (
                    f" ({(r.get('volume_coverage') or {}).get('unverified')} without measured volume)"
                    if (r.get("volume_coverage") or {}).get("unverified")
                    else ""
                )
                if r["status"] == "ok"
                else f"{r['service']} {r['status'].replace('_', ' ')}"
                for r in seeding.get("service_runs") or []
            )
            + ".",
        }
    )
    provider_errors.extend(seeding.get("provider_errors") or [])
    seed_clusters = list(seeding.get("seed_clusters") or [])
    keyword_dataset = list(seeding.get("keyword_dataset") or [])
    cleaning_audits = [seeding.get("keyword_cleaning")]
    # Full rejection record for this run — persisted at the end so the gate can
    # be tuned against real drops instead of a 12-row sample.
    rejected_keywords: list[dict[str, Any]] = list(seeding.get("excluded_keywords") or [])

    # Per-keyword relevance gate on the full seeded dataset, before anything
    # downstream sees it. Every keyword is judged against its own service, the
    # CDD profile and competitor context — or explicitly quarantined.
    from app.services.keyword_validation_gate import (
        stamp_relevance_flags,
        validate_keywords,
    )

    pre_filter_count = len(keyword_dataset)
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Validating {pre_filter_count} seeded keywords against each service, "
                "the client profile and competitor context…"
            ),
        }
    )
    keyword_dataset, relevance_dropped, relevance_audit = await validate_keywords(
        keyword_dataset,
        client_id=str(client.id),
        client_name=client.display_name,
        primary_url=str(client.primary_url or ""),
        industry=client.industry,
        services=services,
        cdd_keywords=cdd_keywords,
        geography=str(geo.get("geographic_focus") or geo.get("location_name") or "") or None,
        prioritization=prioritization,
        competitor_names=competitor_labels,
        competitor_domains=competitor_domains,
        deadline=phase_deadline,
    )
    _mark("relevance_gate")
    # The LLM stage returns its own drops with a different reason key; normalize
    # them into the same rejection record (read-only on that module).
    for row in relevance_dropped or []:
        if not isinstance(row, dict):
            continue
        rejected_keywords.append(
            {
                "keyword": row.get("keyword"),
                "reason": row.get("relevance_drop_reason") or "llm_not_relevant",
                "seed": row.get("seed"),
                "source": (
                    "service_contract"
                    if row.get("classifier_version")
                    else "llm_relevance"
                ),
                "classifier_version": row.get("classifier_version"),
                "match_class": row.get("match_class"),
                "volume": row.get("volume"),
            }
        )
    _relevant_kw_set = {_norm_kw(r.get("keyword")) for r in keyword_dataset if r.get("keyword")}
    seed_clusters = filter_seed_clusters(seed_clusters, _relevant_kw_set)
    # flatten_dataset() rebuilds every row from seed_clusters, so the gate's
    # verdicts have to be written onto the cluster rows or they vanish here —
    # and the cluster rows are what downstream enforcement actually reads.
    seed_clusters = stamp_relevance_flags(seed_clusters, keyword_dataset)
    seed_clusters, seed_identification = await identify_multiple_seed_clusters(
        seed_clusters,
        services=services,
        brand_name=client.display_name,
        industry=client.industry,
        geography=str(geo.get("geographic_focus") or geo.get("location_name") or "") or None,
        deadline=phase_deadline,
    )
    keyword_dataset = flatten_dataset(seed_clusters)
    seeding = {
        **seeding,
        "seed_clusters": seed_clusters,
        "keyword_dataset": keyword_dataset,
        "keyword_count": len(keyword_dataset),
        "class_counts": {
            cls: sum(len(c.get(cls) or []) for c in seed_clusters)
            for cls in ("exact", "phrase", "related", "broad")
        },
        "seeds_with_all_classes": sum(
            1
            for c in seed_clusters
            if all(c.get(cls) for cls in ("exact", "phrase", "related", "broad"))
        ),
        "seed_identification": seed_identification,
    }
    _unjudged = int(relevance_audit.get("quarantined", 0))
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Relevance gate: kept {len(keyword_dataset)} of {pre_filter_count} "
                f"({relevance_audit.get('dropped', 0)} dropped, "
                f"{relevance_audit.get('hard_rule_dropped', 0)} by service rules, "
                f"{relevance_audit.get('api_calls', 0)} LLM calls, "
                f"{relevance_audit.get('cached', 0)} from cache, "
                f"classifier {relevance_audit.get('classifier_version') or 'llm_per_keyword_v2'})."
                + (
                    f" {_unjudged} keyword(s) could NOT be verified "
                    f"({relevance_audit.get('quarantined_by_cause')}) — kept for "
                    "reporting but excluded from clustering, topics, briefs and "
                    "commercial targeting."
                    if _unjudged
                    else ""
                )
            ),
        }
    )
    if seed_identification.get("source") != "unchanged":
        identified = [
            f"{row['service']} ({len(row.get('seeds') or [])})"
            for row in (seed_identification.get("services") or [])
            if isinstance(row, dict)
        ]
        via = (
            "the model"
            if seed_identification.get("source") == "model"
            else "keyword themes"
        )
        events.append(
            {
                "type": "system_notice",
                "content": (
                    "Seed identification grouped keywords into "
                    f"{seed_identification.get('seed_count_after')} seeds "
                    f"({seed_identification.get('seed_count_before')} before, "
                    f"via {via})"
                    + (f": {', '.join(identified)}." if identified else ".")
                ),
            }
        )
    # Everything downstream works from the verified subset. The full dataset
    # (including quarantined rows) is still reported, so nothing is hidden.
    verified_keywords = verified_keywords_only(keyword_dataset)
    if _unjudged and _unjudged / max(pre_filter_count, 1) > 0.25:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Warning: {_unjudged / max(pre_filter_count, 1):.0%} of keywords could "
                    "not be relevance-checked. This Phase 5 output is materially "
                    "degraded — check the LLM provider before relying on it."
                ),
            }
        )
    if seeding.get("provider_expansion_unavailable"):
        events.append(
            {
                "type": "system_notice",
                "content": (
                    "WARNING: DataForSEO Labs could not expand keywords on this "
                    "account (plan or billing). The keyword pool below is short "
                    "because the provider failed, not because demand is thin — "
                    "nothing was substituted to fill the gap. Re-run Phase 5 once "
                    "the provider is restored before acting on these results."
                ),
            }
        )

    if keyword_dataset or seed_clusters:
        if "dataforseo" not in providers_used:
            providers_used.append("dataforseo")
        for row in verified_keywords:
            if is_stale_year_keyword(str(row.get("keyword") or "")):
                continue
            ahrefs_rows.append(
                {
                    "keyword": row.get("keyword"),
                    "volume": row.get("volume"),
                    "difficulty": row.get("difficulty"),
                    "traffic_potential": row.get("traffic_potential"),
                    "cpc": row.get("cpc"),
                    "intent": row.get("intent"),
                    "funnel": row.get("funnel"),
                    # Keywords kept without measured volume stay flagged downstream.
                    "volume_status": row.get("volume_status"),
                    "volume_unverified": bool(row.get("volume_unverified")),
                    "query_variant": bool(row.get("query_variant")),
                    "informational_topup": bool(row.get("informational_topup")),
                    "parent_topic": row.get("parent_topic"),
                    "source": row.get("source") or "dataforseo",
                    "match_class": row.get("match_class"),
                    "seed": row.get("seed"),
                    "target": row.get("target"),
                    "target_type": row.get("target_type"),
                    "ahrefs_endpoint": row.get("ahrefs_endpoint"),
                }
            )
    _tt = seeding.get("target_type_counts") or {}
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Keyword seeding: {seeding.get('keyword_count', 0)} keywords "
                f"(exact={seeding.get('class_counts', {}).get('exact', 0)}, "
                f"phrase={seeding.get('class_counts', {}).get('phrase', 0)}, "
                f"related={seeding.get('class_counts', {}).get('related', 0)}, "
                f"broad={seeding.get('class_counts', {}).get('broad', 0)}) "
                f"across {len(seed_clusters)} target root(s) "
                f"(services={_tt.get('service', 0)}, pages={_tt.get('page', 0)}, "
                f"keywords={_tt.get('keyword', 0)}); volume > {seeding.get('min_volume', 10)}. "
                f"All 4 classes filled for "
                f"{seeding.get('seeds_with_all_classes', 0)}/{len(seed_clusters)} seeds."
            ),
        }
    )

    def _assign_rows_to_services(
        rows: list[dict[str, Any]], source_label: str
    ) -> list[dict[str, Any]]:
        """Keep each Labs row on the service it matches, then apply that service's relevance gate."""
        grouped = partition_keywords_by_service(rows, service_plans)
        kept_all: list[dict[str, Any]] = []
        grouped_norms = {
            _norm_kw(row.get("keyword"))
            for bucket in grouped.values()
            for row in bucket
            if isinstance(row, dict)
        }
        for plan in service_plans:
            bucket = grouped.get(plan.service_id) or []
            if not bucket:
                continue
            kept, excl, audit = filter_relevant_keywords(
                bucket, _build_ctx(plan.seeds), source_label=source_label
            )
            cleaning_audits.append(audit)
            rejected_keywords.extend(excl)
            kept_all.extend(kept)
        for row in rows:
            if not isinstance(row, dict) or not row.get("keyword"):
                continue
            if _norm_kw(row.get("keyword")) not in grouped_norms:
                rejected_keywords.append(
                    {**row, "rejection_reason": "irrelevant_to_service", "source": source_label}
                )
        return kept_all

    # Client + competitor organic keywords (gap signals) via DataForSEO Labs
    # ranked-keywords — this deployment has no SEMrush subscription.
    # The seeded set already has volumes. This second pass is skipped when
    # the turn is nearly out of time so clustering and the report save still run.
    extra_fetch = _seconds_left() > 240
    if not extra_fetch:
        log.info(
            "keyword_extra_fetch_skipped",
            seconds_left=round(_seconds_left(), 1),
        )
        events.append(
            {
                "type": "system_notice",
                "content": (
                    "Saving the seeded keyword report without a second provider "
                    "pass so this run finishes inside the time limit."
                ),
            }
        )
    if extra_fetch and domain:
        # Both were started before expansion; this only collects them.
        org, e3 = await prefetch.result("ranked_keywords")
        site_kws, e3b = await prefetch.result("keywords_for_site")
        provider_errors.extend(e3)
        provider_errors.extend(e3b)
        for row in org:
            if isinstance(row, dict):
                row["sources"] = ["ranked_keywords"]
        for row in site_kws:
            if isinstance(row, dict):
                row["sources"] = ["keywords_for_site"]
        org = list(org) + list(site_kws)
        client_kw_map = {
            str(r.get("keyword") or "").lower(): r.get("position")
            for r in org
            if isinstance(r, dict) and r.get("keyword")
        }
        pending_org: list[dict[str, Any]] = []
        for r in org:
            if is_stale_year_keyword(str(r.get("keyword") or "")):
                continue
            r["client_position"] = r.get("position")
            pending_org.append(r)
        kept = _assign_rows_to_services(pending_org, "dataforseo_organic")
        ahrefs_rows.extend(kept)
    else:
        prefetch.discard("ranked_keywords")
        prefetch.discard("keywords_for_site")
        client_kw_map = {}

    # Competitor research is fetched once per domain, then each row is kept
    # only for the service it describes. A solar keyword never lands on roofing.
    # The fetch was started before expansion; this collects it.
    if extra_fetch and _seconds_left() > 200:
        try:
            comp_rows, comp_errs = await prefetch.result(
                "competitor_keywords", timeout=max(20.0, min(90.0, _seconds_left() - 200))
            )
        except asyncio.TimeoutError:
            log.warning(
                "competitor_keywords_cut_short",
                seconds_left=round(_seconds_left(), 1),
            )
            comp_rows, comp_errs = [], ["competitor_keywords_cut_short"]
    else:
        prefetch.discard("competitor_keywords")
        comp_rows, comp_errs = [], []
    provider_errors.extend(comp_errs)
    pending_comp: list[dict[str, Any]] = []
    for row in comp_rows:
        kw = str(row.get("keyword") or "")
        if not kw or is_stale_year_keyword(kw):
            continue
        row["gap_flag"] = kw.lower() not in client_kw_map
        endpoint = str(row.get("dataforseo_endpoint") or "ranked_keywords")
        row["sources"] = [endpoint]
        pending_comp.append(row)
    if pending_comp and "dataforseo" not in providers_used:
        providers_used.append("dataforseo")
    ahrefs_rows.extend(_assign_rows_to_services(pending_comp, "dataforseo_competitor"))

    # Collect the competitor-page harvest that ran alongside expansion.
    if prefetch.has("harvest"):
        try:
            page_intel = await prefetch.result("harvest")
            _store_competitor_page_intel(profile, commercial, page_intel)
            fresh = scope_competitor_keywords_to_services(
                list(page_intel.get("keywords") or []), services
            )
            if fresh:
                competitor_page_keywords = fresh
                competitor_sites = list(page_intel.get("snapshots") or [])
                harvest_stats = dict(page_intel.get("stats") or {})
                events.append(
                    {
                        "type": "system_notice",
                        "content": (
                            f"Competitor harvest: {harvest_stats.get('pages', 0)} pages, "
                            f"{len(competitor_page_keywords)} volume-qualified keyword(s) "
                            "for selected services."
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001 — harvest must not abort keywords
            log.warning(
                "competitor_page_reharvest_failed",
                client_id=str(client.id),
                error=str(exc)[:200],
            )
            events.append(
                {
                    "type": "system_notice",
                    "content": (
                        "Competitor page harvest failed — falling back to "
                        "organic keyword gap feeds for competitor signal."
                    ),
                }
            )

    # The harvest ran before the client's own rankings were known, so every page
    # keyword was provisionally a gap. Correct that now.
    if competitor_page_keywords:
        competitor_page_keywords = _assign_rows_to_services(
            refresh_gap_flags(competitor_page_keywords, client_kw_map),
            "competitor_page",
        )
        ahrefs_rows.extend(competitor_page_keywords)
    else:
        # Fallback path: no page harvest, so competitor organic gap rows become
        # the competitor signal that reaches clustering.
        competitor_page_keywords = [
            row
            for row in ahrefs_rows
            if isinstance(row, dict)
            and row.get("competitor_domain")
            and row.get("gap_flag")
        ][:40]

    # DataForSEO related + volume + KD + gap (location from geographic_focus).
    # The related lookups were started right after expansion (see `_related`).
    if extra_fetch and _seconds_left() > 200 and prefetch.has("related"):
        try:
            related_results = await prefetch.result(
                "related", timeout=max(20.0, min(90.0, _seconds_left() - 200))
            )
        except asyncio.TimeoutError:
            log.warning("keyword_related_cut_short", seconds_left=round(_seconds_left(), 1))
            related_results = []
    else:
        prefetch.discard("related")
        related_results = []
    for res in related_results:
        if isinstance(res, BaseException):
            provider_errors.append(f"dataforseo_related_error:{type(res).__name__}")
            continue
        related, e5 = res
        provider_errors.extend(e5)
        if related:
            if "dataforseo" not in providers_used:
                providers_used.append("dataforseo")
            pending_related = [
                row
                for row in related
                if not is_stale_year_keyword(str(row.get("keyword") or ""))
            ]
            dfs_rows.extend(_assign_rows_to_services(pending_related, "dataforseo_related"))

    all_kw_names = list(
        {
            *(clean_provider_seed(str(r.get("keyword") or "")) for r in ahrefs_rows if r.get("keyword")),
            *(clean_provider_seed(str(r.get("keyword") or "")) for r in dfs_rows if r.get("keyword")),
            # Only seeds that were actually fetched (each service's capped share).
            *(
                clean_provider_seed(s)
                for s in (seeding.get("seeds") or seed_roots)
                if relevance_ctx.seed_is_supported(s)
            ),
        }
    )
    # Prefer specific multi-word names for volume lookup.
    # No slice here: the endpoints take 1000 keywords per task and batch, so the
    # whole pool gets measured. Truncating to [:60] / [:30] left the rest of the
    # pool with no provider metrics at all.
    all_kw_names = sorted(
        [k for k in all_kw_names if k and not is_stale_year_keyword(str(k))],
        key=lambda k: (-len(str(k).split()), str(k).lower()),
    )
    if extra_fetch and _seconds_left() > 200 and all_kw_names:
        try:
            volumes, kds, e6 = await asyncio.wait_for(
                measured_rows_for_pool(
                    all_kw_names,
                    location_code=location_code,
                    language_code=language_code,
                    labs_location_code=labs_location_code,
                ),
                timeout=max(20.0, min(90.0, _seconds_left() - 180)),
            )
        except asyncio.TimeoutError:
            log.warning("keyword_metrics_cut_short", seconds_left=round(_seconds_left(), 1))
            volumes, kds, e6 = [], [], ["keyword_metrics_cut_short"]
    else:
        volumes, kds, e6 = [], [], []
    provider_errors.extend(e6)
    if volumes:
        if "dataforseo" not in providers_used:
            providers_used.append("dataforseo")
        kept, _excl, audit = filter_relevant_keywords(
            volumes, relevance_ctx, source_label="dataforseo_volume"
        )
        cleaning_audits.append(audit)
        rejected_keywords.extend(_excl)
        dfs_rows.extend(kept)
    kept_kds, _excl, kd_audit = filter_relevant_keywords(
        kds, relevance_ctx, source_label="dataforseo_kd"
    )
    cleaning_audits.append(kd_audit)
    rejected_keywords.extend(_excl)
    dfs_rows.extend(kept_kds)

    if extra_fetch and competitor_domains and _seconds_left() > 180:
        # Started before expansion; this collects it.
        gap_rows, e8 = await prefetch.result("keyword_gap")
        provider_errors.extend(e8)
        if gap_rows:
            if "dataforseo" not in providers_used:
                providers_used.append("dataforseo")
            pending_gap = []
            for row in gap_rows:
                if is_stale_year_keyword(str(row.get("keyword") or "")):
                    continue
                row["gap_flag"] = True
                pending_gap.append(row)
            dfs_rows.extend(_assign_rows_to_services(pending_gap, "dataforseo_gap"))

    _mark("provider_enrichment")
    # Phase 3 stores the pull under backlink_summary.summary. A homepage
    # estimate is labelled and is not used as a Domain Rating in ranking.
    authority, authority_source = _authority_for_ranking(website, competitive)

    keyword_cleaning = compact_cleaning_audit(merge_cleaning_audits(*cleaning_audits)) or {}

    merged = merge_keyword_metrics(ahrefs_rows, dfs_rows)

    # Phase 5 design: the multi-mode keyword seeding dataset (vol > 10) is the
    # shared keyword pool for all downstream buckets (evergreen/trend/avoid,
    # topic selection, clustering). Ahrefs organic pulls are treated as gap
    # signals only, so we filter merged back to seeded keywords here.
    merged_before_seed_filter = len(merged)
    seed_kw_set = {
        _norm_kw(r.get("keyword"))
        for r in keyword_dataset
        if isinstance(r, dict) and r.get("keyword")
    }
    if seed_kw_set:
        # Competitor page targets are evidence of intent, not just a gap signal —
        # they stay in the pool even where the client already ranks.
        merged = filter_merged_to_seed_pool(
            merged,
            seed_kw_set=seed_kw_set,
            competitor_kw_set={
                _norm_kw(r.get("keyword")) for r in competitor_page_keywords
            },
        )
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Phase 5 seed pool filter: kept {len(merged)} of "
                    f"{merged_before_seed_filter} merged keywords "
                    f"(seed pool + competitor gap signals)."
                ),
            }
        )

    keyword_dataset = [stamp_keyword_intent(r) for r in keyword_dataset]
    intent_bal = intent_balance(keyword_dataset)
    intent_warnings = intent_balance_warnings(intent_bal["counts"])

    ranked = rank_opportunities(
        merged,
        seeds=seeds,
        products=products,
        client_authority=float(authority) if authority is not None else None,
        client_authority_source=authority_source,
        relevance_context=relevance_ctx,
    )

    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Multi-mode seed set: {len(keyword_dataset)} keywords across "
                f"{len(seed_clusters)} seeds (shared by Topic Plan + clusters)."
            ),
        }
    )

    # SERP titles for top seeded keywords + competitor-gap evergreen rows
    events.append(
        {
            "type": "system_notice",
            "content": "Checking live SERPs for approved competitors on top keyword opportunities…",
        }
    )
    serp_by_kw: dict[str, list[dict[str, Any]]] = {}
    serp_targets: list[dict[str, Any]] = []
    seen_serp: set[str] = set()
    _serp_seed_pool = sorted(
        verified_keywords, key=lambda r: r.get("volume") or 0, reverse=True
    )
    for row in list(_serp_seed_pool[:8]) + [
        r for r in ranked["strong_evergreen"] if r.get("gap_flag")
    ][:4]:
        kw = str(row.get("keyword") or "").strip()
        key = kw.lower()
        if not kw or key in seen_serp:
            continue
        seen_serp.add(key)
        serp_targets.append(row)

    comps_set = {d.lower().removeprefix("www.") for d in competitor_domains}

    # SERP lookups run concurrently — one per target keyword
    _serp_sem = asyncio.Semaphore(12)

    async def _serp(kw: str) -> tuple[list[dict[str, Any]], list[str]]:
        async with _serp_sem:
            return await dataforseo.serp_overview(
                kw, depth=8, location_code=location_code
            )

    if serp_targets and _seconds_left() > 160:
        try:
            serp_results = await asyncio.wait_for(
                asyncio.gather(
                    *[_serp(str(r.get("keyword") or "").strip()) for r in serp_targets],
                    return_exceptions=True,
                ),
                timeout=max(15.0, min(45.0, _seconds_left() - 140)),
            )
        except asyncio.TimeoutError:
            log.warning("keyword_serp_cut_short", seconds_left=round(_seconds_left(), 1))
            serp_results = []
    else:
        serp_results = []

    _mark("serp_check")
    for row, res in zip(serp_targets, serp_results):
        kw = str(row.get("keyword") or "").strip()
        if isinstance(res, BaseException):
            provider_errors.append(f"dataforseo_serp_error:{type(res).__name__}")
            continue
        serp_rows, e_serp = res
        provider_errors.extend(e_serp)
        if not serp_rows:
            continue
        if "dataforseo" not in providers_used:
            providers_used.append("dataforseo")
        # Only surface SERP rows owned by a KNOWN competitor. Authority/publisher
        # domains that merely rank (salesforce.com, brightedge.com, …) are noise
        # against the approved competitor list, so they are dropped from display.
        preferred = []
        for r in serp_rows:
            if not r.get("title"):
                continue
            dom = (r.get("domain") or "").lower().removeprefix("www.")
            if dom and any(dom == c or dom.endswith("." + c) for c in comps_set):
                preferred.append(r)
        titles = [
            {
                "title": str(r.get("title") or "")[:140],
                "domain": r.get("domain"),
                "url": r.get("url"),
                "position": r.get("position"),
            }
            for r in preferred[:5]
        ]
        serp_by_kw[kw.lower()] = titles
        row["serp_titles"] = titles
        # Stamp shared-memory competitors that actually appear in SERP
        if preferred:
            row["competitor_domains"] = sorted(
                {
                    *(row.get("competitor_domains") or []),
                    *[
                        (p.get("domain") or "").lower().removeprefix("www.")
                        for p in preferred
                        if p.get("domain")
                    ],
                }
            )[:5]
            positions = list(row.get("competitor_positions") or [])
            for p in preferred:
                dom = (p.get("domain") or "").lower().removeprefix("www.")
                if not dom:
                    continue
                entry = {"domain": dom, "position": p.get("position")}
                if entry not in positions:
                    positions.append(entry)
            row["competitor_positions"] = positions[:5]
            if not row.get("gap_flag"):
                row["gap_flag"] = True
        for scored in ranked["all_scored"]:
            if str(scored.get("keyword") or "").strip().lower() == kw.lower():
                scored["serp_titles"] = titles
                if preferred:
                    scored["competitor_domains"] = row.get("competitor_domains")
                    scored["competitor_positions"] = row.get("competitor_positions")
                    scored["gap_flag"] = row.get("gap_flag")

    # Phase 5 order: Keyword Research → Cleaning → Topic Creation → Clustering → Intent
    topic_seed = (
        (cdd_keywords[0] if cdd_keywords else None)
        or (services[0] if services else None)
        or (seeds[0] if seeds else None)
        or (client.industry or client.display_name)
    )
    audience_raw = (
        cdd.get("target_demographic")
        or commercial.get("target_demographic")
        or intake.get("target_demographic")
        or marketing.get("target_audience")
    )
    audience = format_audience_label(audience_raw)

    pain_points: list[str] = []
    for src in (cdd, commercial, intake, marketing):
        val = (
            src.get("pain_points")
            or src.get("customer_pain_points")
            or src.get("audience_pain_points")
            or src.get("customer_problems")
        )
        if isinstance(val, str) and val.strip():
            pain_points.extend(p.strip() for p in val.split(";") if p.strip())
        elif isinstance(val, list):
            pain_points.extend(str(p).strip() for p in val if str(p).strip())
    # Also pull structured demographic pain points when present
    if isinstance(audience_raw, dict):
        primary = audience_raw.get("primary")
        if isinstance(primary, dict):
            pain = primary.get("pain_points_fears")
            if isinstance(pain, str) and pain.strip():
                for chunk in pain.replace("|", ";").replace(".", ";").split(";"):
                    if chunk.strip():
                        pain_points.append(chunk.strip())
    seen_pains: set[str] = set()
    pain_points = [
        p for p in pain_points if not (p.lower() in seen_pains or seen_pains.add(p.lower()))
    ][:6]

    # Build the same service groups the Multi-mode UI shows, THEN pick topics
    # one-per-service so Topic Plan cannot drift to volume mega-heads.
    service_clusters = build_service_seed_clusters(seed_clusters, services)
    service_clusters, subservice_competitor_matrix = enrich_service_clusters_with_competitors(
        service_clusters,
        ranked["all_scored"],
        competitive_landscape=competitive,
        competitors=competitors,
    )
    topic_kw_pool = select_topics_from_service_clusters(
        service_clusters,
        products=products or services,
        limit=10,
        max_per_service=1,
    )
    assigned_services = [
        str(r.get("service") or r.get("target") or r.get("seed") or "")
        for r in topic_kw_pool
    ]
    topic_selection_context = {
        "topic_seed": str(topic_seed),
        "audience": audience,
        "topic_kw_pool": topic_kw_pool,
        "pain_points": pain_points,
        "products": products,
        "cdd_keywords": cdd_keywords,
        "industry": client.industry,
        "competitor_context": competitor_context_blob(competitors, competitor_sites, geo),
        "selection_mode": "new_clusters_vs_sitemap",
        "selection_version": "phase5_sitemap_then_topics_v1",
        "assigned_services": assigned_services,
    }

    # Keyword Clustering — intent + funnel on every cluster before sitemap match
    cluster_input = list(verified_keywords)
    seen_cluster = {str(r.get("keyword") or "").lower() for r in cluster_input}
    for ht in ranked.get("head_terms") or []:
        k = str(ht.get("keyword") or "").lower()
        if k and k not in seen_cluster:
            cluster_input.append(ht)
            seen_cluster.add(k)

    # Keywords the competitors target on their own pages cluster alongside the
    # seeded pool — that is the point of harvesting them.
    before_competitor_merge = len(cluster_input)
    cluster_input = merge_competitor_rows_into_cluster_input(
        cluster_input, competitor_page_keywords
    )
    if len(cluster_input) > before_competitor_merge:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Clustering pool: added "
                    f"{len(cluster_input) - before_competitor_merge} competitor keyword(s) "
                    f"to {before_competitor_merge} seeded keyword(s)."
                ),
            }
        )

    cluster_report = await run_keyword_clustering(
        cluster_input,
        seeds=seeds,
        brand_name=client.display_name,
        domain=domain or None,
        products=products,
        relevance_context=relevance_ctx,
        location_code=location_code,
        language_code=language_code,
        use_llm=time.monotonic() < phase_deadline,
        # Primary selection weighs difficulty against what this site can rank
        # for, but only on a measured Domain Rating, never an estimate.
        site_strength=float(authority) if authority is not None and authority_source == "provider" else None,
    )
    _mark("clustering")
    cluster_report["service_clusters"] = service_clusters
    cluster_report["subservice_competitor_matrix"] = subservice_competitor_matrix
    cluster_report["services_clustered"] = len(service_clusters)
    cluster_report["seeds_clustered_by_service"] = sum(
        int(group.get("seed_count") or 0) for group in service_clusters
    )

    # Post-cluster AI coherence gate: each cluster must be a sensible single-page
    # topic for this business. Rejected clusters stay in the report for audit but
    # are excluded from sitemap classification, CDP, and topic drafting.
    from app.services.cluster_validation_gate import validate_clusters_coherence

    raw_clusters = [
        c for c in (cluster_report.get("clusters") or []) if isinstance(c, dict)
    ]
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Validating {len(raw_clusters)} keyword clusters for topic coherence…"
            ),
        }
    )
    kept_clusters, rejected_clusters, cluster_sense_audit = await validate_clusters_coherence(
        raw_clusters,
        client_id=str(client.id),
        client_name=client.display_name,
        primary_url=str(client.primary_url or ""),
        industry=client.industry,
        services=services,
        cdd_keywords=cdd_keywords,
        geography=str(geo.get("geographic_focus") or geo.get("location_name") or "") or None,
        deadline=phase_deadline,
    )
    _mark("cluster_coherence")
    # Keep rejected clusters in the report (stamped) so operators can see why;
    # only coherent clusters continue into topic / CDP paths.
    cluster_report["clusters"] = kept_clusters + rejected_clusters
    cluster_report["clusters_created"] = len(kept_clusters)
    cluster_report["cluster_sense_gate"] = cluster_sense_audit
    cluster_report["rejected_clusters"] = [
        {
            "name": c.get("name"),
            "primary_keyword": c.get("primary_keyword"),
            "verdict": c.get("cluster_sense_verdict"),
            "reason": c.get("cluster_sense_reason"),
            "cause": c.get("cluster_sense_cause"),
            "keyword_count": c.get("keyword_count") or len(c.get("keywords") or []),
        }
        for c in rejected_clusters
    ]
    if rejected_clusters:
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Cluster coherence: kept {len(kept_clusters)}, "
                    f"rejected {len(rejected_clusters)} "
                    f"(drop/quarantine — not used for topics)."
                ),
            }
        )

    # Phase 3 sitemap is the inventory. Only recrawl if it is empty, and cap that
    # recrawl so keyword research cannot spend the whole turn fetching HTML.
    sm_pages = sitemap_pages(website)
    live_scan: dict[str, Any] = {
        "pages": sm_pages,
        "page_count": len(sm_pages),
        "source_counts": {"sitemap": len(sm_pages)},
        "scanned_at": None,
        "source": "phase3_sitemap",
    }
    if not sm_pages:
        try:
            live_scan = await asyncio.wait_for(
                scan_live_site(
                    client.primary_url,
                    seed_urls=sitemap_urls(website),
                    max_pages=12,
                    use_playwright=False,
                    discover_more=False,
                ),
                timeout=45,
            )
        except asyncio.TimeoutError:
            log.warning("live_site_scan_timeout", client_id=str(client.id))
            live_scan = {"pages": [], "page_count": 0, "source_counts": {}, "scanned_at": None}
        except Exception as exc:  # noqa: BLE001
            log.warning("live_site_scan_failed", client_id=str(client.id), error=str(exc))
            live_scan = {"pages": [], "page_count": 0, "source_counts": {}, "scanned_at": None}

    sitemap_classification = classify_clusters_against_sitemap(
        kept_clusters,
        sitemap_pages=sm_pages or list(live_scan.get("pages") or []),
        live_pages=list(live_scan.get("pages") or []),
        # A capped inventory means "no page covers this" was only checked
        # against part of the site — the new-topic calls get flagged, not trusted.
        inventory_complete=not sitemap_is_truncated(website),
    )
    cluster_report["sitemap_classification"] = sitemap_classification

    clusters = clusters_for_cdp(cluster_report)

    # Draft topics only for clusters that do not already exist on the sitemap
    if _seconds_left() > 150:
        try:
            topic_plan = await asyncio.wait_for(
                run_create_topic_for_new_clusters(
                    message=message,
                    industry=client.industry,
                    audience=audience,
                    topic_seed=str(topic_seed),
                    topic_kw_pool=topic_kw_pool,
                    keyword_dataset=verified_keywords,
                    clusters=kept_clusters,
                    competitor_domains=competitor_domains,
                    competitor_names=competitor_labels,
                    geographic_focus=str(geo.get("geographic_focus") or ""),
                    location_name=str(geo.get("location_name") or ""),
                    competitor_context=competitor_context_blob(
                        competitors, competitor_sites, geo
                    ),
                    products=products,
                    cdd_keywords=cdd_keywords,
                    pain_points=pain_points,
                    serp_by_kw=serp_by_kw,
                ),
                timeout=max(30.0, min(120.0, _seconds_left() - 90)),
            )
        except asyncio.TimeoutError:
            log.warning("create_topic_cut_short", seconds_left=round(_seconds_left(), 1))
            topic_plan = {}
    else:
        log.info("create_topic_skipped", seconds_left=round(_seconds_left(), 1))
        topic_plan = {}
    _mark("topic_plan")
    topic_plan["sitemap_classification"] = {
        "existing_topic_count": sitemap_classification.get("existing_topic_count"),
        "existing_review_count": sitemap_classification.get("existing_review_count"),
        "new_topic_count": sitemap_classification.get("new_topic_count"),
        "pages_used": sitemap_classification.get("pages_used"),
    }

    topics = topics_from_plan(topic_plan)
    if not topics:
        topics = build_topics(
            [c for c in clusters if isinstance(c, dict) and c.get("topic_disposition") == "new_topic"]
            or clusters,
            keyword_dataset,
        )

    def _detail_row(r: dict[str, Any]) -> dict[str, Any]:
        kw = str(r.get("keyword") or "")
        intent = detect_intent(kw, r.get("intent"))
        return {
            "keyword": r.get("keyword"),
            "volume": r.get("volume"),
            "volume_ahrefs": r.get("volume_ahrefs"),
            "volume_dataforseo": r.get("volume_dataforseo"),
            "difficulty": r.get("difficulty"),
            "cpc": r.get("cpc"),
            "intent": intent,
            "funnel": r.get("funnel") or detect_funnel(kw, intent),
            "trend": r.get("trend"),
            "opportunity_score": r.get("opportunity_score"),
            "bucket": r.get("bucket"),
            "gap_flag": r.get("gap_flag"),
            "client_position": r.get("client_position"),
            "competitor_domains": r.get("competitor_domains") or [],
            "competitor_positions": r.get("competitor_positions") or [],
            "serp_titles": r.get("serp_titles") or serp_by_kw.get(str(r.get("keyword") or "").lower(), []),
            "traffic_potential": r.get("traffic_potential"),
            "rationale": r.get("rationale"),
            "parent_topic": r.get("parent_topic"),
            "specificity": r.get("specificity"),
            "business_fit": r.get("business_fit"),
            "gap_score": r.get("gap_score"),
            "is_broad_head": r.get("is_broad_head"),
            "match_class": r.get("match_class"),
            "seed": r.get("seed"),
            "target": r.get("target"),
            "target_type": r.get("target_type"),
        }

    keyword_table = [_detail_row(r) for r in keyword_dataset[:15]]
    evergreen_table = [_detail_row(r) for r in ranked["strong_evergreen"][:10]]

    from app.services.phase_pipeline import enrich_phase5_pack

    serp_for_intent = {
        kw: {
            "validated": bool(rows),
            "organic": [
                {
                    "title": r.get("title"),
                    "domain": r.get("domain"),
                    "url": r.get("url"),
                    "position": r.get("position"),
                }
                for r in rows
                if isinstance(r, dict)
            ],
        }
        for kw, rows in serp_by_kw.items()
    }

    summary = enrich_phase5_pack(
        {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed_keywords": seeds,
        "cdd_keywords": cdd_keywords,
        "products": products,
        "services": services,
        "service_catalog": service_catalog,
        "service_prioritization": prioritization,
        "country": country,
        "geographic_focus": geo.get("geographic_focus") or "",
        "location_code": location_code,
        "labs_location_code": labs_location_code,
        "location_name": geo.get("location_name"),
        "location_type": geo.get("location_type"),
        "geo_note": geo.get("note"),
        "providers_used": providers_used or ["none"],
        "provider_errors": sorted(set(provider_errors)),
        "competitors": competitors,
        "competitor_domains": competitor_domains,
        "competitor_names": competitor_labels,
        "competitor_sites": competitor_sites,
        "competitor_title_seeds": competitor_title_seeds[:10],
        "competitor_page_keywords": competitor_page_keywords[:40],
        "competitor_page_stats": page_intel.get("stats") or {},
        "top_priority_service": top_service,
        "subservice_competitor_matrix": subservice_competitor_matrix,
        "topic_plan": topic_plan,
        "topic_selection_context": topic_selection_context,
        "sitemap_classification": sitemap_classification,
        "topics": topics,
        "cluster_report": cluster_report,
        "clusters": clusters,
        "live_site_scan": live_scan,
        "keyword_seeding": {
            "min_volume": seeding.get("min_volume", 10),
            "class_counts": seeding.get("class_counts") or {},
            "target_type_counts": seeding.get("target_type_counts") or {},
            "note": seeding.get("note"),
            "seed_count": len(seed_clusters),
            "seed_identification": seeding.get("seed_identification") or {},
            # Per service: synonyms the model suggested, which became seeds (tagged
            # seed_origin="synonym"), which were skipped and why, or why none came.
            "seed_synonyms": seeding.get("seed_synonyms") or [],
            # Per service: sub-service topics the model suggested, which became seeds
            # (target_type="sub_service", seed_origin="subservice_expansion"), which were
            # skipped and why (duplicate, in_catalog, excluded_by_scope, ...).
            "subservice_expansion": seeding.get("subservice_expansion") or [],
            # measured / estimated / unverified volume, funnel mix, and per seed the
            # informational share before and after the variant pull and top-up.
            "volume_coverage": seeding.get("volume_coverage") or {},
            "funnel_counts": seeding.get("funnel_counts") or {},
            "funnel_coverage": seeding.get("funnel_coverage") or [],
            "informational_topups": seeding.get("informational_topups") or 0,
            "seeded_keyword_count": seeding.get("keyword_count") or len(keyword_dataset),
            "seeds_with_all_classes": seeding.get("seeds_with_all_classes", 0),
            "class_coverage": seeding.get("class_coverage") or [],
            "providers": seeding.get("providers") or [],
        },
        "keyword_cleaning": keyword_cleaning,
        # Makes fabricated-volume rows legible to every consumer (card,
        # PDF, workbook). The flag existed on the rows but was read nowhere,
        # so estimated volumes rendered exactly like measured ones.
        "keyword_data_quality": summarize_keyword_data_quality(keyword_dataset),
        "relevance_audit": relevance_audit,
        "relevance_dropped_count": len(relevance_dropped),
        "intent_balance": {
            **intent_bal,
            "warnings": intent_warnings,
        },
        "seed_clusters": seed_clusters,
        "keyword_dataset": keyword_dataset[:500],
        "best_opportunities": [_detail_row(r) for r in ranked["best_opportunities"][:12]],
        "strong_evergreen": evergreen_table,
        "head_terms": [_detail_row(r) for r in (ranked.get("head_terms") or [])[:10]],
        "trend_plays": [_detail_row(r) for r in ranked["trend_plays"][:12]],
        "avoid": [_detail_row(r) for r in ranked["avoid"][:10]],
        "keyword_table": keyword_table,
        "keyword_count": len(merged),
        "keyword_pool_target": pool_target,
        "keyword_pool_limits": pool_limits,
        "keyword_pool_actual": len(keyword_dataset),
        "skills_used": ["keyword_clustering", "keyword_seeding", "create_topic", "seo-dataforseo"],
        "note": (
            "Sitemap inventory first, then multi-mode seeding, clustering with "
            "intent/funnel, classify clusters against the sitemap (existing vs new), "
            "and draft topics only for new clusters. URL mapping runs next in Phase 6. "
            "Past years demoted; "
            f"present year preferred. "
            "Generic head terms kept in clusters. "
            f"Competitors locked to shared-memory set ({len(competitors)}). "
            f"Geo locked to {geo.get('location_name')}."
            + (
                f" Sitemap classify: {sitemap_classification.get('existing_topic_count', 0)} existing, "
                f"{sitemap_classification.get('existing_review_count', 0)} review, "
                f"{sitemap_classification.get('new_topic_count', 0)} new."
            )
            + (
                f" Cleaned to CDD/services/pages: kept {keyword_cleaning.get('kept_count')} "
                f"of {keyword_cleaning.get('input_count')} "
                f"(removed {keyword_cleaning.get('removed_count')})."
                if keyword_cleaning.get("input_count")
                else ""
            )
            if providers_used
            else "No Ahrefs/DataForSEO credentials — clustering used seeds without live volume."
        ),
        },
        serp_by_keyword=serp_for_intent,
    )
    from app.services.bw_workbook import attach_workbook_to_search_demand
    from app.services.json_safe import json_safe

    summary = attach_workbook_to_search_demand(summary)
    summary = json_safe(summary)

    # A phase reset clears confirmed_at in the database while this run may
    # still be holding the selection it loaded at the start. Write that reopen
    # back; otherwise the in-memory copy restores the old confirmation.
    fresh = await db.execute(
        text(
            "SELECT commercial_scope->'service_prioritization'->>'confirmed_at' "
            "AS confirmed_at, "
            "commercial_scope->'service_prioritization'->>'reopened_reason' "
            "AS reopened_reason "
            "FROM client_digital_profiles WHERE client_id = :cid"
        ),
        {"cid": str(client.id)},
    )
    stored = fresh.mappings().first()
    if (
        is_prioritization_confirmed(prioritization)
        and stored is not None
        and not stored["confirmed_at"]
    ):
        prioritization["confirmed_at"] = None
        if stored["reopened_reason"]:
            prioritization["reopened_reason"] = stored["reopened_reason"]
    if is_prioritization_confirmed(prioritization) or prioritization.get("reopened_reason"):
        if (
            is_prioritization_confirmed(prioritization)
            and not prioritization.get("competitor_trees")
            and _seconds_left() > 70
        ):
            try:
                # Usually started before expansion and long finished by now.
                if prefetch.has("competitor_trees"):
                    prioritization["competitor_trees"] = await prefetch.result(
                        "competitor_trees", timeout=45
                    )
                else:
                    prioritization["competitor_trees"] = await asyncio.wait_for(
                        discover_competitor_service_trees(
                            competitors,
                            max_competitors=8,
                            max_urls_each=20,
                        ),
                        timeout=45,
                    )
            except asyncio.TimeoutError:
                log.warning(
                    "competitor_service_trees_timeout",
                    client_id=str(client.id),
                    stage="persist",
                )
                prioritization["competitor_trees"] = []
        commercial["service_prioritization"] = prioritization
        profile.commercial_scope = commercial

    _mark("saved")
    summary["timings"] = dict(stage_timings)
    log.info("search_demand_timings", client_id=str(client.id), **stage_timings)
    profile.search_demand_summary = summary
    profile.search_demand_status = "pending_signoff"

    # Clear prior pending ledgers for this agent
    await supersede_pending_findings(
        db, client_id=client.id, agent_key="search_demand"
    )

    db.add(
        FindingsLedger(
            client_id=client.id,
            agent_key="search_demand",
            source_table="client_digital_profiles",
            source_id=profile.id,
            confidence="high" if providers_used else "low",
            status="pending",
        )
    )

    job.status = "succeeded"
    job.completed_at = datetime.now(timezone.utc)
    job.provider_used = ",".join(providers_used) or "seed_only"

    # Persist every rejection from this run, untruncated.
    rejections_written = await record_keyword_rejections(
        db,
        client_id=client.id,
        agent_key="search_demand",
        rejections=rejected_keywords,
        run_id=job.id,
    )

    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "keyword_research",
            "providers": providers_used,
            "keywords": len(merged),
            "rejections_recorded": rejections_written,
        },
    )
    await db.flush()

    best_line = ", ".join(
        f"{b['keyword']} ({b.get('opportunity_score')}%)"
        for b in summary["best_opportunities"][:3]
        if b.get("keyword")
    ) or "none yet"

    card = {
        "card_type": "search_demand_report",
        "title": "Search Demand & Keyword Opportunities",
        "agent_key": "search_demand",
        "actions": ["approve"],
        "required_role": required_role_for("search_demand"),
        **summary,
    }

    events.append(
        {
            "type": "agent_message",
            "agent_key": "search_demand",
            "content": (
                f"Phase 5 complete: {len(merged)} keywords from "
                f"{', '.join(providers_used) or 'seeds only'}; "
                f"seed clusters: {len(seed_clusters)} "
                f"(exact/phrase/related/broad, vol>10 → {seeding.get('keyword_count', 0)} seeded); "
                f"Create Topic produced {len(topic_plan.get('topic_ideas') or [])} ideas "
                f"(pillar: {(topic_plan.get('cluster_map') or {}).get('pillar') or '—'}); "
                f"Keyword Clustering: {cluster_report.get('clusters_created', 0)} clusters, "
                f"{cluster_report.get('orphan_count', 0)} orphans. "
                f"Top opportunities: {best_line}. "
                "Content SEO Specialist can approve into shared memory."
            ),
        }
    )
    events.append({"type": "structured_card", "payload": card})
    events.append({"type": "checkpoint", "payload": card})
    events.append(
        {
            "type": "phase_status",
            "payload": {
                "search_demand_status": profile.search_demand_status,
            },
        }
    )
    events.extend(
        handoff_events(
            "search_demand",
            phase_statuses={
                "website": profile.website_status,
                "content_audit": profile.content_audit_status,
                "search_demand": "pending_signoff",
            },
            result_line=(
                f"{len(merged)} keywords; "
                f"{cluster_report.get('clusters_created', 0)} clusters."
            ),
        )
    )
    # A prefetched call whose step was skipped (time budget, no competitors).
    prefetch.discard_all()
    return events
