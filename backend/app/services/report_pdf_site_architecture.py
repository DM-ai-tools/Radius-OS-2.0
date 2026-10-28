"""Blueprint PDF layout matching site_architecture_blueprint.pdf reference."""

from __future__ import annotations

from typing import Any

from reportlab.platypus import CondPageBreak, Spacer

from app.services.report_pdf_theme import (
    body,
    bullet_list,
    data_table_weighted,
    header_band,
    note_block,
    numbered_list,
    section_rule,
    subsection,
)


def _open_section(flow: list, number: int, title: str) -> None:
    flow.append(Spacer(1, 6))
    flow.append(section_rule())
    flow.append(Spacer(1, 8))
    flow.extend(header_band(f"{number}. {title}"))


def _map_title(row: dict[str, Any]) -> str:
    from app.services.headline_framework import title_for_map_row

    return title_for_map_row(row) or "—"


def _rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


def _str_list(raw: Any, n: int = 4) -> str:
    if not isinstance(raw, list) or not raw:
        return "—"
    bits: list[str] = []
    for x in raw[:n]:
        if isinstance(x, str):
            bits.append(x)
        elif isinstance(x, dict):
            bits.append(
                str(x.get("name") or x.get("domain") or x.get("url") or x.get("title") or "")
            )
    return ", ".join(b for b in bits if b) or "—"


