"""Phase-specific validation criteria — derived from agent purpose and SKILL contracts.

Adding a new phase: register an entry in PHASE_CRITERIA with purpose, required
output keys, applicable semantic parameters, and deterministic check flags.
The validator selects criteria by agent_key; it does not invent a one-size-fits-all rubric.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PhaseCriteria:
    agent_key: str
    phase_label: str
    purpose: str
    # CDP summary attribute holding this phase's working output (None = special)
    summary_attr: str | None
    status_attr: str
    # Deterministic keys that must exist and be non-empty when output is "complete enough"
    required_keys: tuple[str, ...] = ()
    # Keys that, if present, must not claim invented metrics without provider evidence
    honesty_keys: tuple[str, ...] = ()
    # Semantic dimensions to evaluate (subset of CHECK_CATEGORIES)
    semantic_parameters: tuple[str, ...] = ()
    # Prior packs that should not be contradicted
    prior_phase_attrs: tuple[str, ...] = ()
    # Extra deterministic check ids enabled for this phase
    deterministic_checks: tuple[str, ...] = ()
    # What must never appear (soft — semantic + light heuristics)
    never_present_notes: str = ""
    # What successful output must accomplish
    success_looks_like: str = ""
    # Downstream risk if this phase ships bad data
    downstream_risk: str = ""


# Shared semantic sets by phase family
_FOUNDATION_SEMANTIC = (
    "company_alignment",
    "domain_alignment",
    "industry_alignment",
    "phase_objective_compliance",
    "completeness",
    "relevance",
    "quality",
    "factual_consistency",
)

_COMMERCIAL_SEMANTIC = (
    "company_alignment",
    "domain_alignment",
    "industry_alignment",
    "service_product_alignment",
    "business_model_alignment",
    "target_audience_alignment",
    "geographic_market_alignment",
    "company_standard_compliance",
    "phase_objective_compliance",
    "input_output_consistency",
    "factual_consistency",
    "completeness",
    "relevance",
    "quality",
    "prior_phase_consistency",
)

_STRUCTURAL_SEMANTIC = (
    "company_alignment",
    "phase_objective_compliance",
    "input_output_consistency",
    "prior_phase_consistency",
    "completeness",
    "quality",
    "factual_consistency",
)

_CONTENT_SEMANTIC = (
    "company_alignment",
    "domain_alignment",
    "industry_alignment",
    "service_product_alignment",
    "target_audience_alignment",
    "geographic_market_alignment",
    "company_standard_compliance",
    "phase_objective_compliance",
    "prior_phase_consistency",
    "factual_consistency",
    "completeness",
    "relevance",
    "quality",
)


PHASE_CRITERIA: dict[str, PhaseCriteria] = {
    "discovery_agent": PhaseCriteria(
        agent_key="discovery_agent",
        phase_label="Business & Project Discovery",
        purpose=(
            "Pre-research and questionnaire capture of the client's commercial and "
            "marketing context — products, geo, audience, positioning — without fabricating "
            "private metrics."
        ),
        summary_attr=None,
        status_attr="discovery_status",
        required_keys=(),
        honesty_keys=("average_order_value", "lifetime_value", "seo_traffic_current"),
        semantic_parameters=_FOUNDATION_SEMANTIC
        + (
            "service_product_alignment",
            "business_model_alignment",
            "target_audience_alignment",
            "geographic_market_alignment",
        ),
        prior_phase_attrs=(),
        deterministic_checks=("discovery_identity", "output_present"),
        never_present_notes="Invented revenue/AOV/private analytics presented as confirmed fact.",
        success_looks_like=(
            "Correct company identity; industry/domain match; products/services relevant; "
            "confidence/source honesty; gaps named rather than filled with fiction."
        ),
        downstream_risk="Poisoned CDD seeds Phase 5+ with wrong products, geo, and keywords.",
    ),
    "tracking_access_agent": PhaseCriteria(
        agent_key="tracking_access_agent",
        phase_label="Access, Tracking & Data",
        purpose="Verify analytics/tag access and historical baseline honesty before anomaly work.",
        summary_attr="tracking_baseline",
        status_attr="tracking_status",
        required_keys=(),
        semantic_parameters=(
            "company_alignment",
            "phase_objective_compliance",
            "completeness",
            "quality",
            "honesty_anti_hallucination",
        ),
        prior_phase_attrs=("commercial_scope", "marketing_context"),
        deterministic_checks=("output_present", "tracking_honesty"),
        never_present_notes="Claiming connected platforms without evidence; publishing baseline on Flag.",
        success_looks_like="Honest connection statuses; unverified conversions not marked working.",
        downstream_risk="False tracking baseline misleads website anomaly and technical SEO.",
    ),
    "website_situation_agent": PhaseCriteria(
        agent_key="website_situation_agent",
        phase_label="Website Situation",
        purpose="Crawl/audit the live site — inventory, SEO issues, authority — no invented URLs.",
        summary_attr="website_situation_summary",
        status_attr="website_status",
        required_keys=(),
        semantic_parameters=_FOUNDATION_SEMANTIC
        + ("service_product_alignment", "prior_phase_consistency"),
        prior_phase_attrs=("commercial_scope", "marketing_context", "tracking_baseline"),
        deterministic_checks=("output_present", "website_inventory"),
        never_present_notes="Fabricated page URLs; causal traffic claims without tracking.",
        success_looks_like="Real inventory or explicit crawl failure; CDD gaps flagged not invented.",
        downstream_risk="Empty/fake inventory blocks content audit and corrupts IA current_state.",
    ),
    "competitor_market_agent": PhaseCriteria(
        agent_key="competitor_market_agent",
        phase_label="Competitor & Market Analysis",
        purpose="Tiered competitive peers for the client's industry — not generic agency defaults.",
        summary_attr="competitive_landscape_summary",
        status_attr="competitor_status",
        required_keys=(),
        semantic_parameters=_COMMERCIAL_SEMANTIC,
        prior_phase_attrs=(
            "commercial_scope",
            "marketing_context",
            "website_situation_summary",
        ),
        deterministic_checks=("output_present", "competitor_peers"),
        never_present_notes="Peers from unrelated industries; inventing competitor domains.",
        success_looks_like="Industry-relevant scored peers; Tier 5 exclusions explained.",
        downstream_risk="Wrong peers distort keyword gaps and trademark denylists.",
    ),
    "search_demand": PhaseCriteria(
        agent_key="search_demand",
        phase_label="Search Demand & Keywords",
        purpose=(
            "Expand CDD/discovery seeds into keyword clusters with intent/funnel, "
            "classify against the Phase 3 sitemap (existing vs new), and draft topics "
            "only for new clusters — URL mapping follows in Phase 6."
        ),
        summary_attr="search_demand_summary",
        status_attr="search_demand_status",
        required_keys=("cluster_report", "sitemap_classification"),
        honesty_keys=("best_opportunities", "keyword_dataset"),
        semantic_parameters=_COMMERCIAL_SEMANTIC,
        prior_phase_attrs=(
            "commercial_scope",
            "marketing_context",
            "competitive_landscape_summary",
            "website_situation_summary",
        ),
        deterministic_checks=(
            "output_present",
            "required_keys",
            "search_demand_relevance_shell",
        ),
        never_present_notes=(
            "Invented search volumes; competitor brand names as seeds; "
            "keywords for services the company does not offer."
        ),
        success_looks_like=(
            "Clusters with intent/funnel; sitemap classification present; "
            "new-topic drafts only where the site has no match."
        ),
        downstream_risk="Wrong existing/new split locks URL mapping and production.",
    ),
    "content_strategy": PhaseCriteria(
        agent_key="content_strategy",
        phase_label="Content Strategy",
        purpose="Priority queue and calendar from locked Phase 5 — preserve topic titles.",
        summary_attr="seo_strategy_summary",
        status_attr="seo_strategy_status",
        required_keys=("priority_queue",),
        semantic_parameters=_CONTENT_SEMANTIC,
        prior_phase_attrs=(
            "search_demand_summary",
            "commercial_scope",
            "marketing_context",
            "competitive_landscape_summary",
        ),
        deterministic_checks=(
            "output_present",
            "required_keys",
            "strategy_phase5_carry",
        ),
        never_present_notes="Invented volumes; dropping Phase 5 titles; wrong-industry pillars.",
        success_looks_like="Queue inherits Phase 5 topics; gaps/calendar company-relevant.",
        downstream_risk="IA and planning inherit wrong topics/URLs.",
    ),
    "site_architecture": PhaseCriteria(
        agent_key="site_architecture",
        phase_label="Site Architecture & IA",
        purpose="URL/parent/depth tree and cluster ownership from sitemap-classified clusters.",
        summary_attr="site_architecture_summary",
        status_attr="site_architecture_status",
        required_keys=("target_url_tree",),
        semantic_parameters=_STRUCTURAL_SEMANTIC
        + ("service_product_alignment", "domain_alignment"),
        prior_phase_attrs=("seo_strategy_summary", "search_demand_summary"),
        deterministic_checks=(
            "output_present",
            "required_keys",
            "ia_tree_nodes",
        ),
        never_present_notes="Missing url/parent/depth; tree unrelated to strategy keywords.",
        success_looks_like=(
            "Every tree node has url+parent+depth; money pages reachable; "
            "URL map honors existing vs new dispositions."
        ),
        downstream_risk="Broken tree blocks technical SEO and content planning gates.",
    ),
    "technical_seo": PhaseCriteria(
        agent_key="technical_seo",
        phase_label="Technical SEO",
        purpose="Crawlability/indexation/broken-link backlog — mark CWV/hreflang Not Measured when not run.",
        summary_attr="technical_seo_summary",
        status_attr="technical_seo_status",
        required_keys=(),
        semantic_parameters=(),
        prior_phase_attrs=("site_architecture_summary", "website_situation_summary"),
        deterministic_checks=("output_present", "technical_seo_honesty"),
        never_present_notes="Fabricated CWV scores; claiming measured companions that were not run.",
        success_looks_like="Honest not_measured list; IA actions forwarded when tree exists.",
        downstream_risk="Fake scores drive wrong remediation priorities.",
    ),
    "content_audit": PhaseCriteria(
        agent_key="content_audit",
        phase_label="Existing Content Audit",
        purpose="Disposition existing URLs — keep/refresh/optimise/consolidate — not age-based delete.",
        summary_attr="content_audit_summary",
        status_attr="content_audit_status",
        required_keys=("inventory",),
        semantic_parameters=_CONTENT_SEMANTIC,
        prior_phase_attrs=(
            "website_situation_summary",
            "search_demand_summary",
            "technical_seo_summary",
        ),
        deterministic_checks=("output_present", "required_keys", "audit_dispositions"),
        never_present_notes="Empty inventory marked complete; auto-delete by age.",
        success_looks_like="Non-empty inventory with allowed dispositions; gaps vs demand noted.",
        downstream_risk="Planning blocked or wrong refresh/retire actions.",
    ),
    "content_planning": PhaseCriteria(
        agent_key="content_planning",
        phase_label="New Page & Content Planning",
        purpose="Join strategy + architecture + audit into one locked roadmap — no new topics.",
        summary_attr="content_planning_summary",
        status_attr="content_planning_status",
        required_keys=("pages",),
        semantic_parameters=_STRUCTURAL_SEMANTIC
        + ("service_product_alignment", "relevance"),
        prior_phase_attrs=(
            "seo_strategy_summary",
            "site_architecture_summary",
            "content_audit_summary",
        ),
        deterministic_checks=(
            "output_present",
            "required_keys",
            "planning_lock_integrity",
            "planning_action_fit",
        ),
        never_present_notes="Invented topics; duplicate keywords; ignoring architecture URLs.",
        success_looks_like="Locked when gates ok; architecture URL wins conflicts; one keyword→one URL.",
        downstream_risk="Unlocked/bad roadmap blocks or mis-briefs production.",
    ),
    "content_production": PhaseCriteria(
        agent_key="content_production",
        phase_label="Content Briefing & Production",
        purpose="Brief locked create/refresh items; write at most one full draft per run.",
        summary_attr="content_production_summary",
        status_attr="content_production_status",
        required_keys=(),
        semantic_parameters=_CONTENT_SEMANTIC,
        prior_phase_attrs=(
            "content_planning_summary",
            "marketing_context",
            "commercial_scope",
            "seo_strategy_summary",
        ),
        deterministic_checks=("output_present", "production_one_page", "production_action_fit"),
        never_present_notes="Batch drafts; briefing unlocked plan; generic off-brand content.",
        success_looks_like="Briefs match roadmap; draft aligned to company voice/services.",
        downstream_risk="Wrong draft flows into on-page and publishing.",
    ),
    "on_page_seo": PhaseCriteria(
        agent_key="on_page_seo",
        phase_label="On-Page SEO",
        purpose="Meta/schema/links for the selected production draft — strip competitor trademarks.",
        summary_attr="on_page_seo_summary",
        status_attr="on_page_seo_status",
        required_keys=(),
        semantic_parameters=(
            "company_alignment",
            "service_product_alignment",
            "company_standard_compliance",
            "phase_objective_compliance",
            "prior_phase_consistency",
            "completeness",
            "quality",
        ),
        prior_phase_attrs=(
            "content_production_summary",
            "site_architecture_summary",
            "competitive_landscape_summary",
        ),
        deterministic_checks=("output_present", "on_page_one_page"),
        never_present_notes="Optimizing full queue; competitor brand leakage in titles.",
        success_looks_like="Single-page package; IA URL; trademarks blocked/logged.",
        downstream_risk="Wrong package published or trademark risk.",
    ),
    "publishing": PhaseCriteria(
        agent_key="publishing",
        phase_label="Publishing & Indexation",
        purpose="Preview/draft/live publish with verification — never claim success without verify.",
        summary_attr="publishing_summary",
        status_attr="publishing_status",
        required_keys=("mode",),
        semantic_parameters=(
            "company_alignment",
            "phase_objective_compliance",
            "prior_phase_consistency",
            "honesty_anti_hallucination",
            "completeness",
            "quality",
        ),
        prior_phase_attrs=("on_page_seo_summary", "content_production_summary"),
        deterministic_checks=("output_present", "required_keys", "publishing_mode"),
        never_present_notes="Silent live write; unverified success; IndexNow submitted claims.",
        success_looks_like="mode in preview|draft|publish; verification present for writes.",
        downstream_risk="False publish success or wrong live content.",
    ),
}


# Agent key → CDP status / summary (shared with review._set_phase_status)
AGENT_STATUS_ATTR: dict[str, str] = {
    c.agent_key: c.status_attr for c in PHASE_CRITERIA.values()
}
AGENT_SUMMARY_ATTR: dict[str, str | None] = {
    c.agent_key: c.summary_attr for c in PHASE_CRITERIA.values()
}


def get_criteria(agent_key: str) -> PhaseCriteria | None:
    return PHASE_CRITERIA.get(agent_key)


def list_applicable_parameters(agent_key: str) -> list[str]:
    c = get_criteria(agent_key)
    if not c:
        return []
    params = list(c.semantic_parameters)
    if c.required_keys or c.deterministic_checks:
        if "schema_structure" not in params:
            params.append("schema_structure")
    if c.honesty_keys or "honesty_anti_hallucination" in c.deterministic_checks:
        if "honesty_anti_hallucination" not in params:
            params.append("honesty_anti_hallucination")
    return params


def criteria_prompt_block(agent_key: str) -> str:
    """Human-readable block injected into the semantic validator prompt."""
    c = get_criteria(agent_key)
    if not c:
        return f"Unknown phase `{agent_key}` — apply only company_alignment and phase_objective_compliance."
    lines = [
        f"Phase: {c.phase_label} ({c.agent_key})",
        f"Purpose: {c.purpose}",
        f"Success looks like: {c.success_looks_like}",
        f"Must never present: {c.never_present_notes or 'n/a'}",
        f"Downstream risk: {c.downstream_risk}",
        "Applicable validation parameters for THIS phase only:",
        *[f"- {p}" for p in c.semantic_parameters],
        "Do NOT invent checks outside this list. Skip parameters that are not applicable "
        "given the output type (e.g. skip geographic checks if the output has no geo claims).",
    ]
    return "\n".join(lines)


def phase_criteria_as_dict(agent_key: str) -> dict[str, Any]:
    c = get_criteria(agent_key)
    if not c:
        return {}
    return {
        "agent_key": c.agent_key,
        "phase_label": c.phase_label,
        "purpose": c.purpose,
        "summary_attr": c.summary_attr,
        "status_attr": c.status_attr,
        "required_keys": list(c.required_keys),
        "semantic_parameters": list(c.semantic_parameters),
        "deterministic_checks": list(c.deterministic_checks),
        "prior_phase_attrs": list(c.prior_phase_attrs),
        "success_looks_like": c.success_looks_like,
        "never_present_notes": c.never_present_notes,
        "downstream_risk": c.downstream_risk,
    }
