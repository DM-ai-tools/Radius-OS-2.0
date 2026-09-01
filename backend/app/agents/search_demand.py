"""Phase 5 — Search Demand & Keyword Research (create_topic + keyword_clustering)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import ahrefs, dataforseo
from app.integrations.llm import extract_domain
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
    competitor_domains as domains_from_comps,
    competitor_names as names_from_comps,
    local_seed_variants,
    resolve_competitors,
    resolve_geographic_focus,
)
from app.services.create_topic import format_audience_label, run_create_topic, topics_from_plan
from app.services.keyword_clustering import (
    build_service_seed_clusters,
    clusters_for_cdp,
    run_keyword_clustering,
)
from app.services.role_skills import required_role_for
from app.services.keyword_opportunity import (
    build_topics,
    detect_funnel,
    detect_intent,
    is_stale_year_keyword,
    merge_keyword_metrics,
    rank_opportunities,
    select_topics_from_service_clusters,
)
from app.services.keyword_relevance import (
    build_relevance_context,
    compact_cleaning_audit,
    competitor_brand_blocklist,
    filter_relevant_keywords,
    is_competitor_brand_term,
    merge_cleaning_audits,
    website_resource_blobs,
)
from app.services.keyword_seeding import (
    clean_provider_seed,
    filter_seed_clusters,
    flatten_dataset,
    run_multi_mode_seeding,
)
from app.agents.prompts import load_skill, load_skill_file


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
            key = item.lower()
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def _page_target_seeds(website: dict[str, Any]) -> list[str]:
    """Core targeting terms for each existing service / sub-service page (Phase 3)."""
    out: list[str] = []
    seen: set[str] = set()

    def _term_from_page(page: dict[str, Any]) -> str | None:
        # Prefer CDD-matched terms, then title, then last path segment
        cdd_terms = page.get("cdd_terms") or []
        if isinstance(cdd_terms, list) and cdd_terms:
            return str(cdd_terms[0]).strip()
        title = str(page.get("title") or "").strip()
        if title and len(title) <= 70 and title.lower() not in ("home", "services"):
            return title
        path = str(page.get("path") or page.get("url") or "").strip()
        seg = [s for s in path.replace("//", "/").strip("/").split("/") if s]
        if seg:
            last = seg[-1].replace("-", " ").replace("_", " ").strip()
            if last and len(last) > 2:
                return last
        return None

    def _add(term: str | None) -> None:
        t = str(term or "").strip()
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)

    for blob in website_resource_blobs(website):
        pages = blob.get("pages")
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict):
                    continue
                ptype = str(page.get("page_type") or "")
                cluster = str(page.get("cluster") or "")
                if ptype in ("service", "sub_service") or cluster in (
                    "services",
                    "sub_services",
                    "service_hub",
                ):
                    _add(_term_from_page(page))
        hier = blob.get("page_hierarchy")
        if isinstance(hier, list):
            for row in hier:
                if not isinstance(row, dict):
                    continue
                if str(row.get("cluster") or "") in ("services", "sub_services", "service_hub"):
                    _add(_term_from_page(row))
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


async def run_search_demand(
    db: AsyncSession,
    *,
    client: Client,
    session_id: UUID,
    user_id: UUID,
    message: str,
) -> list[dict]:
    _ = load_skill("search_demand")
    _ = load_skill_file("create-topic")
    _ = load_skill_file("keyword-clustering")
    _ = user_id

    events: list[dict] = []
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
    website = dict(profile.website_situation_summary or {})
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
    seeds = clean_seeds[:22]

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

    # Do not recrawl competitor HTML here — Phase 4 already timed out on WAF/dead
    # hosts (e.g. .au 403). Keyword gaps come from Ahrefs organic keywords instead.
    events.append(
        {
            "type": "system_notice",
            "content": (
                "Using the approved competitor list for keyword gaps "
                "(no live recrawl)."
            ),
        }
    )
    competitor_sites: list[dict[str, Any]] = []
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
            # Never harvest the competitor's brand "name" as a seed — only
            # descriptive theme fields, and even those are brand-filtered.
            for key in ("positioning", "tier_name", "notes"):
                phrase = " ".join(str(row.get(key) or "").split()[:6]).strip()
                if (
                    phrase
                    and len(phrase) > 3
                    and not is_stale_year_keyword(phrase)
                    and phrase.lower() not in seen
                    and not _is_competitor_brand(phrase)
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

    providers_used: list[str] = []
    provider_errors: list[str] = []
    ahrefs_rows: list[dict[str, Any]] = []
    dfs_rows: list[dict[str, Any]] = []

    # Ahrefs multi-mode seeding: expand EVERY target root (each service / product /
    # page / business keyword), not just CDD keywords. Each root -> exact/phrase/related/broad.
    seed_roots: list[str] = []
    seed_targets: dict[str, dict[str, str]] = {}

    def _add_root(term: str, target_type: str) -> None:
        t = clean_provider_seed(str(term or "").strip()) or str(term or "").strip()
        n = t.lower()
        if not t or len(n) < 2 or is_stale_year_keyword(n):
            return
        # Never seed on a competitor's brand name — seeds describe the client.
        if _is_competitor_brand(t):
            return
        if n not in seed_targets:
            seed_targets[n] = {"target": t, "target_type": target_type}
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
    for term in _page_target_seeds(website):
        _add_root(term, "page")
    # 4) Remaining discovered seeds (website themes) — skip templated variants
    for s in seeds:
        if not _is_template_variant(s):
            _add_root(s, "keyword")

    # Prioritize services + CDD keywords first, then pages, then rest
    seed_roots = sorted(
        seed_roots,
        key=lambda s: (
            {"service": 0, "keyword": 1, "page": 2}.get(
                seed_targets.get(s.lower(), {}).get("target_type", "keyword"), 3
            ),
            -len(s.split()),
            s.lower(),
        ),
    )
    relevance_ctx = build_relevance_context(
        services=services,
        cdd_keywords=cdd_keywords,
        website=website,
        seeds=seed_roots,
        competitor_names=competitor_labels,
        competitor_domains=competitor_domains,
        brand_name=client.display_name,
        domain=domain or None,
    )
    seeding = await run_multi_mode_seeding(
        seed_roots,
        country=country,
        min_volume=10,
        max_seeds=30,
        limit_per_mode=35,
        seed_targets=seed_targets,
        location_code=labs_location_code,
        relevance_context=relevance_ctx,
    )
    provider_errors.extend(seeding.get("provider_errors") or [])
    seed_clusters = list(seeding.get("seed_clusters") or [])
    keyword_dataset = list(seeding.get("keyword_dataset") or [])
    cleaning_audits = [seeding.get("keyword_cleaning")]

    # Relevance filter on the full seeded dataset before downstream merge/scoring.
    from app.services.keyword_llm_relevance import llm_filter_keywords_by_seed

    company_url = str(client.primary_url or "")
    comp_urls = [
        f"https://{d}" if not d.startswith("http") else d
        for d in competitor_domains[:6]
    ]
    pre_filter_count = len(keyword_dataset)
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Filtering {pre_filter_count} seeded keywords for business relevance…"
            ),
        }
    )
    keyword_dataset, relevance_dropped, relevance_audit = await llm_filter_keywords_by_seed(
        keyword_dataset,
        company_url=company_url,
        competitor_urls=comp_urls,
        business_name=client.display_name,
        industry=client.industry,
        services=services,
        geography=str(geo.get("geographic_focus") or geo.get("location_name") or "") or None,
        seed_targets=seed_targets,
    )
    _relevant_kw_set = {_norm_kw(r.get("keyword")) for r in keyword_dataset if r.get("keyword")}
    seed_clusters = filter_seed_clusters(seed_clusters, _relevant_kw_set)
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
    }
    events.append(
        {
            "type": "system_notice",
            "content": (
                f"Relevance filter: kept {len(keyword_dataset)} of {pre_filter_count} "
                f"({relevance_audit.get('dropped', 0)} dropped, "
                f"{relevance_audit.get('api_calls', 0)} API calls)."
            ),
        }
    )

    if keyword_dataset or seed_clusters:
        if "ahrefs" not in providers_used:
            providers_used.append("ahrefs")
        for row in keyword_dataset:
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
                    "parent_topic": row.get("parent_topic"),
                    "source": row.get("source") or "ahrefs",
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
                f"keywords={_tt.get('keyword', 0)}); volume > 10. "
                f"All 4 classes filled for "
                f"{seeding.get('seeds_with_all_classes', 0)}/{len(seed_clusters)} seeds."
            ),
        }
    )

    # Client + competitor organic keywords (gap signals)
    if domain:
        org, e3 = await ahrefs.organic_keywords(domain, country=country, limit=50)
        provider_errors.extend(e3)
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
        kept, _excl, audit = filter_relevant_keywords(
            pending_org, relevance_ctx, source_label="ahrefs_organic"
        )
        cleaning_audits.append(audit)
        ahrefs_rows.extend(kept)
    else:
        client_kw_map = {}

    for cd in competitor_domains[:4]:
        corg, e4 = await ahrefs.organic_keywords(cd, country=country, limit=40)
        provider_errors.extend(e4)
        pending_comp: list[dict[str, Any]] = []
        for r in corg:
            kw = str(r.get("keyword") or "")
            if not kw or is_stale_year_keyword(kw):
                continue
            kl = kw.lower()
            r["gap_flag"] = kl not in client_kw_map
            r["competitor_domain"] = cd
            pending_comp.append(r)
        kept, _excl, audit = filter_relevant_keywords(
            pending_comp, relevance_ctx, source_label="ahrefs_competitor"
        )
        cleaning_audits.append(audit)
        ahrefs_rows.extend(kept)

    # DataForSEO related + volume + KD + gap (location from geographic_focus).
    # Related-keyword lookups run concurrently — sequential calls blew the turn budget.
    _dfs_sem = asyncio.Semaphore(4)

    async def _related(seed: str) -> tuple[list[dict[str, Any]], list[str]]:
        async with _dfs_sem:
            return await dataforseo.related_keywords(
                clean_provider_seed(seed), limit=35, location_code=labs_location_code
            )

    related_results = await asyncio.gather(
        *[_related(s) for s in seed_roots[:8]], return_exceptions=True
    )
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
            kept, _excl, audit = filter_relevant_keywords(
                pending_related, relevance_ctx, source_label="dataforseo_related"
            )
            cleaning_audits.append(audit)
            dfs_rows.extend(kept)

    all_kw_names = list(
        {
            *(clean_provider_seed(str(r.get("keyword") or "")) for r in ahrefs_rows if r.get("keyword")),
            *(clean_provider_seed(str(r.get("keyword") or "")) for r in dfs_rows if r.get("keyword")),
            *(clean_provider_seed(s) for s in seed_roots if relevance_ctx.seed_is_supported(s)),
        }
    )
    # Prefer specific multi-word names for volume lookup
    all_kw_names = sorted(
        [k for k in all_kw_names if k and not is_stale_year_keyword(str(k))],
        key=lambda k: (-len(str(k).split()), str(k).lower()),
    )[:60]
    volumes, e6 = await dataforseo.search_volume(
        all_kw_names, location_code=location_code
    )
    provider_errors.extend(e6)
    if volumes:
        if "dataforseo" not in providers_used:
            providers_used.append("dataforseo")
        kept, _excl, audit = filter_relevant_keywords(
            volumes, relevance_ctx, source_label="dataforseo_volume"
        )
        cleaning_audits.append(audit)
        dfs_rows.extend(kept)
    kds, e7 = await dataforseo.keyword_difficulty(
        all_kw_names[:30], location_code=labs_location_code
    )
    provider_errors.extend(e7)
    kept_kds, _excl, kd_audit = filter_relevant_keywords(
        kds, relevance_ctx, source_label="dataforseo_kd"
    )
    cleaning_audits.append(kd_audit)
    dfs_rows.extend(kept_kds)

    if competitor_domains:
        gap_rows, e8 = await dataforseo.keyword_gap(
            domain or competitor_domains[0],
            competitor_domains,
            location_code=labs_location_code,
        )
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
            kept, _excl, audit = filter_relevant_keywords(
                pending_gap, relevance_ctx, source_label="dataforseo_gap"
            )
            cleaning_audits.append(audit)
            dfs_rows.extend(kept)

    # Authority from website/competitive summary if present
    authority = None
    bl = website.get("backlinks") or website.get("authority") or {}
    if isinstance(bl, dict):
        authority = bl.get("authority_score") or bl.get("domain_rating")
    if authority is None:
        authority = competitive.get("client_baseline_maturity")

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
        merged = [r for r in merged if _norm_kw(r.get("keyword")) in seed_kw_set]
        events.append(
            {
                "type": "system_notice",
                "content": (
                    f"Phase 5 seed pool filter: kept {len(merged)} of "
                    f"{merged_before_seed_filter} merged keywords "
                    f"(Multi-mode seeding vol>{10})."
                ),
            }
        )

    ranked = rank_opportunities(
        merged,
        seeds=seeds,
        products=products,
        client_authority=float(authority) if authority is not None else None,
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
        keyword_dataset, key=lambda r: r.get("volume") or 0, reverse=True
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
    _serp_sem = asyncio.Semaphore(4)

    async def _serp(kw: str) -> tuple[list[dict[str, Any]], list[str]]:
        async with _serp_sem:
            return await dataforseo.serp_overview(
                kw, depth=8, location_code=location_code
            )

    serp_results = await asyncio.gather(
        *[_serp(str(r.get("keyword") or "").strip()) for r in serp_targets],
        return_exceptions=True,
    )

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
    topic_kw_pool = select_topics_from_service_clusters(
        service_clusters,
        products=products or services,
        limit=10,
        max_per_service=1,
    )
    topic_plan = await run_create_topic(
        seed=str(topic_seed),
        message=message,
        audience=audience,
        best_opportunities=topic_kw_pool,
        strong_evergreen=[],
        keyword_pool=keyword_dataset,
        competitor_domains=competitor_domains,
        competitor_names=competitor_labels,
        geographic_focus=str(geo.get("geographic_focus") or ""),
        location_name=str(geo.get("location_name") or ""),
        competitor_context=competitor_context_blob(competitors, competitor_sites, geo),
        industry=client.industry,
        products=products,
        cdd_keywords=cdd_keywords,
        pain_points=pain_points,
    )
    topic_plan["selection_mode"] = "service_round_robin"
    topic_plan["selection_version"] = "phase5_service_intent_v2"
    topic_plan["assigned_services"] = [
        str(r.get("service") or r.get("target") or r.get("seed") or "")
        for r in topic_kw_pool
    ]

    # Keyword Clustering — uses the multi-mode seeded keyword dataset directly
    cluster_input = list(keyword_dataset)
    seen_cluster = {str(r.get("keyword") or "").lower() for r in cluster_input}
    for ht in ranked.get("head_terms") or []:
        k = str(ht.get("keyword") or "").lower()
        if k and k not in seen_cluster:
            cluster_input.append(ht)
            seen_cluster.add(k)

    cluster_report = await run_keyword_clustering(
        cluster_input,
        seeds=seeds,
        brand_name=client.display_name,
        domain=domain or None,
        products=products,
        relevance_context=relevance_ctx,
    )
    cluster_report["service_clusters"] = service_clusters
    cluster_report["services_clustered"] = len(service_clusters)
    cluster_report["seeds_clustered_by_service"] = sum(
        int(group.get("seed_count") or 0) for group in service_clusters
    )
    clusters = clusters_for_cdp(cluster_report)

    # Link topic ideas to clusters + live metrics from the seeded dataset first
    metrics_by_kw = {
        str(r.get("keyword") or "").strip().lower(): r
        for r in keyword_dataset
        if r.get("keyword")
    }
    for r in ranked["all_scored"]:
        key = str(r.get("keyword") or "").strip().lower()
        if key and key not in metrics_by_kw:
            metrics_by_kw[key] = r
    seed_kw_keys = set(metrics_by_kw)
    grounded_ideas: list[dict[str, Any]] = []
    for idea in topic_plan.get("topic_ideas") or []:
        if not isinstance(idea, dict):
            continue
        mk = str(
            idea.get("primary_keyword") or idea.get("keyword") or ""
        ).strip().lower()
        if mk not in seed_kw_keys:
            continue
        met = metrics_by_kw.get(mk) or {}
        if idea.get("volume") is None and met.get("volume") is not None:
            idea["volume"] = met.get("volume")
        if idea.get("difficulty") in (None, "", "—") and met.get("difficulty") is not None:
            idea["difficulty"] = met.get("difficulty")
        if not idea.get("intent") and met.get("intent"):
            idea["intent"] = met.get("intent")
        if met.get("cpc") is not None:
            idea["cpc"] = met.get("cpc")
        if met.get("competitor_domains"):
            idea["competitor_domains"] = met.get("competitor_domains")
        if met.get("serp_titles"):
            idea["serp_titles"] = met.get("serp_titles")
        elif mk in serp_by_kw:
            idea["serp_titles"] = serp_by_kw[mk]
        # Secondaries must stay in the same seed/target family as the primary
        primary_seed = _norm_kw(met.get("seed") or idea.get("seed"))
        primary_target = _norm_kw(met.get("target") or idea.get("target"))
        secondary = []
        for s in idea.get("secondary_keywords") or idea.get("supporting_keywords") or []:
            text = str(s).strip()
            key = text.lower()
            if not text or key == mk or key not in seed_kw_keys:
                continue
            other = metrics_by_kw.get(key) or {}
            same_family = (
                (primary_seed and _norm_kw(other.get("seed")) == primary_seed)
                or (primary_target and _norm_kw(other.get("target")) == primary_target)
            )
            if same_family:
                secondary.append(text)
        if len(secondary) < 3:
            from app.services.create_topic import _secondary_keywords_for

            secondary = _secondary_keywords_for(
                str(idea.get("primary_keyword") or idea.get("keyword") or ""),
                pool=keyword_dataset,
            )
        idea["secondary_keywords"] = secondary[:8]
        idea["supporting_keywords"] = secondary[:8]
        if met.get("match_class") and not idea.get("match_class"):
            idea["match_class"] = met.get("match_class")
        if met.get("seed") and not idea.get("seed"):
            idea["seed"] = met.get("seed")
        if met.get("target") and not idea.get("target"):
            idea["target"] = met.get("target")
        # Intent/funnel already shaped from keyword — don't let clusters overwrite intent
        for c in clusters:
            if mk == str(c.get("primary_keyword") or "").strip().lower():
                idea["cluster"] = c.get("name")
                break
        grounded_ideas.append(idea)
    topic_plan["topic_ideas"] = grounded_ideas
    topic_plan["keyword_source"] = "multi_mode_seeding"

    topics = topics_from_plan(topic_plan)
    if not topics:
        topics = build_topics(clusters, keyword_dataset)

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
        "topic_plan": topic_plan,
        "topics": topics,
        "cluster_report": cluster_report,
        "clusters": clusters,
        "keyword_seeding": {
            "min_volume": seeding.get("min_volume", 10),
            "class_counts": seeding.get("class_counts") or {},
            "target_type_counts": seeding.get("target_type_counts") or {},
            "note": seeding.get("note"),
            "seed_count": len(seed_clusters),
            "seeded_keyword_count": seeding.get("keyword_count") or len(keyword_dataset),
            "seeds_with_all_classes": seeding.get("seeds_with_all_classes", 0),
            "class_coverage": seeding.get("class_coverage") or [],
            "providers": seeding.get("providers") or [],
        },
        "keyword_cleaning": keyword_cleaning,
        "relevance_audit": relevance_audit,
        "relevance_dropped_count": len(relevance_dropped),
        "seed_clusters": seed_clusters,
        "keyword_dataset": keyword_dataset[:500],
        "best_opportunities": [_detail_row(r) for r in ranked["best_opportunities"][:12]],
        "strong_evergreen": evergreen_table,
        "head_terms": [_detail_row(r) for r in (ranked.get("head_terms") or [])[:10]],
        "trend_plays": [_detail_row(r) for r in ranked["trend_plays"][:12]],
        "avoid": [_detail_row(r) for r in ranked["avoid"][:10]],
        "keyword_table": keyword_table,
        "keyword_count": len(merged),
        "skills_used": ["create_topic", "keyword_clustering", "keyword_seeding"],
        "note": (
            "Multi-mode seeding (exact / phrase / related / broad, volume > 10) then "
            "opportunity scoring. Prioritized specific service/CDD keywords with "
            "manageable KD and competitor-gap topics. Past years demoted; "
            f"present year preferred. "
            "Generic head terms kept in clusters. "
            f"Competitors locked to shared-memory set ({len(competitors)}). "
            f"Geo locked to {geo.get('location_name')}."
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

    summary = attach_workbook_to_search_demand(summary)

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
    await log_event(
        db,
        client_id=client.id,
        actor_type="agent",
        event_type="job_completed",
        event_detail={
            "job_type": "keyword_research",
            "providers": providers_used,
            "keywords": len(merged),
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
    return events
