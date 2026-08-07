"""SEO role ↔ skill coverage (SearchFit Skills Role Coverage + TRD Phases 1–4).

Full catalog is defined here for all roles/skills. Runtime agents for Phases 1–4
are enforced via RolePermission rows; later-phase skills are mapped but not wired yet.
"""

from __future__ import annotations

from typing import Any

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
        "description": "Owns Competitor & Market; escalation lead across phases.",
    },
    {
        "name": "content_seo_specialist",
        "label": "Content SEO Specialist",
        "description": "Phases 5+ (topics, clustering, briefs, content) — view-only on 1–4.",
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
        "description": "Final readiness gate before Phase 5 handoff.",
    },
]

# All catalog roles can self-register (HoD included — needed for org bootstrap on Railway).
SELF_SERVICE_ROLES: set[str] = {r["name"] for r in SEO_ROLES}


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
        "roles": ["seo_strategist"],  # shared view for all; trigger/approve strategist-led
        "shared": True,
        "implemented": True,
    },
    {
        "phase": 5,
        "title": "Search Demand & Keyword Research",
        "skills": ["create_topic", "keyword_clustering"],
        "roles": ["content_seo_specialist"],
        "implemented": False,
    },
    {
        "phase": 6,
        "title": "SEO Strategy & Information Architecture",
        "skills": ["content_strategy"],
        "roles": ["seo_strategist"],
        "implemented": False,
    },
    {
        "phase": 7,
        "title": "Technical SEO",
        "skills": ["technical_seo", "seo_audit", "broken_links"],
        "roles": ["technical_seo_specialist"],
        "implemented": False,
    },
    {
        "phase": 8,
        "title": "Existing Content Audit",
        "skills": ["content_strategy"],
        "roles": ["seo_strategist"],
        "implemented": False,
    },
    {
        "phase": 9,
        "title": "New Page & Content Planning",
        "skills": ["content_strategy", "content_brief"],
        "roles": ["seo_strategist", "content_seo_specialist"],
        "implemented": False,
    },
    {
        "phase": 10,
        "title": "Content Briefing & Production",
        "skills": ["content_brief", "create_content", "content_translation"],
        "roles": ["content_seo_specialist"],
        "implemented": False,
    },
    {
        "phase": 11,
        "title": "On-Page SEO",
        "skills": ["on_page_seo", "schema_markup", "internal_linking"],
        "roles": ["on_page_seo_specialist", "structured_data_specialist"],
        "implemented": False,
    },
    {
        "phase": 12,
        "title": "Publishing & Indexation",
        "skills": ["platform_publish"],
        "roles": ["seo_strategist", "on_page_seo_specialist"],
        "implemented": False,
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
        "roles": ["seo_strategist", "seo_qa_lead"],
        "implemented": False,
    },
    {
        "phase": 17,
        "title": "Continuous SEO Improvement",
        "skills": ["seo_check", "ai_visibility"],
        "roles": ["seo_qa_lead", "technical_seo_specialist"],
        "implemented": False,
    },
]

# Runtime agent keys (Phases 1–4 + gate)
PHASE_AGENTS = (
    "discovery_agent",
    "tracking_access_agent",
    "website_situation_agent",
    "competitor_market_agent",
    "readiness_gate",
)

AGENT_LABELS = {
    "discovery_agent": "Discovery",
    "tracking_access_agent": "Tracking & access",
    "website_situation_agent": "Website situation",
    "competitor_market_agent": "Competitor analysis",
    "readiness_gate": "Readiness gate",
}

# (can_trigger, can_approve) for Phase 1–4 agents — from TRD §8 + coverage notes
# Competitor is shared for viewing; trigger+approve stays with strategist (TRD).
# CSM can trigger tracking (T5 known-changes) but Tech SEO approves (T6).
ROLE_PHASE_PERMISSIONS: dict[str, list[tuple[str, bool, bool]]] = {
    # Org lead — full trigger + approve on every Phase 1–4 skill (and readiness)
    "head_of_department": [
        ("discovery_agent", True, True),
        ("tracking_access_agent", True, True),
        ("website_situation_agent", True, True),
        ("competitor_market_agent", True, True),
        ("readiness_gate", True, True),
    ],
    "client_success_manager": [
        ("discovery_agent", True, True),
        ("tracking_access_agent", True, False),
        ("website_situation_agent", False, False),
        ("competitor_market_agent", False, False),
        ("readiness_gate", False, False),
    ],
    "technical_seo_specialist": [
        ("discovery_agent", False, False),
        ("tracking_access_agent", True, True),
        ("website_situation_agent", True, True),
        ("competitor_market_agent", False, False),
        ("readiness_gate", False, False),
    ],
    "seo_strategist": [
        ("discovery_agent", False, False),
        ("tracking_access_agent", False, False),
        ("website_situation_agent", False, False),
        ("competitor_market_agent", True, True),
        ("readiness_gate", False, False),
    ],
    "content_seo_specialist": [
        ("discovery_agent", False, False),
        ("tracking_access_agent", False, False),
        ("website_situation_agent", False, False),
        ("competitor_market_agent", False, False),
        ("readiness_gate", False, False),
    ],
    "on_page_seo_specialist": [
        ("discovery_agent", False, False),
        ("tracking_access_agent", False, False),
        ("website_situation_agent", False, False),
        ("competitor_market_agent", False, False),
        ("readiness_gate", False, False),
    ],
    "structured_data_specialist": [
        ("discovery_agent", False, False),
        ("tracking_access_agent", False, False),
        ("website_situation_agent", False, False),
        ("competitor_market_agent", False, False),
        ("readiness_gate", False, False),
    ],
    "seo_qa_lead": [
        ("discovery_agent", False, False),
        ("tracking_access_agent", False, False),
        ("website_situation_agent", False, False),
        ("competitor_market_agent", False, False),
        ("readiness_gate", True, True),
    ],
}


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
    }

    def section(title: str, source_role: str, body: Any, *, empty: str) -> dict[str, Any]:
        clean = body
        if isinstance(body, dict):
            clean = {k: v for k, v in body.items() if k != "_draft"}
        has = bool(clean) and clean not in ({}, [], None, "")
        return {
            "title": title,
            "source_role": source_role,
            "source_label": role_label(source_role),
            "status": "ready" if has else "waiting",
            "empty_hint": empty,
            "data": clean if has else None,
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
                        "access": "Full trigger + approve on all Phase 1–4 skills and readiness",
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
                competitive,
                empty="Run Competitor analysis to publish the tiered landscape.",
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
                competitive,
                empty="Run competitor analysis to publish the tiered landscape.",
            )
        )

    else:
        # Content / On-page / Structured — view shared upstream memory (phases 1–4)
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
                competitive,
                empty="No competitive landscape yet.",
            )
        )
        sections.append(
            {
                "title": "Your skills (later phases)",
                "source_role": role_name,
                "source_label": role_label(role_name),
                "status": "planned",
                "empty_hint": "Phases 5–17 skills are mapped but not enabled in this build.",
                "data": [
                    p.get("title") or p
                    for p in PHASE_SKILL_COVERAGE
                    if role_name in (p.get("roles") or []) and not p.get("implemented")
                ],
            }
        )

    return sections
