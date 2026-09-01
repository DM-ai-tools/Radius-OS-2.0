"""Collect structured phase reports for client export."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatMessage, ChatSession, Client, ClientDigitalProfile

NOISE_EVENT_TYPES = frozenset(
    {
        "checkpoint",
        "phase_status",
        "job_progress",
        "thinking",
        "done",
        "active_agent",
        "system_notice",
        "error",
    }
)

SKIP_CARD_TYPES = frozenset(
    {
        "oauth_request",
        "discovery_rerun_confirm",
    }
)

PROFILE_SUMMARY_FIELDS: list[tuple[str, str, str]] = [
    ("website_situation_summary", "website_audit", "Website audit"),
    ("competitive_landscape_summary", "competitor_landscape", "Competitor landscape"),
    ("search_demand_summary", "search_demand_report", "Search demand"),
    ("seo_strategy_summary", "content_strategy_report", "Content strategy"),
    ("site_architecture_summary", "site_architecture_blueprint", "Site architecture"),
    ("technical_seo_summary", "technical_seo_report", "Technical SEO"),
    ("content_audit_summary", "content_audit_report", "Content audit"),
    ("content_planning_summary", "content_planning_report", "Content planning"),
    ("content_production_summary", "content_production_report", "Content production"),
    ("on_page_seo_summary", "on_page_seo_report", "On-page SEO"),
    ("publishing_summary", "publishing_report", "Publishing"),
]

CARD_ORDER: dict[str, int] = {
    "discovery_preresearch": 1,
    "discovery_questionnaire": 2,
    "discovery_completeness": 3,
    "discovery_profile": 4,
    "tracking_health": 10,
    "tracking_t1_access": 11,
    "tracking_t2_audit": 12,
    "tracking_t3_conversions": 13,
    "tracking_t4_baseline": 14,
    "tracking_t5_known_changes": 15,
    "tracking_t6_signoff": 16,
    "website_audit": 20,
    "broken_link_report": 21,
    "seo_audit_report": 22,
    "competitor_landscape": 30,
    "search_demand_report": 40,
    "content_strategy_report": 50,
    "site_architecture_blueprint": 60,
    "technical_seo_report": 70,
    "content_audit_report": 80,
    "content_planning_report": 90,
    "content_production_report": 100,
    "on_page_seo_report": 110,
    "publishing_report": 120,
    "readiness_score": 200,
}


def _default_title(card_type: str) -> str:
    return card_type.replace("_", " ").strip().title()


def _is_report_payload(payload: dict) -> bool:
    event_type = str(payload.get("event_type") or payload.get("type") or "")
    if event_type in NOISE_EVENT_TYPES:
        return False
    card_type = payload.get("card_type")
    if not card_type:
        return event_type == "structured_card" and bool(payload.get("title"))
    return str(card_type) not in SKIP_CARD_TYPES


def _sort_key(entry: dict) -> tuple[int, str]:
    card_type = str(entry.get("card_type") or "")
    return (CARD_ORDER.get(card_type, 999), card_type)


async def build_report_export(db: AsyncSession, client_id: UUID) -> dict:
    client = (
        await db.execute(select(Client).where(Client.id == client_id))
    ).scalar_one_or_none()
    if not client:
        raise LookupError("Client not found")

    profile = (
        await db.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client_id)
        )
    ).scalar_one_or_none()

    result = await db.execute(
        select(ChatMessage, ChatSession.started_at)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.client_id == client_id)
        .where(ChatMessage.structured_payload.isnot(None))
        .order_by(ChatMessage.created_at.asc())
    )
    rows = result.all()

    latest_by_type: dict[str, dict] = {}
    for msg, _session_started in rows:
        payload = msg.structured_payload
        if not isinstance(payload, dict) or not _is_report_payload(payload):
            continue
        card_type = str(payload.get("card_type") or "structured_report")
        latest_by_type[card_type] = {
            "id": str(msg.id),
            "card_type": card_type,
            "title": str(payload.get("title") or _default_title(card_type)),
            "agent_key": msg.agent_key or payload.get("agent_key"),
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "source": "chat",
            "payload": payload,
        }

    client_name = client.display_name or client.legal_name or "client"
    client_ctx = {
        "id": str(client.id),
        "name": client_name,
        "primary_url": client.primary_url,
        "industry": client.industry,
    }

    if profile:
        for field, card_type, _fallback in PROFILE_SUMMARY_FIELDS:
            if card_type in latest_by_type:
                continue
            summary = getattr(profile, field, None)
            if not summary or summary == {}:
                continue
            draft = {
                "card_type": card_type,
                "payload": {"card_type": card_type, **summary},
            }
            title = resolve_report_title(draft, client_ctx)
            latest_by_type[card_type] = {
                "id": None,
                "card_type": card_type,
                "title": title,
                "agent_key": None,
                "created_at": profile.updated_at.isoformat() if profile.updated_at else None,
                "source": "profile_summary",
                "payload": {"card_type": card_type, "title": title, **summary},
            }

    reports = sorted(latest_by_type.values(), key=_sort_key)
    for report in reports:
        report["title"] = resolve_report_title(report, client_ctx)
        if isinstance(report.get("payload"), dict):
            report["payload"]["title"] = report["title"]

    slug = "".join(ch if ch.isalnum() else "-" for ch in client_name.lower()).strip("-") or "client"

    return {
        "client": client_ctx,
        "exported_at": datetime.now(UTC).isoformat(),
        "report_count": len(reports),
        "filename_slug": slug,
        "reports": reports,
        "phase_statuses": {
            "discovery": profile.discovery_status if profile else "not_started",
            "tracking": profile.tracking_status if profile else "not_started",
            "website": profile.website_status if profile else "not_started",
            "competitor": profile.competitor_status if profile else "not_started",
            "search_demand": profile.search_demand_status if profile else "not_started",
            "seo_strategy": profile.seo_strategy_status if profile else "not_started",
            "site_architecture": profile.site_architecture_status if profile else "not_started",
            "technical_seo": profile.technical_seo_status if profile else "not_started",
            "content_audit": profile.content_audit_status if profile else "not_started",
            "content_planning": profile.content_planning_status if profile else "not_started",
            "content_production": profile.content_production_status if profile else "not_started",
            "on_page_seo": profile.on_page_seo_status if profile else "not_started",
            "publishing": profile.publishing_status if profile else "not_started",
            "overall_readiness_score": float(profile.overall_readiness_score or 0)
            if profile and profile.overall_readiness_score is not None
            else None,
        },
    }


CARD_TYPE_TITLES: dict[str, str] = {
    "discovery_preresearch": "D1 — Automated pre-research: {client}",
    "discovery_questionnaire": "D2 — CDD questionnaire: {client}",
    "discovery_completeness": "D3 — Completeness scoring: {client}",
    "discovery_profile": "D4 — Discovery profile: {client}",
    "tracking_t1_access": "T1 — Access collection: {client}",
    "tracking_t2_audit": "T2 — Tracking audit: {client}",
    "tracking_t3_conversions": "T3 — Conversions: {client}",
    "tracking_t4_baseline": "T4 — Baseline: {client}",
    "tracking_t5_known_changes": "T5 — Known-changes log: {client}",
    "tracking_t6_signoff": "T6 — Tracking sign-off: {client}",
    "website_audit": "Website Situation — Audit Summary: {client}",
    "broken_link_report": "Broken Link Report: {client}",
    "seo_audit_report": "SEO Audit Report: {client}",
    "competitor_landscape": "Competitor & Market Analysis: {client}",
    "search_demand_report": "Search Demand & Keyword Opportunities: {client}",
    "content_audit_report": "Content Audit: {client}",
    "content_strategy_report": "Content Strategy: {client}",
    "site_architecture_blueprint": "Site Architecture Blueprint: {client}",
    "technical_seo_report": "Technical SEO: {client}",
    "content_planning_report": "Content Planning: {client}",
    "content_production_report": "Content Production: {client}",
    "on_page_seo_report": "On-Page SEO: {client}",
    "publishing_report": "Publishing & Indexation: {client}",
    "readiness_score": "Readiness Score: {client}",
}


def resolve_report_title(report: dict, client: dict | None = None) -> str:
    """Best display title for a report — payload title, then client-aware defaults."""
    payload = report.get("payload") if isinstance(report.get("payload"), dict) else {}
    card_type = str(payload.get("card_type") or report.get("card_type") or "")
    client_name = str((client or {}).get("name") or payload.get("client") or payload.get("business_name") or "").strip()

    for candidate in (report.get("title"), payload.get("title")):
        text = str(candidate or "").strip()
        if text and text.lower() not in {"report", "update", "structured report"}:
            return text

    template = CARD_TYPE_TITLES.get(card_type)
    if template:
        return template.format(client=client_name) if client_name else template.split(":")[0].strip()

    if card_type:
        base = _default_title(card_type)
        return f"{base}: {client_name}" if client_name else base
    return f"Report: {client_name}" if client_name else "Report"


def report_filename_slug(report: dict, client: dict | None = None) -> str:
    title = resolve_report_title(report, client)
    slug = "".join(ch if ch.isalnum() else "-" for ch in title.lower()).strip("-")
    return slug[:72] or "report"


def find_report(bundle: dict, card_type: str) -> dict | None:
    for report in bundle.get("reports") or []:
        if str(report.get("card_type") or "") == card_type:
            return report
    return None


def _legacy_card_slug(card_type: str) -> str:
    return card_type.replace("_", "-").strip("-") or "report"

