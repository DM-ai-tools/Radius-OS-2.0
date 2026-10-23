"""Map chat / CDP card payloads into shapes the PDF blueprints expect."""

from __future__ import annotations

import json
from typing import Any

_SKIP = frozenset(
    {
        "card_type",
        "agent_key",
        "actions",
        "required_role",
        "event_type",
        "type",
        "title",
        "_memory_slim",
        "_draft",
        "audit_ids",
        "tabs_run",
        "invite_manual",
        "empty",
        "mode",
        "step",
        "subtitle",
        "skills_used",
        "analysis_mode",
        "discovery_sources",
    }
)

_AUDIT_TAB_KEYS = frozenset({"technical", "authority", "anomalies", "backlink_summary", "traffic_anomaly", "crawl"})


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [r for r in value if isinstance(r, dict)]


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _join_parts(parts: list[Any], *, limit: int = 4) -> str:
    bits = [str(p) for p in parts if p not in (None, "", [], {})][:limit]
    return ", ".join(bits)


def _pick(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = row.get(key)
        if val not in (None, "", [], {}):
            return val
    return None


def _normalize_handoffs(rows: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in _as_rows(rows):
        out.append(
            {
                "item": _first(row.get("item"), row.get("concern"), row.get("deliverable")),
                "receiving": _first(row.get("receiving"), row.get("route_to"), row.get("owner")),
            }
        )
    return out


def _detail_note(detail: Any) -> str:
    if detail in (None, "", {}, []):
        return ""
    if isinstance(detail, str):
        return detail[:180]
    try:
        return json.dumps(detail, default=str)[:180]
    except TypeError:
        return str(detail)[:180]


def _normalize_website_audit(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    tabs = _as_dict(payload.get("tabs"))
    technical = _as_dict(tabs.get("technical"))

    if tabs:
        out["technical"] = technical
        broken = _as_dict(technical.get("broken_links"))
        out.setdefault("pages_found", technical.get("pages_found"))
        out.setdefault("indexable", technical.get("indexable"))
        out.setdefault(
            "broken_count",
            broken.get("broken_count") or technical.get("broken_count"),
        )
        out.setdefault("severity", _first(*(payload.get("severities") or {}).values()))

        if not out.get("summary"):
            parts: list[str] = []
            if payload.get("tabs_run"):
                parts.append(f"Audits run: {_join_parts(payload['tabs_run'])}")
            if technical.get("pages_found") is not None:
                parts.append(f"{technical.get('pages_found')} pages found")
            if broken.get("broken_count"):
                parts.append(f"{broken.get('broken_count')} broken links")
            out["summary"] = ". ".join(parts)

        samples = _as_rows(technical.get("status_samples"))
        if samples and not out.get("issues"):
            out["issues"] = [
                {
                    "issue": s.get("url") or s.get("issue") or "Page check",
                    "severity": s.get("status") or s.get("severity") or "—",
                    "count": 1,
                    "note": s.get("note") or "",
                }
                for s in samples
            ]

        if not out.get("page_audits"):
            out["page_audits"] = [
                {
                    "url": s.get("url"),
                    "score": s.get("score"),
                    "status": s.get("status"),
                    "note": s.get("note") or "",
                }
                for s in samples
                if s.get("url")
            ]

        tab_rows: list[dict[str, Any]] = []
        severities = _as_dict(payload.get("severities"))
        for name, data in tabs.items():
            if not isinstance(data, dict):
                continue
            tab_rows.append(
                {
                    "tab": name.replace("_", " ").title(),
                    "severity": severities.get(name) or data.get("severity") or "—",
                    "status": data.get("status") or "—",
                    "note": _detail_note(
                        data.get("message")
                        or data.get("note")
                        or data.get("reason")
                        or data.get("error")
                    ),
                }
            )
        if tab_rows:
            out["audit_tabs"] = tab_rows

    # CDP / approved memory shape: {audit_type: {summary, severity, status}, pages_found, ...}
    if not tabs:
        audit_rows: list[dict[str, Any]] = []
        for key, value in payload.items():
            if key in _SKIP or key.startswith("_"):
                continue
            if not isinstance(value, dict) or "summary" not in value:
                continue
            if key in _AUDIT_TAB_KEYS or value.get("severity") is not None:
                summary = value.get("summary")
                note = ""
                if isinstance(summary, dict):
                    note = _detail_note(
                        summary.get("message")
                        or summary.get("note")
                        or summary.get("error")
                        or summary
                    )
                elif summary:
                    note = str(summary)[:180]
                audit_rows.append(
                    {
                        "tab": key.replace("_", " ").title(),
                        "severity": value.get("severity") or "—",
                        "status": value.get("status") or "—",
                        "note": note,
                    }
                )
        if audit_rows:
            out["audit_tabs"] = audit_rows
        if not out.get("summary"):
            bits = []
            if payload.get("pages_found") is not None:
                bits.append(f"{payload.get('pages_found')} pages found")
            if payload.get("seo_overall_score") is not None:
                bits.append(f"SEO score {payload.get('seo_overall_score')}")
            if payload.get("note"):
                bits.append(str(payload.get("note")))
            out["summary"] = ". ".join(bits) or out.get("title", "")

    return out


def _normalize_competitor(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["summary"] = _first(out.get("executive_summary"), out.get("summary"))
    baseline = _as_dict(out.get("client_baseline"))
    if baseline.get("maturity_score") is not None:
        out.setdefault("client_baseline", baseline)
    elif out.get("client_baseline_maturity") is not None:
        out["client_baseline"] = {"maturity_score": out.get("client_baseline_maturity")}
    out["competitor_count"] = _first(
        out.get("competitors_scored"),
        out.get("competitors_in_report"),
        len(out.get("competitors") or []),
    )

    urls_by_name = {
        str(row.get("name") or "").strip().lower(): row.get("url") or row.get("domain")
        for row in _as_rows(out.get("competitors"))
        if isinstance(row, dict) and (row.get("url") or row.get("domain"))
    }
    tier_rows = []
    for row in _as_rows(out.get("tier_overview")):
        url = _first(row.get("url"), row.get("domain"), urls_by_name.get(str(row.get("name") or "").strip().lower()))
        tier_rows.append(
            {
                **row,
                "url": url,
                "composite": _first(row.get("composite"), row.get("score"), row.get("maturity_score")),
                "future_threat": _first(row.get("future_threat"), row.get("threat")),
            }
        )
    if tier_rows:
        out["tier_overview"] = tier_rows

    rec = _as_dict(out.get("recommendations"))
    if rec:
        out["recommendations"] = rec
        for key in ("benchmark", "differentiate", "monitor"):
            items = rec.get(key)
            if isinstance(items, list) and items and isinstance(items[0], dict):
                rec[key] = [
                    " · ".join(f"{k}: {v}" for k, v in item.items() if not isinstance(v, (list, dict)))
                    for item in items
                ]

    if not out.get("parameter_gaps") and out.get("scorecards"):
        out["parameter_gaps"] = [
            {
                "parameter": gap.get("parameter") or gap.get("label"),
                "client_score": gap.get("client_score"),
                "top_competitor": gap.get("top_competitor") or gap.get("competitor"),
                "gap": gap.get("gap"),
                "action": gap.get("action") or gap.get("recommendation"),
            }
            for sc in _as_rows(out.get("scorecards"))
            for gap in _as_rows(sc.get("gaps") or sc.get("weaknesses") or [])
            if isinstance(gap, dict)
        ][:20]

    return out


def _normalize_tracking(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    score = out.get("tracking_score", out.get("score"))
    blockers = out.get("blockers") or []
    out["score"] = score
    out["status"] = "blocked" if blockers else _first(out.get("status"), "ready")
    if not out.get("summary"):
        bits = []
        if score is not None:
            bits.append(f"Tracking readiness {score}%")
        if blockers:
            bits.append(f"{len(blockers)} blocker(s)")
        if out.get("automation_note"):
            bits.append(str(out.get("automation_note")))
        out["summary"] = ". ".join(bits)

    if not out.get("findings"):
        out["findings"] = [
            {
                "finding": row.get("element") or row.get("finding") or "Check",
                "severity": row.get("check_result") or row.get("severity") or "—",
                "note": _detail_note(row.get("detail")),
            }
            for row in _as_rows(out.get("rows") or out.get("findings"))
        ]

    missing = out.get("missing") or out.get("next_steps") or []
    if isinstance(missing, list):
        out["next_steps"] = [str(x) for x in missing]
    return out


def _normalize_search_demand(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    cluster_report = _as_dict(out.get("cluster_report"))
    out["cluster_count"] = _first(
        out.get("cluster_count"),
        cluster_report.get("clusters_created"),
        len(out.get("clusters") or []),
    )
    out["keyword_count"] = _first(out.get("keyword_count"), len(out.get("keyword_table") or []))
    out["quick_win_count"] = _first(
        out.get("quick_win_count"),
        len([r for r in _as_rows(out.get("best_opportunities")) if str(r.get("priority", "")).lower().find("quick") >= 0]),
    )
    out["source"] = _join_parts(out.get("providers_used") or [])
    if not out.get("opportunities"):
        out["opportunities"] = _as_rows(
            out.get("best_opportunities") or out.get("keyword_table") or out.get("topics")
        )
    for bucket in ("trend_plays", "avoid"):
        rows = _as_rows(out.get(bucket))
        if rows and isinstance(rows[0], dict):
            out[bucket] = [
                " · ".join(
                    f"{k}: {v}"
                    for k, v in row.items()
                    if k not in _SKIP and v not in (None, "", [], {})
                )[:160]
                for row in rows
            ]
    if not out.get("summary") and out.get("note"):
        out["summary"] = str(out.get("note"))
    # The layered Keyword Clusters sections read kc_* keys; the raw report and
    # the flattened list would otherwise also be auto-rendered as extra tables.
    from app.services.keyword_cluster_report import build_cluster_report_view

    out.update(build_cluster_report_view(out))
    out.pop("cluster_report", None)
    out.pop("clusters", None)
    return out


def _normalize_content_strategy(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    # The model write-up is internal. Downloads show the tables, not that essay.
    out.pop("executive_summary", None)
    out.pop("calendar_notes", None)
    out.pop("summary", None)

    core_topics = []
    for row in _as_rows(out.get("core_topics") or out.get("pillars")):
        core_topics.append(
            {
                **row,
                "name": _first(row.get("name"), row.get("pillar"), row.get("topic")),
                "volume": _first(row.get("volume"), row.get("opportunity_score")),
                "difficulty": _first(row.get("difficulty"), row.get("kd")),
                "priority": _first(row.get("priority"), row.get("priority_label")),
            }
        )
    if core_topics:
        out["core_topics"] = core_topics

    pillars = []
    for row in _as_rows(out.get("pillars")):
        pillars.append(
            {
                **row,
                "name": _first(row.get("name"), row.get("pillar")),
                "topic": _first(row.get("topic"), row.get("primary_keyword")),
                "volume": _first(row.get("volume"), row.get("opportunity_score")),
                "priority": _first(row.get("priority"), row.get("priority_label")),
            }
        )
    if pillars:
        out["pillars"] = pillars

    gaps = []
    for row in _as_rows(out.get("content_gaps") or out.get("competitor_content_gaps")):
        competitors = row.get("competitors")
        gaps.append(
            {
                **row,
                "topic": _first(row.get("topic"), row.get("keyword"), row.get("title")),
                "opportunity": _first(row.get("opportunity"), row.get("action"), row.get("priority")),
                "competitor": _join_parts(competitors if isinstance(competitors, list) else [row.get("competitor")]),
                "note": _first(row.get("note"), row.get("reason"), row.get("rationale")),
            }
        )
    if gaps:
        out["content_gaps"] = gaps

    if out.get("handoffs"):
        out["handoffs"] = _normalize_handoffs(out.get("handoffs"))

    from app.services.content_queue import present_content_plan

    out = present_content_plan(out)

    if not out.get("url_ia_plan"):
        out["url_ia_plan"] = _as_rows(out.get("url_ia_plan") or out.get("url_plan"))

    return out


def _normalize_broken_links(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if not out.get("summary"):
        out["summary"] = (
            f"Scanned {out.get('pages_scanned', '—')} pages · "
            f"{out.get('broken_count', 0)} broken links · "
            f"{out.get('redirect_chain_count', 0)} redirect chains"
        )
    if not out.get("broken_links"):
        rows = []
        for source, label in (("internal", "internal"), ("external", "external")):
            for row in _as_rows(out.get(source)):
                rows.append(
                    {
                        "url": _first(row.get("url"), row.get("href"), row.get("link")),
                        "status": _first(row.get("status_code"), row.get("status")),
                        "source": _first(row.get("source_page"), row.get("from"), label),
                    }
                )
        out["broken_links"] = rows
    return out


def _normalize_discovery(payload: dict[str, Any], card_type: str) -> dict[str, Any]:
    out = dict(payload)
    if card_type == "discovery_completeness":
        out["status"] = f"{out.get('completeness_score', '—')}% complete"
        if out.get("missing_fields"):
            out["summary"] = f"Missing or low-confidence: {_join_parts(out.get('missing_fields'), limit=8)}"
    if card_type == "discovery_profile":
        fields = []
        for label, block in (
            ("From research", out.get("from_research")),
            ("Confirmed by client", out.get("confirmed_by_client")),
        ):
            if not isinstance(block, dict):
                continue
            for key, value in block.items():
                if isinstance(value, dict) and "value" in value:
                    val = value.get("value")
                    src = value.get("source") or label
                else:
                    val = value
                    src = label
                fields.append({"field": key.replace("_", " ").title(), "value": val, "source": src})
        if fields:
            out["fields"] = fields
        if not out.get("summary"):
            out["summary"] = (
                f"Discovery {out.get('completeness_score', '—')}% complete"
                + (f" · {len(out.get('discrepancies') or [])} discrepancies flagged" if out.get("discrepancies") else "")
            )
    return out


def _percent_label(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number == int(number):
        return f"{int(number)}"
    return f"{number:.1f}"


def _normalize_readiness(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    phases = _as_dict(out.get("phases"))
    scores: list[str] = []
    missing: list[str] = list(out.get("missing") or [])
    if phases:
        if not missing:
            missing = []
            for phase, data in phases.items():
                if not isinstance(data, dict):
                    continue
                for item in data.get("missing") or []:
                    missing.append(f"{phase}: {item}")
            out["missing"] = missing
        for phase, data in phases.items():
            if not isinstance(data, dict) or data.get("score") is None:
                continue
            name = str(phase).replace("_", " ").title()
            scores.append(f"{name} — {_percent_label(data.get('score'))}%")
    if scores:
        out["phase_scores"] = scores
    return out


def _title_text(value: Any) -> Any:
    if isinstance(value, dict):
        return _first(value.get("after"), value.get("before"), value.get("text"))
    return value


def _normalize_on_page(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    pages = _as_rows(out.get("pages") or out.get("queue"))
    if pages:
        out["pages"] = [
            {
                **row,
                "url": _first(row.get("url"), row.get("path")),
                "title": _first(_title_text(row.get("title")), row.get("h1")),
                "score": _first(row.get("score"), row.get("current_score")),
                "keyword": _first(row.get("keyword"), row.get("fixes"), row.get("target_keyword")),
            }
            for row in pages
        ]
    if not out.get("summary"):
        out["summary"] = _first(out.get("note"), out.get("headline"))
    return out


# Report slug per audit group (matches report_pdf_document.TECH_GROUP_SLUGS).
_TECH_GROUP_SLUG = {
    "Crawlability": "crawlability",
    "Indexation": "indexation",
    "URL structure": "url_structure",
    "Canonicalisation": "canonicalisation",
    "Sitemap": "sitemap",
    "Internal linking": "internal_linking",
    "On-page technical SEO": "on_page",
    "JavaScript/rendering": "javascript_rendering",
    "Performance": "performance",
    "Mobile": "mobile",
    "Structured data": "structured_data",
    "Security": "security",
}
# What would complete each group when fields are not measured.
_TECH_GROUP_NEEDS = {
    "Crawlability": "A deeper crawl (more pages) for crawl depth, orphans and blocked resources.",
    "Indexation": "Search Console connection for indexed, excluded and soft-404 counts.",
    "URL structure": "Full-site crawl for parameter and duplicate URL inventories.",
    "Canonicalisation": "Search Console for Google-selected canonicals; full crawl for every page's canonical.",
    "Sitemap": "Sitemap XML reachable from this server; Search Console for submitted sitemaps.",
    "Internal linking": "A deeper crawl for the link graph, anchors and orphan pages.",
    "On-page technical SEO": "Full-site crawl to extend the page sample to every URL.",
    "JavaScript/rendering": "A browser-rendered copy (Firecrawl) to compare with raw HTML.",
    "Performance": "Chrome UX Report field data (needs enough real traffic) for INP and pass/fail.",
    "Mobile": "Manual tap-target review (no longer audited by Lighthouse).",
    "Structured data": "A deeper crawl to check every template's markup.",
    "Security": "Response headers readable from this server (some sites block it).",
}


def _technical_seo_report_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Curated payload for the twelve-group layout. Only what the blueprint renders
    (plus identity and summary), so raw provider blobs do not spill into
    auto-generated sections."""
    out: dict[str, Any] = {
        key: payload.get(key)
        for key in (
            "client_name", "primary_url", "site", "executive_summary", "severity", "phase6_connected",
            "scorecard_line", "quick_wins", "priority_backlog", "ia_actions", "specialist_routing",
            "not_measured", "audit_coverage", "page_sample", "on_page_audit", "canonical_audit",
            "js_rendering_audit", "performance_audit", "structured_data_audit", "security_audit",
        )
        if payload.get(key) not in (None, "", [], {})
    }
    out["score"] = _first(payload.get("score"), payload.get("overall_score"))
    pct = _as_dict(_as_dict(payload.get("audit_coverage")).get("summary")).get("coverage_pct")
    if out["score"] is not None and isinstance(pct, (int, float)) and pct < 50:
        # A mean over a few measured groups reads as authoritative; say how much it covers.
        out["score"] = f"{out['score']} (based on {pct}% of fields)"
    out["coverage_label"] = "Crawl and page sample"
    out["data_source_note"] = " ".join(
        bit for bit in (
            "Providers: " + ", ".join(payload.get("providers_used") or []) + "." if payload.get("providers_used") else "",
            "Sections marked Reduced coverage had fields this pass could not measure; each says why.",
        ) if bit
    )
    groups: dict[str, Any] = {}
    for g in _as_rows(payload.get("audit_groups")):
        slug = _TECH_GROUP_SLUG.get(str(g.get("group")))
        if not slug:
            continue
        score = g.get("score")
        groups[slug] = {**g, "score_label": f"{score}/100" if score is not None else "not measured"}
    out["group"] = groups
    cov = _as_dict(payload.get("audit_coverage"))
    needs = {**_TECH_GROUP_NEEDS, **_as_dict(payload.get("coverage_needs"))}
    out["coverage_matrix"] = [
        {
            "group": row.get("group"),
            "fields": len(row.get("fields") or []),
            "measured": sum(1 for f in row.get("fields") or [] if f.get("status") != "not_measured"),
            "with_issues": row.get("issue_count"),
            "not_measured": row.get("not_measured_count"),
            "needs": needs.get(str(row.get("group")), "") if row.get("not_measured_count") else "Complete this pass.",
        }
        for row in _as_rows(cov.get("groups"))
    ]
    sd = dict(_as_dict(out.get("structured_data_audit")))
    if sd:
        sd["eligible_rows"] = [{"result": r} for r in sd.get("rich_results_eligible") or []]
        out["structured_data_audit"] = sd
    sec = dict(_as_dict(out.get("security_audit")))
    wp = _as_dict(sec.get("wordpress"))
    if wp.get("is_wordpress"):
        sec["wordpress_rows"] = [
            {"check": "Config backups readable", "result": ", ".join(wp.get("exposed_files") or []) or "none of the common names"},
            {"check": "XML-RPC", "result": "enabled" if wp.get("xmlrpc_enabled") else f"not exposed (status {wp.get('xmlrpc_status')})"},
            {"check": "Version disclosed", "result": wp.get("version_exposed") or "no"},
            {"check": "readme.html public", "result": "yes" if wp.get("readme_exposed") else "no"},
        ]
        out["security_audit"] = sec
    clusters = _as_dict(out.get("canonical_audit"))
    if clusters.get("duplicate_clusters"):
        out["canonical_audit"] = {
            **clusters,
            "duplicate_clusters": [
                {**c, "urls": ", ".join(str(u) for u in (c.get("urls") or [])[:5])}
                for c in _as_rows(clusters.get("duplicate_clusters"))
            ],
        }
    return out


def _normalize_technical_seo(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("audit_groups"):
        return _technical_seo_report_payload(payload)
    out = dict(payload)
    out["score"] = _first(out.get("score"), out.get("overall_score"))
    if not out.get("findings_by_theme"):
        themes = _as_rows(out.get("themes"))
        if themes:
            out["findings_by_theme"] = themes
        else:
            sections = _as_dict(out.get("sections"))
            if sections:
                out["findings_by_theme"] = [
                    {
                        "theme": name.replace("_", " ").title(),
                        "score": data.get("score"),
                        "findings": data.get("findings") or [],
                    }
                    for name, data in sections.items()
                    if isinstance(data, dict)
                ]
    if not out.get("priority_backlog"):
        backlog = _as_rows(out.get("backlog") or out.get("priority_fixes"))
        if backlog:
            out["priority_backlog"] = backlog
    return out


def _normalize_content_audit(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if not out.get("inventory") and out.get("pages"):
        out["inventory"] = _as_rows(out.get("pages"))
    inventory = _as_rows(out.get("inventory"))
    if out.get("inventory_count") is None:
        out["inventory_count"] = len(inventory)
    if out.get("handoffs"):
        out["handoffs"] = _normalize_handoffs(out.get("handoffs"))
    return out


def _format_business_fit(value: Any) -> str:
    if isinstance(value, dict) and value:
        return " · ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", [], {}))
    if value not in (None, "", [], {}):
        return str(value)
    return "—"


def _normalize_content_planning(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    pages = _as_rows(out.get("pages")) or _as_rows(out.get("roadmap"))
    if pages:
        out["pages"] = pages
    # "roadmap" is the same list as "pages" (content_planning.py aliases both to the
    # same rows) — drop it here so the auto-section walker doesn't render it twice.
    out.pop("roadmap", None)
    signals = []
    for p in pages or []:
        angle = p.get("angle")
        fit = p.get("business_fit")
        domains = p.get("competitor_domains")
        if not (angle or fit or domains):
            continue
        signals.append(
            {
                "url_n": p.get("url_n") or p.get("path") or p.get("url"),
                "angle": angle or "—",
                "business_fit": _format_business_fit(fit),
                "competitor_domains": ", ".join(domains or []) or "—",
            }
        )
    if signals:
        out["content_signals"] = signals
    return out


def _normalize_seo_audit(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    if not out.get("summary"):
        bits = []
        if out.get("overall_score") is not None:
            bits.append(f"Score {out.get('overall_score')}/100")
        if out.get("score_band"):
            bits.append(str(out["score_band"]))
        if out.get("pages_analyzed") is not None:
            bits.append(f"{out.get('pages_analyzed')} pages analyzed")
        if out.get("error"):
            bits.append(str(out["error"]))
        out["summary"] = " · ".join(bits)
    if not out.get("issues"):
        issues: list[dict[str, Any]] = []
        for severity, key in (
            ("Critical", "critical"),
            ("Warning", "warnings"),
            ("Opportunity", "opportunities"),
        ):
            for row in _as_rows(out.get(key)):
                issues.append(
                    {
                        "issue": _first(row.get("issue"), row.get("message"), row.get("title")),
                        "severity": severity,
                        "url": _first(row.get("url"), row.get("ref")),
                    }
                )
        out["issues"] = issues
    return out


def _normalize_content_production(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    briefs = []
    for row in _as_rows(out.get("briefs")):
        briefs.append(
            {
                **row,
                "url": _first(row.get("url"), row.get("path")),
                "angle": _first(row.get("angle"), row.get("differentiation")),
                "status": _first(
                    row.get("status"),
                    "writer ready" if row.get("writer_ready") else None,
                ),
            }
        )
    if briefs:
        out["briefs"] = briefs
    return out


def _normalize_publishing(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    rows = _as_rows(out.get("queue") or out.get("publish_queue"))
    if rows:
        out["queue"] = rows
    if not out.get("summary"):
        out["summary"] = (
            f"{out.get('pages_in_package', len(rows))} page(s) · "
            f"mode {out.get('mode_effective') or out.get('mode_requested') or '—'} · "
            f"CMS {out.get('cms_mode') or '—'}"
        )
    return out


def _normalize_generic_lists(payload: dict[str, Any]) -> dict[str, Any]:
    """Alias common queue / roadmap field names used across phases."""
    out = dict(payload)
    aliases = {
        "roadmap": ("page_roadmap", "pages", "planned_pages"),
        "queue": ("publish_queue", "items"),
        "briefs": ("production_briefs", "drafts"),
    }
    for target, sources in aliases.items():
        if out.get(target):
            continue
        for source in sources:
            rows = _as_rows(out.get(source))
            if rows:
                out[target] = rows
                break
    return out


_NORMALIZERS: dict[str, Any] = {
    "website_audit": _normalize_website_audit,
    "broken_link_report": _normalize_broken_links,
    "competitor_landscape": _normalize_competitor,
    "search_demand_report": _normalize_search_demand,
    "content_strategy_report": _normalize_content_strategy,
    "readiness_score": _normalize_readiness,
    "on_page_seo_report": _normalize_on_page,
    "technical_seo_report": _normalize_technical_seo,
    "content_audit_report": _normalize_content_audit,
    "content_planning_report": _normalize_content_planning,
    "seo_audit_report": _normalize_seo_audit,
    "content_production_report": _normalize_content_production,
    "publishing_report": _normalize_publishing,
}


def normalize_report_payload(card_type: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    out = dict(payload)
    has_dedicated_normalizer = False
    if card_type.startswith("tracking_"):
        out = _normalize_tracking(out)
        has_dedicated_normalizer = True
    elif card_type.startswith("discovery_"):
        out = _normalize_discovery(out, card_type)
        has_dedicated_normalizer = True
    else:
        normalizer = _NORMALIZERS.get(card_type)
        if normalizer:
            out = normalizer(out)
            has_dedicated_normalizer = True
    if not has_dedicated_normalizer:
        # Generic roadmap/queue/briefs aliasing is a blunt fallback for card types
        # with no dedicated normalizer (e.g. publishing_report's publish_queue ->
        # queue). Card types with a dedicated normalizer already shaped their own
        # fields correctly — re-aliasing here would undo intentional cleanup, e.g.
        # content_planning_report dropping "roadmap" because it's the same list
        # as "pages" and would otherwise render as a duplicate section.
        out = _normalize_generic_lists(out)
    return out
