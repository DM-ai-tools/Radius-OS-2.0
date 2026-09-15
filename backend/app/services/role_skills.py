"""SEO role ↔ skill coverage.

Authority:
  - SearchFit SEO_Skills_Role_Coverage Details_v1.0.html (phase → owning role)
  - docs/architecture/TR_SEO_Architecture_v1_9.html (skill assignment by role + common skills)

Full catalog is defined here for all roles/skills. Runtime agents for Phases 1–12
are enforced via RolePermission rows; later-phase skills are mapped but not wired yet.
"""

from __future__ import annotations

from typing import Any

from app.services.memory_packs import (
    slim_competitor_memory,
    slim_content_audit_memory,
    slim_content_planning_memory,
    slim_content_production_memory,
    slim_on_page_seo_memory,
    slim_publishing_memory,
    slim_search_demand_memory,
    slim_seo_strategy_memory,
    slim_site_architecture_memory,
    slim_technical_seo_memory,
)

# Display labels for signup / UI
SEO_ROLES: list[dict[str, str]] = [
    {
        "name": "head_of_department",
        "label": "Head of Department",
        "description": "Full access — can run and approve every skill across all phases.",
    },
    {
        "name": "client_success_manager",
        "label": "Client Success Manager",
        "description": "Owns Discovery (D1–D5); supports Tracking known-changes (T5).",
    },
    {
        "name": "technical_seo_specialist",
        "label": "Technical SEO Specialist",
        "description": "Owns Tracking sign-off (T6) and Website Situation Analysis.",
    },
    {
        "name": "seo_strategist",
        "label": "SEO Strategist",
        "description": "Owns Competitor & Market, Content Strategy, and Site Architecture (Phase 4 + 6).",
    },
    {
        "name": "content_seo_specialist",
        "label": "Content SEO Specialist",
        "description": "Owns Phase 5 Search Demand (create topic + keyword clustering).",
    },
    {
        "name": "on_page_seo_specialist",
        "label": "On-Page SEO Specialist",
        "description": "On-page SEO, internal linking, publish authority — view-only on 1–4.",
    },
    {
        "name": "structured_data_specialist",
        "label": "Structured Data Specialist",
        "description": "Schema markup — view-only on 1–4.",
    },
    {
        "name": "seo_qa_lead",
        "label": "SEO QA Lead",
        "description": "QA / reporting across phases.",
    },
]

# Head of Department is deliberately excluded from unconditional self-service:
# it grants full trigger+approve access across every phase for every client, and
# there is no per-client ownership model to scope that down. It is only ever
# self-assignable for first-run org bootstrap on Railway (see auth.py's
# `_hod_exists` gate) — once one Head of Department account exists, further HoD
# accounts must be created/promoted by an existing admin, not self-registered.
SELF_SERVICE_ROLES: set[str] = {r["name"] for r in SEO_ROLES if r["name"] != "head_of_department"}
BOOTSTRAP_ADMIN_ROLE = "head_of_department"