def _fmt_vol(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_cpc(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_kw_list(raw: Any, n: int = 10) -> str:
    if not isinstance(raw, list) or not raw:
        return "—"
    return ", ".join(str(k) for k in raw[:n] if k) or "—"


def _competitor_observation(row: dict[str, Any]) -> str:
    for key in (
        "structural_observations",
        "ia_notes",
        "note",
        "notes",
        "structure",
        "summary",
    ):
        if row.get(key):
            return str(row[key])
    hubs = row.get("hub_paths") if isinstance(row.get("hub_paths"), list) else []
    parts: list[str] = []
    if row.get("page_count") is not None:
        parts.append(f"{row['page_count']} pages")
    if row.get("blog_count") is not None:
        parts.append(f"{row['blog_count']} blog posts")
    if hubs:
        parts.append(f"Hubs: {', '.join(str(h) for h in hubs[:4])}")
    return "; ".join(parts) if parts else "—"


def _metric_note(metric: str, current: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    issue_map = {str(i.get("issue") or "").lower(): str(i.get("note") or "") for i in issues}
    defaults = {
        "total tracked urls": "Baseline crawl snapshot",
        "http 200 status": "Pending live crawl confirmation" if not current.get("status_200") else "Healthy responses",
        "max depth": "Seed level" if (current.get("max_click_depth") or 0) <= 1 else "Monitor hub depth",
        "pages ≤ 3 clicks": "Optimal accessibility",
        "pages depth 4+": "No deep architecture penalties" if not current.get("depth_4_plus") else "Remediate via nav/hubs",
        "orphan pages": "Fully connected graph" if not current.get("orphans") else "Link from hubs or nav",
        "phantom directories": "Clean directory mapping" if not current.get("phantom_dirs") else "Add hub pages",
    }
    key = metric.lower()
    for ik, note in issue_map.items():
        if ik and ik in key:
            return note or defaults.get(key, "—")
    return defaults.get(key, "—")


def render_site_architecture_blueprint(
    flow: list,
    report: dict,
    client: dict,
    *,
    show_title: bool = True,
) -> None:
    from app.services.report_export import resolve_report_title

    payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
    current = payload.get("current_state") if isinstance(payload.get("current_state"), dict) else {}
    url_rules = (
        payload.get("url_convention_rules")
        if isinstance(payload.get("url_convention_rules"), dict)
        else None
    )
    rollout = payload.get("rollout_plan") if isinstance(payload.get("rollout_plan"), dict) else None

    issues = _rows(current.get("issues"))
    url_map_report = (
        payload.get("url_map_report") if isinstance(payload.get("url_map_report"), dict) else {}
    )
    from app.services.url_map_report import numbered

    url_map = numbered(_rows((url_map_report or {}).get("final_url_map")))
    tree = _rows(payload.get("target_url_tree"))
    ownership = _rows(payload.get("cluster_ownership"))
    types = _rows(payload.get("page_type_model"))
    redirects = _rows(payload.get("redirect_map"))
    handoffs = _rows(payload.get("handoffs"))
    competitor_ia = _rows(payload.get("competitor_ia"))
    competitor_sites = _rows(payload.get("competitor_sites"))
    money_pages = (
        [str(p) for p in payload.get("money_pages")] if isinstance(payload.get("money_pages"), list) else []
    )
    evidence_notes = (
        [str(n) for n in current.get("evidence_notes")]
        if isinstance(current.get("evidence_notes"), list)
        else []
    )

    meta_bits: list[str] = []
    if payload.get("primary_url") or payload.get("domain") or client.get("primary_url"):
        meta_bits.append(str(payload.get("primary_url") or payload.get("domain") or client.get("primary_url")))
    geo = " · ".join(str(x) for x in [payload.get("geographic_focus"), payload.get("location_name")] if x)
    if geo:
        meta_bits.append(f"Geo: {geo}")

    title = resolve_report_title(report, client)
    if show_title:
        flow.extend(header_band(title, " · ".join(meta_bits), level="report"))
    elif meta_bits:
        flow.append(body(" · ".join(meta_bits)))
        flow.append(Spacer(1, 6))

    if show_title:
        flow.append(Spacer(1, 8))
        flow.append(section_rule())
        flow.append(Spacer(1, 8))
    flow.extend(header_band("Executive Summary"))
    client_label = payload.get("client") or client.get("name") or "the client"
    flow.append(
        body(
            f"This document establishes the site architecture blueprint for {client_label}. "
            f"The architecture uses a hub-and-spoke content model with {len(tree)} planned URLs "
            f"and {len(ownership)} cluster ownership assignments."
        )
    )
    flow.append(Spacer(1, 10))

    # 1. Competitive Architecture Benchmarking
    _open_section(flow, 1, "Competitive Architecture Benchmarking")
    comp_rows = competitor_ia or competitor_sites
    flow.append(
        data_table_weighted(
            ["Competitor", "Domain", "Structural Observations & Characteristics"],
            [
                [
                    c.get("name") or c.get("domain") or "Competitor",
                    c.get("domain") or c.get("url") or "—",
                    _competitor_observation(c),
                ]
                for c in comp_rows
            ],
            [1.1, 1.0, 2.4],
            max_rows=40,
        )
    )
    flow.append(Spacer(1, 10))

    # 2. Crawl Metrics and Architecture Gate
    _open_section(flow, 2, "Crawl Metrics and Architecture Gate")
    flow.append(
        body(
            "Initial site crawl metrics and structural constraints are outlined below to ensure "
            "compliance with technical SEO standards."
        )
    )
    metric_rows = [
        ["Total Tracked URLs", current.get("urls_crawled"), _metric_note("Total Tracked URLs", current, issues)],
        ["HTTP 200 Status", current.get("status_200"), _metric_note("HTTP 200 Status", current, issues)],
        ["Max Depth", current.get("max_click_depth"), _metric_note("Max Depth", current, issues)],
        ["Pages ≤ 3 Clicks", current.get("within_3_clicks"), _metric_note("Pages ≤ 3 Clicks", current, issues)],
        ["Pages Depth 4+", current.get("depth_4_plus"), _metric_note("Pages Depth 4+", current, issues)],
        ["Orphan Pages", current.get("orphans"), _metric_note("Orphan Pages", current, issues)],
        ["Phantom Directories", current.get("phantom_dirs"), _metric_note("Phantom Directories", current, issues)],
    ]
    flow.append(
        data_table_weighted(
            ["Metric / Parameter", "Value / Status", "Operational Note"],
            metric_rows,
            [1.4, 0.8, 2.3],
        )
    )
    technical_bits = list(evidence_notes)
    if payload.get("gate"):
        technical_bits.append(str(payload["gate"]))
    technical_bits.append(
        "Measured click depth is evaluated from the homepage seed rather than folder slash counts. "
        "Google does not count URL slashes; restructures solely for flat URL appearances are discouraged "
        "in favor of hub-and-spoke organizational logic."
    )
    flow.append(note_block("Technical Note:", " ".join(technical_bits)))
    flow.append(Spacer(1, 10))

    # 3. Page-Type Model & Taxonomy
    _open_section(flow, 3, "Page-Type Model & Taxonomy")
    flow.append(
        body("The taxonomy defines the structural hierarchy, indexing rules, and URL patterns across the domain.")
    )
    flow.append(
        data_table_weighted(
            ["Page Type", "URL Pattern", "Parent Node", "Breadcrumb Path", "Indexable"],
            [
                [
                    t.get("type"),
                    t.get("url_pattern"),
                    t.get("parent") or "—",
                    t.get("breadcrumb"),
                    "Yes" if t.get("indexable") is True else "No" if t.get("indexable") is False else "—",
                ]
                for t in types
            ],
            [1.0, 1.0, 0.8, 1.2, 0.6],
        )
    )
    if money_pages:
        flow.append(subsection("Core Money Pages"))
        flow.append(body("The primary revenue-generating and conversion-focused pages comprise:"))
        flow.extend(bullet_list(money_pages, max_items=12))
    flow.append(Spacer(1, 10))

    # 4. Target URL Tree
    _open_section(flow, 4, "Target URL Tree")
    flow.append(
        body("The target URL tree operationalizes the keyword targeting strategy across primary hubs and service spokes.")
    )
    flow.append(
        data_table_weighted(
            ["Type", "Path", "Depth", "Target Keyword", "Cluster Title", "Absolute URL"],
            [
                [
                    n.get("type") or "page",
                    n.get("path") or n.get("url"),
                    n.get("depth"),
                    n.get("primary_keyword") or n.get("keyword"),
                    n.get("cluster") or n.get("title") or "—",
                    n.get("absolute_url") or "—",
                ]
                for n in tree
            ],
            [0.6, 0.9, 0.45, 0.9, 0.9, 1.5],
            max_rows=40,
        )
    )
    if len(tree) > 40:
        flow.append(body(f"+{len(tree) - 40} more URLs in blueprint"))

    # 5. URL Mapping & Taxonomy Plan
    _open_section(flow, 5, "URL Mapping & Taxonomy Plan")
    flow.append(
        body(
            "Every keyword cluster resolved to a URL decision: optimize an existing page, "
            "review a weak match before merging, create new, or fold into a parent page that "
            "is yet to be built. Three views of the same numbered rows: taxonomy and status, "
            "keyword economics, then the decision and its evidence."
        )
    )
    flow.append(subsection("Taxonomy & Status"))
    flow.append(
        data_table_weighted(
            ["#", "Lvl", "L1", "L2", "L3", "L4", "Current URL", "Proposed URL", "Status", "Page Type", "Priority"],
            [
                [
                    u.get("row_no"),
                    u.get("level"),
                    u.get("l1_category") or "—",
                    u.get("l2_subcategory") or "—",
                    u.get("l3_subsubcategory") or "—",
                    u.get("l4_attribution") or "—",
                    u.get("current_url") or "—",
                    u.get("proposed_url") or "—",
                    u.get("status") or u.get("action"),
                    u.get("page_type") or "—",
                    u.get("priority") or u.get("score_band"),
                ]
                for u in url_map
            ],
            [0.4, 0.45, 0.75, 0.65, 0.65, 0.65, 1.25, 1.25, 0.9, 0.75, 0.8],
            max_rows=40,
        )
    )
    flow.append(Spacer(1, 8))
    flow.append(subsection("Keywords & Opportunity"))
    flow.append(
        data_table_weighted(
            [
                "#",
                "Primary Keyword",
                "Title",
                "Vol/mo",
                "CPC",
                "Secondary Keywords (5-10)",
                "Combined Vol",
                "Est. Products",
                "In Scope?",
                "Notes",
            ],
            [
                [
                    u.get("row_no"),
                    u.get("primary_keyword"),
                    _map_title(u),
                    _fmt_vol(u.get("search_volume")),
                    _fmt_cpc(u.get("cpc")),
                    _fmt_kw_list(u.get("secondary_keywords_sheet") or u.get("secondary_keywords")),
                    _fmt_vol(u.get("combined_cluster_volume")),
                    u.get("est_products") if u.get("est_products") is not None else "—",
                    u.get("in_scope") or "—",
                    u.get("notes") or "—",
                ]
                for u in url_map
            ],
            [0.4, 1.0, 1.0, 0.7, 0.6, 1.1, 0.85, 0.8, 0.65, 0.8],
            max_rows=40,
        )
    )
    from app.services.url_map_report import (
        DECISION_HEADERS,
        DECISION_WEIGHTS,
        decision_row,
        has_decision_evidence,
    )

    decided = [u for u in url_map if has_decision_evidence(u)]
    if decided:
        flow.append(Spacer(1, 8))
        # Keep the heading with the first rows instead of alone at a page foot.
        flow.append(CondPageBreak(180))
        flow.append(subsection("Decision & Evidence"))
        flow.append(
            data_table_weighted(
                DECISION_HEADERS,
                [decision_row(u) for u in decided],
                DECISION_WEIGHTS,
                max_rows=40,
            )
        )
    summary = (url_map_report or {}).get("summary") or {}
    if summary:
        flow.append(
            body(
                f"Checks: {summary.get('inconsistent_rows', 0)} contradictory rows rejected, "
                f"{summary.get('url_collisions', 0)} URL collisions flagged, "
                f"{summary.get('hygiene_review', 0)} proposals flagged by URL hygiene, "
                f"{summary.get('scored_without_search_console', 0)} rows scored without Search Console."
            )
        )
    if len(url_map) > 40:
        flow.append(body(f"+{len(url_map) - 40} more mapped clusters in payload"))
    flow.append(Spacer(1, 10))

    # 6. Cluster Ownership & Governance
    _open_section(flow, 6, "Cluster Ownership & Governance")
    flow.append(
        body(
            "To prevent internal keyword cannibalization and establish clear authority, "
            "each content cluster is assigned a canonical owner."
        )
    )
    flow.append(
        data_table_weighted(
            [
                "Content Cluster",
                "Canonical Owner",
                "Disposition",
                "Competing URLs",
                "Governance Note",
            ],
            [
                [
                    o.get("cluster"),
                    o.get("canonical_owner_url") or o.get("owner_url") or o.get("url"),
                    o.get("disposition") or "keep",
                    _str_list(o.get("competing_urls"), 3),
                    o.get("note")
                    or "Hub owns cluster; spokes nest underneath; cross-cluster links permitted.",
                ]
                for o in ownership
            ],
            [1.0, 1.2, 0.7, 0.8, 1.6],
            max_rows=40,
        )
    )
    flow.append(Spacer(1, 10))

    # 7. URL Convention Rules & Governance
    _open_section(flow, 7, "URL Convention Rules & Governance")
    if url_rules:
        plain_rules = [
            f"{k.replace('_', ' ').title()}: {v}"
            for k, v in url_rules.items()
            if k != "references" and isinstance(v, str)
        ]
        flow.extend(bullet_list(plain_rules, max_items=60))
    else:
        flow.append(body("—"))
    flow.append(Spacer(1, 10))

    # 8. Rollout Plan and Phased Handoffs
    _open_section(flow, 8, "Rollout Plan and Phased Handoffs")
    if rollout:
        flow.append(
            body(
                "The rollout plan requires rigorous adherence to sequence to safeguard organic performance during implementation:"
            )
        )
        seq = rollout.get("sequence") if isinstance(rollout.get("sequence"), list) else []
        if seq:
            flow.extend(numbered_list([str(s) for s in seq], max_items=60))
        extras: list[str] = []
        if rollout.get("freeze_window"):
            extras.append(f"Freeze: {rollout['freeze_window']}")
        if rollout.get("rollback_trigger"):
            extras.append(f"Rollback: {rollout['rollback_trigger']}")
        risks = rollout.get("high_risk_flags") if isinstance(rollout.get("high_risk_flags"), list) else []
        if risks:
            extras.append(f"Risk flags: {' · '.join(str(r) for r in risks)}")
        flow.extend(bullet_list(extras, max_items=60))
    if redirects:
        flow.append(subsection("Redirect map (Technical SEO handoff)"))
        flow.append(
            data_table_weighted(
                ["Old URL", "New URL", "Code", "Note"],
                [
                    [r.get("old_url"), r.get("new_url"), r.get("code") or 301, r.get("note")]
                    for r in redirects
                ],
                [1.2, 1.2, 0.5, 1.6],
                max_rows=40,
            )
        )
    if handoffs:
        flow.append(subsection("Cross-Functional Handoffs"))
        flow.append(
            data_table_weighted(
                ["Deliverable", "Receiving phase / owner"],
                [
                    [
                        h.get("item") or h.get("concern") or h.get("deliverable"),
                        h.get("receiving") or h.get("route_to") or h.get("owner"),
                    ]
                    for h in handoffs
                ],
                [1.4, 2.1],
            )
        )

    refs = []
    if url_rules and isinstance(url_rules.get("references"), list):
        refs = [str(r) for r in url_rules["references"]]
    if refs:
        flow.append(Spacer(1, 10))
        flow.append(subsection("References"))
        flow.extend(bullet_list(refs, max_items=10))