# Phase / skill ownership from coverage doc (all phases). implemented=False → future.
PHASE_SKILL_COVERAGE: list[dict[str, Any]] = [
    {
        "phase": 1,
        "title": "Business & Project Discovery",
        "skills": ["discovery_agent"],
        "roles": ["client_success_manager"],
        "implemented": True,
    },
    {
        "phase": 2,
        "title": "Access, Tracking & Data Collection",
        "skills": ["tracking_access_agent"],
        "roles": ["technical_seo_specialist", "client_success_manager"],
        "implemented": True,
    },
    {
        "phase": 3,
        "title": "Current Website Situation Analysis",
        "skills": ["website_situation_agent", "seo_audit"],
        "roles": ["technical_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 4,
        "title": "Competitor & Market Analysis",
        "skills": ["competitor_market_agent"],
        # TR_SEO_Architecture v1.9 — common skill, not gated to one role
        "roles": [
            "seo_strategist",
            "content_seo_specialist",
            "technical_seo_specialist",
            "on_page_seo_specialist",
            "structured_data_specialist",
            "seo_qa_lead",
        ],
        "shared": True,
        "implemented": True,
    },
    {
        "phase": 5,
        "title": "Search Demand & Keyword Research",
        "skills": ["search_demand", "keyword_clustering", "create_topic"],
        "roles": ["content_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 6,
        "title": "SEO Strategy & Information Architecture",
        # Coverage v1.0 mapped Content Strategy only; Site Architecture fills the IA gap
        "skills": ["content_strategy", "site_architecture"],
        "roles": ["seo_strategist", "on_page_seo_specialist"],  # Content Strategy shared w/ On-Page (v1.9)
        "implemented": True,
    },
    {
        "phase": 7,
        "title": "Technical SEO",
        # Entry: technical_seo composites seo_audit + broken_links; companions are handoffs
        "skills": [
            "technical_seo",
            "seo_audit",
            "broken_links",
            "rendering_audit",
            "cwv_measurement",
            "hreflang_validator",
        ],
        "roles": ["technical_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 8,
        "title": "Existing Content Audit",
        "skills": ["content_audit"],
        "roles": ["seo_strategist", "on_page_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 9,
        "title": "New Page & Content Planning",
        "skills": ["content_planning"],
        "roles": ["seo_strategist", "content_seo_specialist", "on_page_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 10,
        "title": "Content Briefing & Production",
        "skills": ["content_brief", "create_content"],
        "roles": ["content_seo_specialist", "on_page_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 11,
        "title": "On-Page SEO",
        "skills": ["on_page_seo", "schema_markup", "internal_linking"],
        "roles": ["on_page_seo_specialist", "structured_data_specialist", "technical_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 12,
        "title": "Publishing & Indexation",
        "skills": ["platform_publish"],
        "roles": ["seo_strategist", "on_page_seo_specialist"],
        "implemented": True,
    },
    {
        "phase": 13,
        "title": "Local SEO",
        "skills": [],
        "roles": [],
        "implemented": False,
    },
    {
        "phase": 14,
        "title": "Authority, Links & Digital PR",
        "skills": [],
        "roles": [],
        "implemented": False,
    },
    {
        "phase": 15,
        "title": "Conversion & UX Optimization",
        "skills": [],
        "roles": [],
        "implemented": False,
    },
    {
        "phase": 16,
        "title": "Reporting & Measurement",
        "skills": ["unified_report"],
        "roles": ["seo_qa_lead"],
        "implemented": False,
    },
    {
        "phase": 17,
        "title": "Continuous SEO Improvement",
        "skills": ["seo_check", "ai_visibility"],
        "roles": ["seo_qa_lead", "seo_strategist"],
        "implemented": False,
    },
]

# Runtime agent keys (Phases 1–12 + gate) — RolePermission.agent_key values
PHASE_AGENTS = (
    "discovery_agent",
    "tracking_access_agent",
    "website_situation_agent",
    "competitor_market_agent",
    "readiness_gate",
    "search_demand",
    "content_strategy",
    "site_architecture",
    "technical_seo",
    "content_audit",
    "content_planning",
    "content_production",
    "on_page_seo",
    "publishing",
)

AGENT_LABELS = {
    "discovery_agent": "Discovery",
    "tracking_access_agent": "Tracking & access",
    "website_situation_agent": "Website situation",
    "competitor_market_agent": "Competitor analysis",
    "readiness_gate": "Readiness score",
    "search_demand": "Search demand & keywords",
    "content_strategy": "Content strategy",
    "site_architecture": "Site architecture & IA",
    "technical_seo": "Technical SEO",
    "content_audit": "Existing content audit",
    "content_planning": "Content planning",
    "content_production": "Content briefing & production",
    "on_page_seo": "On-page SEO",
    "publishing": "Publishing & indexation",
}

# Base 6 SearchFit roles from TR_SEO_Architecture_v1_9 (excludes CSM / HoD)
SEARCHFIT_BASE_ROLES: list[str] = [
    "seo_strategist",
    "content_seo_specialist",
    "technical_seo_specialist",
    "on_page_seo_specialist",
    "structured_data_specialist",
    "seo_qa_lead",
]

# ---------------------------------------------------------------------------
# Skill → role ownership (source of truth)
#
# Authority:
#   - SearchFit SEO_Skills_Role_Coverage Details_v1.0.html  (phase → owning role)
#   - docs/architecture/TR_SEO_Architecture_v1_9.html  (skill assignment by role + common skills)
#
# Wire this when adding a skill. Permissions + card required_role derive from it.
# agent_key = RolePermission key (sub-skills may share a parent agent).
# ---------------------------------------------------------------------------
SKILL_ROLE_OWNERS: dict[str, dict[str, Any]] = {
    # --- TR workflows (Coverage Phases 1–3) ---
    "discovery_agent": {
        "label": "Discovery (D1–D5)",
        "agent_key": "discovery_agent",
        "owner": "client_success_manager",
        "trigger": ["client_success_manager"],
        "approve": ["client_success_manager"],
        "source": "Coverage P1 — Client Success Manager",
    },
    "tracking_access_agent": {
        "label": "Tracking & access (T1–T6)",
        "agent_key": "tracking_access_agent",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist", "client_success_manager"],  # CSM: T5
        "approve": ["technical_seo_specialist"],  # T6
        "source": "Coverage P2 — Tech SEO + CSM",
    },
    "website_situation_agent": {
        "label": "Website situation analysis",
        "agent_key": "website_situation_agent",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Coverage P3 — Technical SEO Specialist (SEO Audit lane)",
    },
    # --- Architecture v1.9: Competitor Research = common skill (all 6 roles) ---
    "competitor_market_agent": {
        "label": "Competitor Research",
        "agent_key": "competitor_market_agent",
        "owner": "seo_strategist",  # CDP card label only — not an exclusive gate
        "trigger": list(SEARCHFIT_BASE_ROLES),
        "approve": list(SEARCHFIT_BASE_ROLES),
        "shared": True,
        "source": "Architecture v1.9 common skill; Coverage P4 Shared — all roles",
    },
    "readiness_gate": {
        "label": "Readiness score",
        "agent_key": "readiness_gate",
        "owner": "seo_qa_lead",
        "trigger": ["seo_qa_lead"],
        "approve": ["seo_qa_lead"],
        "source": "TRD / Architecture — SEO QA / Reporting Lead",
    },
    # --- Phase 5 (Coverage + Architecture: Content SEO) ---
    "search_demand": {
        "label": "Search demand & keyword research",
        "agent_key": "search_demand",
        "owner": "content_seo_specialist",
        "trigger": ["content_seo_specialist", "head_of_department"],
        "approve": ["content_seo_specialist", "head_of_department"],
        "source": "Coverage P5 — Content SEO Specialist (+ HoD full access)",
    },
    "create_topic": {
        "label": "Create Topic",
        "agent_key": "search_demand",
        "owner": "content_seo_specialist",
        "trigger": ["content_seo_specialist"],
        "approve": ["content_seo_specialist"],
        "source": "After sitemap classification — new clusters only",
    },
    "keyword_clustering": {
        "label": "Keyword Clustering",
        "agent_key": "search_demand",
        "owner": "content_seo_specialist",
        "trigger": ["content_seo_specialist"],
        "approve": ["content_seo_specialist"],
        "source": "Architecture v1.9 — Content SEO Specialist",
    },
    # --- Phase 6 (Coverage: Strategist; Architecture: Content Strategy shared w/ On-Page) ---
    "content_strategy": {
        "label": "Content Strategy",
        "agent_key": "content_strategy",
        "owner": "seo_strategist",
        "trigger": ["seo_strategist", "on_page_seo_specialist"],
        "approve": ["seo_strategist"],
        "shared": True,
        "source": "Coverage P6 Strategist; Architecture v1.9 shared with On-Page",
    },
    # Fills Coverage P6 gap ("site-structure/URL/navigation has no dedicated skill")
    "site_architecture": {
        "label": "Site Architecture & IA",
        "agent_key": "site_architecture",
        "owner": "seo_strategist",
        "trigger": ["seo_strategist"],
        "approve": ["seo_strategist"],
        "source": "Coverage P6 owning role (Strategist) — IA gap fill",
    },
    # --- Catalog skills not yet runtime agents (Architecture v1.9 columns) ---
    "seo_audit": {
        "label": "SEO Audit",
        "agent_key": "technical_seo",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Architecture v1.9 — Technical SEO Specialist (Phase 7)",
    },
    "technical_seo": {
        "label": "Technical SEO",
        "agent_key": "technical_seo",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Coverage P7 — Technical SEO Specialist",
    },
    "broken_links": {
        "label": "Broken Links",
        "agent_key": "technical_seo",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Architecture v1.9 — Technical SEO Specialist (Phase 7)",
    },
    "rendering_audit": {
        "label": "Rendering Audit",
        "agent_key": "technical_seo",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Phase 7 companion — handoff from technical SEO (scripts/render_diff.py)",
    },
    "cwv_measurement": {
        "label": "CWV Measurement",
        "agent_key": "technical_seo",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Phase 7 companion — handoff from technical SEO (scripts/cwv_report.py)",
    },
    "hreflang_validator": {
        "label": "hreflang Validator",
        "agent_key": "technical_seo",
        "owner": "technical_seo_specialist",
        "trigger": ["technical_seo_specialist"],
        "approve": ["technical_seo_specialist"],
        "source": "Phase 7 companion — handoff from technical SEO (scripts/hreflang_check.py)",
    },
    "content_audit": {
        "label": "Existing Content Audit",
        "agent_key": "content_audit",
        "owner": "seo_strategist",
        "trigger": ["seo_strategist", "on_page_seo_specialist"],
        "approve": ["seo_strategist"],
        "shared": True,
        "source": "Coverage P8 — Strategist / On-Page",
    },
    "content_planning": {
        "label": "New Page & Content Planning",
        "agent_key": "content_planning",
        "owner": "seo_strategist",
        "trigger": ["seo_strategist", "content_seo_specialist", "on_page_seo_specialist"],
        "approve": ["seo_strategist"],
        "shared": True,
        "source": "Coverage P9 — Strategist / Content / On-Page",
    },
    "content_brief": {
        "label": "Content Brief",
        "agent_key": "content_production",
        "owner": "content_seo_specialist",
        "trigger": ["content_seo_specialist", "on_page_seo_specialist"],
        "approve": ["content_seo_specialist"],
        "source": "Coverage P10 — Content SEO Specialist",
    },
    "create_content": {
        "label": "Create Content",
        "agent_key": "content_production",
        "owner": "content_seo_specialist",
        "trigger": ["content_seo_specialist", "on_page_seo_specialist"],
        "approve": ["content_seo_specialist", "on_page_seo_specialist"],
        "shared": True,
        "source": "Architecture v1.9 — shared Content SEO + On-Page",
    },
    "content_production": {
        "label": "Content Briefing & Production",
        "agent_key": "content_production",
        "owner": "content_seo_specialist",
        "trigger": ["content_seo_specialist", "on_page_seo_specialist"],
        "approve": ["content_seo_specialist"],
        "source": "Coverage P10 — Content SEO Specialist",
    },
    "on_page_seo": {
        "label": "On-Page SEO",
        "agent_key": "on_page_seo",
        "owner": "on_page_seo_specialist",
        "trigger": [
            "on_page_seo_specialist",
            "structured_data_specialist",
            "technical_seo_specialist",
        ],
        "approve": [
            "on_page_seo_specialist",
            "structured_data_specialist",
        ],
        "shared": True,
        "source": "Coverage P11 — On-Page / Schema / Tech",
    },
    "internal_linking": {
        "label": "Internal Linking",
        "agent_key": "on_page_seo",
        "owner": "on_page_seo_specialist",
        "trigger": ["on_page_seo_specialist", "technical_seo_specialist"],
        "approve": ["on_page_seo_specialist", "technical_seo_specialist"],
        "shared": True,
        "source": "Architecture v1.9 + Coverage P11 — On-Page + Technical shared",
    },
    "schema_markup": {
        "label": "Schema Markup",
        "agent_key": "on_page_seo",
        "owner": "structured_data_specialist",
        "trigger": ["structured_data_specialist", "on_page_seo_specialist"],
        "approve": ["structured_data_specialist", "on_page_seo_specialist"],
        "shared": True,
        "source": "Architecture v1.9 — Structured Data Specialist (Phase 11)",
    },
    "generate_schema": {
        "label": "Generate Schema",
        "agent_key": "on_page_seo",
        "owner": "structured_data_specialist",
        "trigger": ["structured_data_specialist"],
        "approve": ["structured_data_specialist"],
        "source": "Architecture v1.9 — Structured Data Specialist",
    },
    "platform_publish": {
        "label": "Platform publish + IndexNow",
        "agent_key": "publishing",
        "owner": "seo_strategist",
        # v1.9 step 13: final publish authority is lead-only — Senior SEO Strategist
        # (Tier A) and Content & On-Page Lead (content changes). Everyone else may
        # approve into the queue but "cannot trigger this step themselves".
        "trigger": ["seo_strategist", "on_page_seo_specialist"],
        "approve": ["seo_strategist", "on_page_seo_specialist", "seo_qa_lead"],
        "shared": True,
        "source": "Coverage P12 + Architecture v1.9 — lead-only publish, QA triages queue",
    },
    "publishing": {
        "label": "Publishing & Indexation",
        "agent_key": "publishing",
        "owner": "seo_strategist",
        "trigger": ["seo_strategist", "on_page_seo_specialist"],
        "approve": ["seo_strategist", "on_page_seo_specialist"],
        "shared": True,
        "source": "Coverage P12 — Strategist + On-Page",
    },
    "seo_check": {
        "label": "SEO Check",
        "agent_key": "seo_check",
        "owner": "seo_qa_lead",
        "trigger": ["seo_qa_lead"],
        "approve": ["seo_qa_lead"],
        "implemented": False,
        "source": "Architecture v1.9 — SEO QA / Reporting Lead",
    },
    "ai_visibility": {
        "label": "AI Visibility (GEO)",
        "agent_key": "ai_visibility",
        "owner": "seo_strategist",
        "trigger": ["seo_strategist", "seo_qa_lead"],
        "approve": ["seo_strategist", "seo_qa_lead"],
        "shared": True,
        "implemented": False,
        "source": "Architecture v1.9 + Coverage P17 — Strategist + QA shared",
    },
    "unified_report": {
        "label": "Unified report synthesis",
        "agent_key": "unified_report",
        "owner": "seo_qa_lead",
        "trigger": ["seo_qa_lead"],
        "approve": ["seo_qa_lead"],
        "implemented": False,
        "source": "Coverage P16 — SEO QA / Reporting Lead",
    },
}


def required_role_for(skill_key: str) -> str:
    """Card / sign-off role for a skill or agent key (owner)."""
    meta = SKILL_ROLE_OWNERS.get(skill_key)
    if meta:
        return str(meta["owner"])
    for meta in SKILL_ROLE_OWNERS.values():
        if meta.get("agent_key") == skill_key:
            return str(meta["owner"])
    return "head_of_department"


def _build_role_phase_permissions() -> dict[str, list[tuple[str, bool, bool]]]:
    """Derive RolePermission rows from SKILL_ROLE_OWNERS (+ HoD full access)."""
    agent_keys = list(PHASE_AGENTS)
    by_agent: dict[str, dict[str, set[str]]] = {
        a: {"trigger": set(), "approve": set()} for a in agent_keys
    }
    for meta in SKILL_ROLE_OWNERS.values():
        agent = str(meta["agent_key"])
        if agent not in by_agent:
            continue
        by_agent[agent]["trigger"].update(meta.get("trigger") or [])
        by_agent[agent]["approve"].update(meta.get("approve") or [])

    role_names = [r["name"] for r in SEO_ROLES]
    out: dict[str, list[tuple[str, bool, bool]]] = {}
    for role_name in role_names:
        rows: list[tuple[str, bool, bool]] = []
        for agent in agent_keys:
            if role_name == "head_of_department":
                rows.append((agent, True, True))
            else:
                rows.append(
                    (
                        agent,
                        role_name in by_agent[agent]["trigger"],
                        role_name in by_agent[agent]["approve"],
                    )
                )
        out[role_name] = rows
    return out


# (can_trigger, can_approve) — derived from SKILL_ROLE_OWNERS
ROLE_PHASE_PERMISSIONS: dict[str, list[tuple[str, bool, bool]]] = _build_role_phase_permissions()


def role_label(role_name: str) -> str:
    for r in SEO_ROLES:
        if r["name"] == role_name:
            return r["label"]
    return role_name.replace("_", " ").title()


def permissions_for_role(role_name: str) -> list[dict[str, Any]]:
    rows = ROLE_PHASE_PERMISSIONS.get(role_name, [])
    out = []
    for agent_key, can_trigger, can_approve in rows:
        out.append(
            {
                "agent_key": agent_key,
                "label": AGENT_LABELS.get(agent_key, agent_key),
                "can_trigger": can_trigger,
                "can_approve": can_approve,
                "implemented": agent_key in PHASE_AGENTS,
            }
        )
    return out


def triggerable_agents(role_name: str) -> list[str]:
    return [
        a
        for a, t, _ in ROLE_PHASE_PERMISSIONS.get(role_name, [])
        if t
    ]


def build_playground_sections(
    role_name: str,
    *,
    client_name: str,
    industry: str | None,
    profile: Any,
) -> list[dict[str, Any]]:
    """Shared Client Digital Profile memory, filtered to what this role needs from others."""
    commercial = dict(getattr(profile, "commercial_scope", None) or {})
    marketing = dict(getattr(profile, "marketing_context", None) or {})
    tracking = dict(getattr(profile, "tracking_baseline", None) or {})
    website = dict(getattr(profile, "website_situation_summary", None) or {})
    competitive = dict(getattr(profile, "competitive_landscape_summary", None) or {})
    search_demand = dict(getattr(profile, "search_demand_summary", None) or {})
    seo_strategy = dict(getattr(profile, "seo_strategy_summary", None) or {})
    site_architecture = dict(getattr(profile, "site_architecture_summary", None) or {})
    technical_seo = dict(getattr(profile, "technical_seo_summary", None) or {})
    content_audit = dict(getattr(profile, "content_audit_summary", None) or {})
    content_planning = dict(getattr(profile, "content_planning_summary", None) or {})
    content_production = dict(getattr(profile, "content_production_summary", None) or {})
    on_page_seo = dict(getattr(profile, "on_page_seo_summary", None) or {})
    publishing = dict(getattr(profile, "publishing_summary", None) or {})
    intake = dict(marketing.get("client_intake") or {})

    phase_labels = {
        "not_started": "Not started",
        "in_progress": "In progress",
        "pending_signoff": "Awaiting sign-off",
        "complete": "Done",
    }

    def phase_status(raw: str | None) -> str:
        key = (raw or "not_started").strip()
        return phase_labels.get(key, key.replace("_", " ").capitalize())

    def access_status(raw: str | None) -> str:
        labels = {
            "connected": "Connected",
            "not_connected": "Not connected",
            "pending_setup": "Needs setup",
            "pass": "Pass",
            "warning": "Needs review",
            "fail": "Failed",
            "blocked": "Blocked",
        }
        key = (raw or "").strip()
        return labels.get(key, key.replace("_", " ").capitalize() or "—")

    def present_platforms(raw: Any) -> list[dict[str, str]] | None:
        if not isinstance(raw, list) or not raw:
            return None
        out: list[dict[str, str]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or row.get("key") or "System")
            status = access_status(str(row.get("status") or ""))
            detail = str(row.get("detail") or "").strip()
            item: dict[str, str] = {"system": label, "status": status}
            if detail:
                item["note"] = detail
            out.append(item)
        return out or None

    def present_discovery() -> dict[str, Any]:
        pack: dict[str, Any] = {}
        b2b = commercial.get("b2b_b2c") or intake.get("b2b_b2c")
        if b2b:
            pack["commercial_scope"] = b2b
        products = (
            commercial.get("products")
            or marketing.get("products")
            or intake.get("products")
        )
        if products:
            pack["products"] = products
        objectives = marketing.get("objectives")
        if objectives:
            pack["objectives"] = objectives
        positioning = commercial.get("positioning") or marketing.get("positioning")
        if positioning:
            pack["positioning"] = positioning
        geo = commercial.get("geographic_focus") or intake.get("geographic_focus")
        if geo:
            pack["geographic_focus"] = geo
        model = commercial.get("business_model") or intake.get("business_model")
        if model:
            pack["business_model"] = model
        comps = marketing.get("competitors") or commercial.get("competitors")
        if comps:
            pack["competitors"] = comps
        goal = commercial.get("business_goal") or intake.get("business_goal")
        if goal:
            pack["business_goal"] = goal
        return pack

    def present_tracking() -> dict[str, Any]:
        pack: dict[str, Any] = {}
        platforms = present_platforms(
            tracking.get("system_access") or tracking.get("t1_platforms")
        )
        if platforms:
            pack["system_access"] = platforms
        hist = tracking.get("historical_baseline") or tracking.get("historical_draft")
        if isinstance(hist, dict) and hist:
            clean = {
                k: v
                for k, v in hist.items()
                if k not in ("mode", "raw", "events", "payload") and v not in (None, "", [], {})
            }
            # Prefer friendly fields
            friendly: dict[str, Any] = {}
            if clean.get("title"):
                friendly["summary"] = clean["title"]
            if clean.get("note"):
                friendly["note"] = clean["note"]
            if clean.get("status"):
                friendly["status"] = access_status(str(clean["status"]))
            if clean.get("access"):
                friendly["access"] = clean["access"]
            if clean.get("client"):
                friendly["client"] = clean["client"]
            pack["historical_baseline"] = friendly or clean
        known = tracking.get("known_changes")
        if known:
            pack["known_changes"] = known
        return pack

    def present_website(raw: dict[str, Any]) -> dict[str, Any]:
        if not raw:
            return {}
        skip = {"audit_ids", "raw", "tabs_run", "agent_key", "actions", "required_role", "_draft"}
        out: dict[str, Any] = {}
        # Top-level crawl highlights for playground
        if raw.get("pages_found") is not None:
            out["pages_found"] = raw.get("pages_found")
        if raw.get("indexable") is not None:
            out["indexable"] = raw.get("indexable")
        if raw.get("broken_links") is not None:
            out["broken_links"] = raw.get("broken_links")
        if raw.get("note"):
            out["note"] = raw.get("note")
        if raw.get("sample_urls"):
            out["sample_urls"] = raw.get("sample_urls")
        if raw.get("site_sitemap"):
            sm = raw.get("site_sitemap") or {}
            out["site_sitemap"] = {
                "url_count": sm.get("url_count"),
                "shown_count": sm.get("shown_count"),
                "sources": sm.get("sources"),
                "truncated": sm.get("truncated"),
                "sections": [
                    {
                        "cluster": s.get("cluster"),
                        "label": s.get("label"),
                        "count": s.get("count"),
                        "pages": [
                            {
                                "path": p.get("path"),
                                "url": p.get("url"),
                                "title": p.get("title"),
                                "status": p.get("status"),
                                "cluster": p.get("cluster"),
                            }
                            for p in (s.get("pages") or [])[:40]
                            if isinstance(p, dict)
                        ],
                    }
                    for s in (sm.get("sections") or [])[:12]
                    if isinstance(s, dict)
                ],
            }
            out["sitemap_url_count"] = raw.get("sitemap_url_count") or sm.get("url_count")
        if raw.get("_draft"):
            out["sign_off"] = "Draft — awaiting Technical SEO approve"

        for k, v in raw.items():
            if k in skip or v in (None, "", [], {}):
                continue
            if k in (
                "pages_found",
                "indexable",
                "broken_links",
                "source",
                "note",
                "sample_urls",
                "severity",
                "crawl_source",
            ):
                continue
            if k == "crawl_technical" and isinstance(v, dict):
                summary = v.get("summary") if isinstance(v.get("summary"), dict) else v
                if isinstance(summary, dict):
                    out["crawl"] = {
                        "pages_found": summary.get("pages_found"),
                        "indexable": summary.get("indexable"),
                        "broken_links": summary.get("broken_links"),
                        "canonical_issues": summary.get("canonical_issues"),
                        "note": summary.get("note"),
                    }
                    samples = summary.get("status_samples") or summary.get("discovered_urls") or []
                    if samples and "sample_urls" not in out:
                        if isinstance(samples[0], dict):
                            out["sample_urls"] = [s.get("url") for s in samples[:12] if s.get("url")]
                        else:
                            out["sample_urls"] = samples[:12]
                continue
            if isinstance(v, dict) and "summary" in v:
                out[k.replace("_", " ")] = {
                    "severity": v.get("severity"),
                    "status": v.get("status"),
                    **(
                        {
                            sk: sv
                            for sk, sv in (v.get("summary") or {}).items()
                            if sk
                            in (
                                "pages_found",
                                "broken_count",
                                "domain_rating",
                                "referring_domains",
                                "drop_percent",
                                "message",
                                "note",
                            )
                            and sv not in (None, "", [], {})
                        }
                        if isinstance(v.get("summary"), dict)
                        else {}
                    ),
                }
            else:
                out[k.replace("_", " ")] = v
        return out

    def present_tech_bundle() -> dict[str, Any] | None:
        t = present_tracking()
        w = present_website(website)
        if not t and not w:
            return None
        body: dict[str, Any] = {}
        if t:
            body["tracking"] = t
        if w:
            body["website"] = w
        return body

    statuses = {
        "Discovery": phase_status(getattr(profile, "discovery_status", None)),
        "Tracking": phase_status(getattr(profile, "tracking_status", None)),
        "Website": phase_status(getattr(profile, "website_status", None)),
        "Competitors": phase_status(getattr(profile, "competitor_status", None)),
        "Search demand": phase_status(getattr(profile, "search_demand_status", None)),
        "SEO strategy": phase_status(getattr(profile, "seo_strategy_status", None)),
        "Site architecture": phase_status(getattr(profile, "site_architecture_status", None)),
        "Technical SEO": phase_status(getattr(profile, "technical_seo_status", None)),
        "Content audit": phase_status(getattr(profile, "content_audit_status", None)),
        "Content planning": phase_status(getattr(profile, "content_planning_status", None)),
        "Content production": phase_status(getattr(profile, "content_production_status", None)),
        "On-page SEO": phase_status(getattr(profile, "on_page_seo_status", None)),
        "Publishing": phase_status(getattr(profile, "publishing_status", None)),
    }

    def present_search_demand() -> dict[str, Any]:
        """Shared memory: essentials only. Full report lives on the chat card."""
        if not search_demand:
            return {}
        return slim_search_demand_memory(search_demand)

    def present_competitors() -> dict[str, Any]:
        if not competitive:
            return {}
        return slim_competitor_memory(competitive)

    def present_seo_strategy() -> dict[str, Any]:
        if not seo_strategy:
            return {}
        return slim_seo_strategy_memory(seo_strategy)

    def present_site_architecture() -> dict[str, Any]:
        if not site_architecture:
            return {}
        return slim_site_architecture_memory(site_architecture)

    def present_technical_seo() -> dict[str, Any]:
        if not technical_seo:
            return {}
        return slim_technical_seo_memory(technical_seo)

    def present_content_audit() -> dict[str, Any]:
        if not content_audit:
            return {}
        return slim_content_audit_memory(content_audit)

    def present_content_planning() -> dict[str, Any]:
        if not content_planning:
            return {}
        return slim_content_planning_memory(content_planning)

    def present_content_production() -> dict[str, Any]:
        if not content_production:
            return {}
        return slim_content_production_memory(content_production)

    def present_on_page_seo() -> dict[str, Any]:
        if not on_page_seo:
            return {}
        return slim_on_page_seo_memory(on_page_seo)

    def present_publishing() -> dict[str, Any]:
        if not publishing:
            return {}
        return slim_publishing_memory(publishing)

    def section(
        title: str,
        source_role: str,
        body: Any,
        *,
        empty: str,
        report_kind: str | None = None,
    ) -> dict[str, Any]:
        clean = body
        if isinstance(body, dict):
            clean = {
                k: v
                for k, v in body.items()
                if k != "_draft" and not str(k).startswith("_")
            }
        has = bool(clean) and clean not in ({}, [], None, "")
        kind = report_kind
        if not kind:
            tl = title.lower()
            if "discovery" in tl:
                kind = "discovery"
            elif "tracking" in tl:
                kind = "tracking"
            elif "website" in tl:
                kind = "website"
            elif "competitor" in tl:
                kind = "competitors"
            elif "search demand" in tl or "keyword" in tl:
                kind = "search_demand"
            elif "site architecture" in tl or (
                "architecture" in tl and "strategy" not in tl
            ):
                kind = "site_architecture"
            elif "technical seo" in tl:
                kind = "technical_seo"
            elif "content audit" in tl:
                kind = "content_audit"
            elif "content planning" in tl or "roadmap" in tl:
                kind = "content_planning"
            elif "content production" in tl or "brief" in tl:
                kind = "content_production"
            elif "on-page" in tl or "on page" in tl:
                kind = "on_page_seo"
            elif "publish" in tl:
                kind = "publishing"
            elif "strategy" in tl or "content strategy" in tl:
                kind = "seo_strategy"
            else:
                kind = "generic"
        return {
            "title": title,
            "source_role": source_role,
            "source_label": role_label(source_role),
            "status": "ready" if has else "waiting",
            "empty_hint": empty,
            "data": clean if has else None,
            "report_kind": kind,
        }

    readiness = float(getattr(profile, "overall_readiness_score", None) or 0)

    # Common header context
    sections: list[dict[str, Any]] = [
        {
            "title": "Client",
            "source_role": "shared",
            "source_label": "Shared memory",
            "status": "ready",
            "empty_hint": "",
            "data": {
                "name": client_name,
                "industry": industry or "—",
                "phase_statuses": statuses,
                "readiness": f"{round(readiness)}%",
            },
        }
    ]

    discovery_pack = present_discovery()
    tracking_pack = present_tracking()
    website_pack = present_website(website)
    tech_bundle = present_tech_bundle()

    if role_name == "client_success_manager":
        sections.append(
            section(
                "Your lane — Discovery outputs",
                "client_success_manager",
                discovery_pack,
                empty="Run Discovery to publish commercial scope into shared memory.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Tracking baseline",
                "technical_seo_specialist",
                tracking_pack,
                empty="Waiting on Tracking (T1–T6). You can help with T5 known-changes.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Website situation",
                "technical_seo_specialist",
                website_pack,
                empty="Website situation not signed off yet.",
            )
        )

    elif role_name in ("seo_qa_lead", "head_of_department"):
        if role_name == "head_of_department":
            sections.append(
                {
                    "title": "Department overview",
                    "source_role": "head_of_department",
                    "source_label": role_label("head_of_department"),
                    "status": "ready",
                    "empty_hint": "",
                    "data": {
                        "access": "Full trigger + approve on all Phase 1–12 skills and readiness",
                        "phases_mapped": [
                            p["title"]
                            for p in PHASE_SKILL_COVERAGE
                            if p.get("implemented") or p.get("skills")
                        ],
                    },
                }
            )
        sections.append(
            section(
                "From CSM — Discovery",
                "client_success_manager",
                discovery_pack,
                empty="Run Discovery to fill commercial scope, products, and positioning.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Tracking",
                "technical_seo_specialist",
                tracking_pack,
                empty="Run Tracking check to publish GA4 / GSC / GTM access into shared memory.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Website situation",
                "technical_seo_specialist",
                website_pack,
                empty="Run Website situation to crawl the site and publish technical memory.",
            )
        )
        sections.append(
            section(
                "From Strategist — Competitors",
                "seo_strategist",
                present_competitors(),
                empty="Run Competitor analysis to publish the tiered landscape.",
            )
        )
        sections.append(
            section(
                "From Content SEO — Search demand",
                "content_seo_specialist",
                present_search_demand(),
                empty="Run Search demand for topics, clusters, and best opportunities.",
            )
        )
        sections.append(
            section(
                "From Strategist — SEO strategy & IA",
                "seo_strategist",
                present_seo_strategy(),
                empty="After Phase 5 approval: run Content strategy for pillars and topic priorities.",
            )
        )
        sections.append(
            section(
                "From Strategist — Site architecture",
                "seo_strategist",
                present_site_architecture(),
                empty="After clusters exist: Strategist runs Site architecture for click-depth audit + URL tree.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Technical SEO",
                "technical_seo_specialist",
                present_technical_seo(),
                empty="Phase 7 technical audit not signed off yet.",
            )
        )
        sections.append(
            section(
                "From Strategist — Content audit",
                "seo_strategist",
                present_content_audit(),
                empty="Phase 8 content audit not ready.",
            )
        )
        sections.append(
            section(
                "From Strategist — Content planning",
                "seo_strategist",
                present_content_planning(),
                empty="Phase 9 locked roadmap not ready.",
            )
        )
        sections.append(
            section(
                "From Content SEO — Content production",
                "content_seo_specialist",
                present_content_production(),
                empty="Phase 10 briefs / drafts not ready.",
            )
        )
        sections.append(
            section(
                "From On-Page — On-page SEO",
                "on_page_seo_specialist",
                present_on_page_seo(),
                empty="Phase 11 on-page package not ready.",
            )
        )
        sections.append(
            section(
                "From Strategist — Publishing",
                "seo_strategist",
                present_publishing(),
                empty="Phase 12 publish package not ready.",
            )
        )

    elif role_name == "technical_seo_specialist":
        sections.append(
            section(
                "From CSM — Commercial & marketing context",
                "client_success_manager",
                discovery_pack,
                empty="Discovery not approved yet — commercial context missing for audits.",
            )
        )
        sections.append(
            section(
                "Your lane — Tracking baseline",
                "technical_seo_specialist",
                tracking_pack,
                empty="Run Tracking check, capture known-changes, then approve T6.",
            )
        )
        sections.append(
            section(
                "Your lane — Website situation",
                "technical_seo_specialist",
                website_pack,
                empty="Run website situation analysis after tracking is trustworthy.",
            )
        )
        sections.append(
            section(
                "Upstream — Site architecture (Strategist)",
                "seo_strategist",
                present_site_architecture(),
                empty="Strategist owns IA blueprint; Phase 7 receives redirect/robots/facet handoffs.",
            )
        )
        sections.append(
            section(
                "Your lane — Technical SEO",
                "technical_seo_specialist",
                present_technical_seo(),
                empty="Run Technical SEO after IA handoffs for audit + broken links + backlog.",
            )
        )
        sections.append(
            section(
                "Downstream — On-page package",
                "on_page_seo_specialist",
                present_on_page_seo(),
                empty="On-Page package appears after Phase 11.",
            )
        )

    elif role_name == "seo_strategist":
        sections.append(
            section(
                "From CSM — Discovery profile",
                "client_success_manager",
                discovery_pack,
                empty="Need Discovery sign-off before competitive framing is complete.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Tracking & site",
                "technical_seo_specialist",
                tech_bundle,
                empty="Tracking/website memory empty — competitor scan can still run but context is thin.",
            )
        )
        sections.append(
            section(
                "Your lane — Competitive landscape",
                "seo_strategist",
                present_competitors(),
                empty="Run competitor analysis to publish the tiered landscape.",
            )
        )
        sections.append(
            section(
                "From Content SEO — Search demand",
                "content_seo_specialist",
                present_search_demand(),
                empty="Waiting on Phase 5 keyword research.",
            )
        )
        sections.append(
            section(
                "Your lane — SEO strategy & IA",
                "seo_strategist",
                present_seo_strategy(),
                empty="After Phase 5 approval, run content strategy to publish pillars and URL/IA.",
            )
        )
        sections.append(
            section(
                "Your lane — Site architecture",
                "seo_strategist",
                present_site_architecture(),
                empty="Run and approve site architecture after clusters exist.",
            )
        )
        sections.append(
            section(
                "From Technical SEO — Technical SEO",
                "technical_seo_specialist",
                present_technical_seo(),
                empty="Waiting on Phase 7 technical sign-off.",
            )
        )
        sections.append(
            section(
                "Your lane — Content audit",
                "seo_strategist",
                present_content_audit(),
                empty="Run Existing Content Audit after IA/strategy context exists.",
            )
        )
        sections.append(
            section(
                "Your lane — Content planning",
                "seo_strategist",
                present_content_planning(),
                empty="Run Content Planning after IA + approved strategy — briefs pick up from the queue.",
            )
        )
        sections.append(
            section(
                "Your lane — Publishing",
                "seo_strategist",
                present_publishing(),
                empty="Mock publish package after On-Page approve.",
            )
        )

    elif role_name == "content_seo_specialist":
        sections.append(
            section(
                "Upstream — Discovery (CSM)",
                "client_success_manager",
                discovery_pack,
                empty="No Discovery memory yet.",
            )
        )
        sections.append(
            section(
                "Upstream — Tracking (Tech SEO)",
                "technical_seo_specialist",
                tracking_pack,
                empty="Waiting on Tracking sign-off.",
            )
        )
        sections.append(
            section(
                "Upstream — Website (Tech SEO)",
                "technical_seo_specialist",
                website_pack,
                empty="Waiting on website situation.",
            )
        )
        sections.append(
            section(
                "Upstream — Competitors (Strategist)",
                "seo_strategist",
                present_competitors(),
                empty="No competitive landscape yet.",
            )
        )
        sections.append(
            section(
                "Your lane — Search demand & keywords",
                "content_seo_specialist",
                present_search_demand(),
                empty="Run Search demand (topics + clustering via Ahrefs/DataForSEO).",
            )
        )
        sections.append(
            section(
                "From Strategist — SEO strategy",
                "seo_strategist",
                present_seo_strategy(),
                empty="Strategist publishes pillars/IA after your Phase 5 sign-off.",
            )
        )
        sections.append(
            section(
                "Upstream — Site architecture",
                "seo_strategist",
                present_site_architecture(),
                empty="URL/parent/depth must be fixed before the Phase 9 roadmap merge.",
            )
        )
        sections.append(
            section(
                "Upstream — Technical SEO",
                "technical_seo_specialist",
                present_technical_seo(),
                empty="Waiting on Phase 7 technical sign-off.",
            )
        )
        sections.append(
            section(
                "Upstream — Content audit",
                "seo_strategist",
                present_content_audit(),
                empty="Waiting on Phase 8 content audit.",
            )
        )
        sections.append(
            section(
                "Upstream — Content planning",
                "seo_strategist",
                present_content_planning(),
                empty="Need locked roadmap before briefs.",
            )
        )
        sections.append(
            section(
                "Your lane — Content production",
                "content_seo_specialist",
                present_content_production(),
                empty="Run Content Production for briefs + draft stubs.",
            )
        )
        sections.append(
            section(
                "Downstream — On-page SEO",
                "on_page_seo_specialist",
                present_on_page_seo(),
                empty="On-Page package appears after Phase 11.",
            )
        )
        sections.append(
            section(
                "Downstream — Publishing",
                "seo_strategist",
                present_publishing(),
                empty="Mock publish after On-Page approve.",
            )
        )

    else:
        # On-page / Structured — view shared upstream memory
        sections.append(
            section(
                "Upstream — Discovery (CSM)",
                "client_success_manager",
                discovery_pack,
                empty="No Discovery memory yet.",
            )
        )
        sections.append(
            section(
                "Upstream — Tracking & website (Tech SEO)",
                "technical_seo_specialist",
                tech_bundle,
                empty="No tracking/website memory yet.",
            )
        )
        sections.append(
            section(
                "Upstream — Competitors (Strategist)",
                "seo_strategist",
                present_competitors(),
                empty="No competitive landscape yet.",
            )
        )
        sections.append(
            section(
                "Upstream — Search demand",
                "content_seo_specialist",
                present_search_demand(),
                empty="No Phase 5 keyword memory yet.",
            )
        )
        sections.append(
            section(
                "Upstream — Site architecture",
                "seo_strategist",
                present_site_architecture(),
                empty="No IA blueprint yet.",
            )
        )
        sections.append(
            section(
                "Upstream — Content audit",
                "seo_strategist",
                present_content_audit(),
                empty="Content audit not ready.",
            )
        )
        sections.append(
            section(
                "Upstream — Content planning",
                "seo_strategist",
                present_content_planning(),
                empty="Need locked roadmap before briefs.",
            )
        )
        sections.append(
            section(
                "Upstream — Content production",
                "content_seo_specialist",
                present_content_production(),
                empty="Briefs/drafts not ready.",
            )
        )
        sections.append(
            section(
                "Your lane — On-page SEO",
                "on_page_seo_specialist",
                present_on_page_seo(),
                empty="Run On-Page SEO for titles, schema, and internal links.",
            )
        )
        sections.append(
            section(
                "Your lane — Publishing",
                "seo_strategist",
                present_publishing(),
                empty="Mock publish checklist after on-page approve.",
            )
        )
        later = [
            p.get("title") or p
            for p in PHASE_SKILL_COVERAGE
            if role_name in (p.get("roles") or []) and not p.get("implemented")
        ]
        if later:
            sections.append(
                {
                    "title": "Your skills (later phases)",
                    "source_role": role_name,
                    "source_label": role_label(role_name),
                    "status": "planned",
                    "empty_hint": "Phases 13–17 skills are mapped but not enabled in this build.",
                    "data": later,
                }
            )

    return sections
