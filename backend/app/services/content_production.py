"""Phase 10 — Content briefs + one create-content draft from a locked roadmap."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.services.create_topic import format_audience_label
from app.services.create_topic import funnel_balance as _funnel_balance
from app.logging_config import get_logger
from app.services.create_topic import (
    funnel_balance_warnings as _funnel_balance_warnings,
)

log = get_logger("content_production")

_SELECT_HINTS = (
    "write the full draft",
    "write full draft",
    "write the content for",
    "write content for",
    "draft this topic",
    "draft topic",
    "select topic",
)

_CREATE_BASIS_OK = {
    "new_content_gap",
    "strategy_gap",
    "url_map_create",
    "audit_disposition",
}


def planning_gate_ok(planning_status: str | None, planning: dict[str, Any]) -> bool:
    if planning.get("locked") is False:
        return False
    if planning.get("locked") is True:
        return True
    if (planning_status or "") == "complete":
        roadmap = planning.get("pages") or planning.get("roadmap") or []
        return isinstance(roadmap, list) and any(
            isinstance(r, dict) and (r.get("url") or r.get("path") or r.get("url_n")) for r in roadmap
        )
    roadmap = planning.get("pages") or planning.get("roadmap") or []
    return isinstance(roadmap, list) and any(
        isinstance(r, dict) and (r.get("url") or r.get("path") or r.get("url_n")) for r in roadmap
    )


def _briefable_pages(planning: dict[str, Any]) -> list[dict[str, Any]]:
    pages = planning.get("pages") or planning.get("roadmap") or []
    locked = planning.get("locked") is True
    rows = []
    for r in pages:
        if not isinstance(r, dict):
            continue
        action = str(r.get("action") or "").lower()
        if action not in ("create", "refresh"):
            continue
        # A locked/approved roadmap already decided create vs optimize. Don't drop
        # those rows just because approve-time memory slimming stripped evidence fields.
        if action == "create" and not locked:
            check = str(r.get("existing_content_check") or "").lower()
            basis = str(r.get("decision_basis") or "").lower()
            justified = bool(str(r.get("new_content_justification") or "").strip())
            if check not in ("no_match", "audit_not_available") or (
                basis not in _CREATE_BASIS_OK and not justified
            ):
                continue
        rows.append(r)
    rows.sort(key=lambda r: int(r.get("priority_rank") or 999))
    return rows


def parse_topic_selection(message: str | None) -> dict[str, str] | None:
    """Parse a user click / prompt that picks one priority topic to draft."""
    text = str(message or "").strip()
    if not text:
        return None
    lower = text.lower()
    if not any(h in lower for h in _SELECT_HINTS):
        return None
    url = ""
    m = re.search(r"\((https?://[^)\s]+|/[^)\s]+)\)", text)
    if m:
        url = m.group(1).strip()
    m2 = re.search(r"(?:url|path)\s*[:=]\s*(\S+)", text, re.IGNORECASE)
    if m2:
        url = m2.group(1).strip().rstrip(".,)")
    keyword = ""
    m3 = re.search(r"(?:for|topic)\s*[:]\s*(.+?)(?:\s*\(|$)", text, re.IGNORECASE)
    if m3:
        keyword = m3.group(1).strip(" :")
    if not url and not keyword:
        return None
    return {"url": url, "keyword": keyword}


def _norm_topic(val: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(val or "").lower()).strip()


def _url_path(val: Any) -> str:
    raw = str(val or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return (urlparse(raw).path or "/").rstrip("/").lower()
    return raw.rstrip("/").lower()


def pick_brief(
    briefs: list[dict[str, Any]],
    *,
    selected_url: str | None = None,
    selected_keyword: str | None = None,
) -> dict[str, Any] | None:
    want_url = _url_path(selected_url)
    want_kw = _norm_topic(selected_keyword)
    if want_url:
        for b in briefs:
            got = _url_path(b.get("url") or b.get("path"))
            if got and (got == want_url or got.endswith(want_url) or want_url.endswith(got)):
                return b
    if want_kw:
        for b in briefs:
            got = _norm_topic(b.get("keyword") or b.get("title"))
            if got and (got == want_kw or want_kw in got or got in want_kw):
                return b
    return None


def topic_choices(
    briefs: list[dict[str, Any]],
    *,
    pages: list[dict[str, Any]] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Pages the operator can draft — full roadmap in a dropdown, not a short list.

    Writer-ready briefs come first (with richer labels). Any remaining briefable
    roadmap pages are appended so selection is not capped at eight. Selecting a
    page that was not pre-briefed triggers a brief for that page alone.
    """
    ready = [b for b in briefs if isinstance(b, dict) and b.get("writer_ready")]
    ready.sort(
        key=lambda r: (
            0 if r.get("from_selected_service") else 1,
            int(r.get("priority_rank") or r.get("wave") or 999),
        )
    )
    selected = [b for b in ready if b.get("from_selected_service")]
    others = [b for b in ready if not b.get("from_selected_service")]
    ordered = selected + others

    seen_urls: set[str] = set()
    seen_kws: set[str] = set()
    rows: list[dict[str, Any]] = []

    def _append(row: dict[str, Any]) -> None:
        kw = str(row.get("keyword") or row.get("primary_keyword") or row.get("title") or "topic")
        url = str(row.get("url") or row.get("path") or row.get("url_n") or "")
        url_key = _url_path(url)
        kw_key = _norm_topic(kw)
        if url_key and url_key in seen_urls:
            return
        if not url_key and kw_key and kw_key in seen_kws:
            return
        if url_key:
            seen_urls.add(url_key)
        if kw_key:
            seen_kws.add(kw_key)
        rows.append(
            {
                "rank": len(rows) + 1,
                "keyword": kw,
                "title": row.get("title") or kw,
                "url": url,
                "action": row.get("action") or row.get("recommended_action"),
                "work_label": row.get("work_label"),
                "from_selected_service": row.get("from_selected_service"),
                "funnel": row.get("funnel"),
                "intent": row.get("search_intent") or row.get("intent"),
                "writer_ready": bool(row.get("writer_ready")),
                "page_type": row.get("page_type") or row.get("content_type"),
                "content_type": row.get("content_type") or row.get("page_type"),
                "primary_keyword": row.get("primary_keyword") or kw,
                "secondary_keywords": list(
                    row.get("secondary_keywords")
                    or row.get("supporting_keywords")
                    or []
                ),
                "meta_description": row.get("meta_description"),
                "content_goal": row.get("content_goal"),
                "differentiation": row.get("differentiation"),
                "outline": row.get("outline"),
                "who_how_why": row.get("who_how_why"),
                "prompt": (
                    f"Write the full draft for: {kw} ({url})"
                    if url
                    else f"Write the full draft for: {kw}"
                ),
            }
        )

    for b in ordered:
        if len(rows) >= int(limit):
            break
        _append({**b, "writer_ready": True})

    for page in pages or []:
        if len(rows) >= int(limit):
            break
        if not isinstance(page, dict):
            continue
        _append(page)

    return rows


def _comparison_rivals(
    brief: dict[str, Any],
    *,
    competitors: list[dict[str, Any]] | None,
    commercial: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Service-scoped rivals when the brief names a service; otherwise the client set.

    Matches the content-signal builder: a non-empty per-service pick wins, and an
    empty pick falls back to the stored client competitors. Capped at three.
    """
    from app.services.create_content import COMPARISON_RIVAL_CAP
    from app.services.service_competitors import resolve_service_competitors

    rows = [row for row in (competitors or []) if isinstance(row, dict)]
    service_id = str(brief.get("service_id") or brief.get("service") or "").strip()
    if service_id:
        scoped = resolve_service_competitors(
            commercial, service_id=service_id, competitor_rows=rows
        )
        if scoped:
            rows = scoped
    return rows[:COMPARISON_RIVAL_CAP]


async def _point_rivals_at_ranking_pages(
    rivals: list[dict[str, Any]],
    keyword: str,
) -> list[dict[str, Any]]:
    """Point each rival at the URL that ranks for this keyword, when DataForSEO has one."""
    from app.integrations import dataforseo

    pointed: list[dict[str, Any]] = []
    for rival in rivals:
        row = dict(rival)
        domain = str(row.get("domain") or row.get("url") or "")
        try:
            searches, _errors = await dataforseo.top_searches(domain, limit=20)
        except Exception as exc:  # noqa: BLE001
            log.warning("comparison_top_searches_failed", error=str(exc)[:200])
            pointed.append(row)
            continue
        match = dataforseo.pick_ranking_url(searches, keyword)
        if match and match.get("url"):
            row["url"] = match["url"]
            row["ranking_keyword"] = match["keyword"]
            if match.get("position") is not None:
                row["ranking_position"] = match["position"]
        pointed.append(row)
    return pointed


async def _gather_comparison_pages(
    rivals: list[dict[str, Any]],
    *,
    keyword: str,
    snapshots: list[dict[str, Any]],
) -> list[Any]:
    """Live rival pages, then cached headings. Failures stay in the list."""
    from app.services.content_benchmark.fetch import gather_competitor_content
    from app.services.content_benchmark.report import service_terms

    if not rivals:
        return []
    rivals = await _point_rivals_at_ranking_pages(rivals, keyword)
    try:
        return await gather_competitor_content(
            rivals,
            snapshots=snapshots,
            service_terms=service_terms(keyword, [keyword]),
            allow_live=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("comparison_evidence_failed", error=str(exc)[:240])
        return []


def _existing_excerpt(website: dict[str, Any], url: str | None) -> str | None:
    if not url:
        return None
    want = str(url).lower().rstrip("/")
    for key in ("sample_pages", "pages", "top_pages", "crawled_pages"):
        for row in website.get(key) or []:
            if not isinstance(row, dict):
                continue
            path = str(row.get("url") or row.get("path") or "").lower().rstrip("/")
            if path.endswith(want) or want.endswith(path.strip("/")):
                text = row.get("text") or row.get("excerpt") or row.get("title")
                if text:
                    return str(text)[:800]
    return None


async def run_content_production_plan(
    *,
    client_name: str,
    primary_url: str,
    content_planning_status: str | None = None,
    content_planning: dict[str, Any] | None = None,
    seo_strategy: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    website: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    on_page_seo: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    content_context_builder: Any | None = None,
    industry: str | None = None,
    limit: int = 8,
    write_all: bool = False,
    selected_url: str | None = None,
    selected_keyword: str | None = None,
    prior_briefs: list[dict[str, Any]] | None = None,
    google_policy: dict[str, Any] | None = None,
    competitors: list[dict[str, Any]] | None = None,
    commercial: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Brief the locked queue; wait for a topic click; write **one** full draft."""
    planning = dict(content_planning or {})
    site = dict(website or {})
    demand = dict(search_demand or {})
    if planning.get("locked") is False:
        return {
            "blocked": True,
            "reason": (
                "Phase 10 must not brief from an unlocked roadmap. "
                f"{planning.get('lock_reason') or 'Re-run Content Planning after fixing upstream packs.'}"
            ),
        }
    if not planning_gate_ok(content_planning_status, planning):
        return {
            "blocked": True,
            "reason": (
                "Content Production needs a locked Content Planning report. "
                "Run and approve Phase 9 first."
            ),
        }

    if write_all:
        from app.services.create_content import refuse_batch

        return {
            "blocked": True,
            **refuse_batch(limit),
            "client_name": client_name,
            "primary_url": primary_url,
            "source": "create_content",
        }

    pages = _briefable_pages(planning)
    from app.services.content_brief import generate_briefs
    from app.services.create_content import draft_gate, write_one_page

    reuse = [b for b in (prior_briefs or []) if isinstance(b, dict)]
    selected = bool(selected_url or selected_keyword)
    if selected and reuse:
        from app.services.content_brief import refresh_brief_author_gates

        # Re-evaluate YMYL/author standing — cached briefs can still carry a stale
        # "credentials" blocker after CDD positioning / expertise is available.
        briefs = [
            refresh_brief_author_gates(
                b,
                industry=industry,
                marketing=marketing,
                client_name=client_name,
            )
            for b in reuse
        ]
        brief_pack: dict[str, Any] = {"briefs": briefs, "skipped_new_urls": []}
    else:
        brief_pack = await generate_briefs(
            client_name=client_name,
            industry=industry,
            audience=(
                format_audience_label(dict(seo_strategy or {}).get("target_audience"))
                or None
            ),
            marketing=marketing,
            seo_strategy=seo_strategy,
            site_architecture=site_architecture,
            content_audit=content_audit,
            website=website,
            search_demand=search_demand,
            roadmap=pages,
            limit=limit,
        )
        briefs = [b for b in (brief_pack.get("briefs") or []) if isinstance(b, dict)]

    ready = [b for b in briefs if b.get("writer_ready")]
    choices = topic_choices(ready, pages=pages)
    held = [
        {
            "keyword": b.get("keyword"),
            "url": b.get("url"),
            "reason": (b.get("preflight") or {}).get("blockers")
            if isinstance(b.get("preflight"), dict)
            else ["Not writer-ready"],
        }
        for b in briefs
        if not b.get("writer_ready")
    ]

    drafts: list[dict[str, Any]] = []
    write_refusal: dict[str, Any] | None = None
    queued_ready: list[dict[str, Any]] = []
    awaiting = False

    if not choices and not ready:
        sample = []
        if briefs and isinstance(briefs[0].get("preflight"), dict):
            sample = [str(b) for b in (briefs[0]["preflight"].get("blockers") or []) if b]
        write_refusal = {
            "refused": True,
            "reason": (
                "; ".join(sample[:3])
                if sample
                else "No writer-ready brief — fix pre-flight / author / differentiation via content-brief."
            ),
            "route_to": "content_brief",
        }
    elif not selected:
        awaiting = True
        queued_ready = [
            {"keyword": c.get("keyword"), "url": c.get("url"), "reason": "Waiting for operator to pick a topic."}
            for c in choices
        ]
    else:
        chosen = pick_brief(ready, selected_url=selected_url, selected_keyword=selected_keyword)
        if not chosen:
            # Operator picked a roadmap page that was not in the pre-briefed set —
            # brief that page alone, then continue.
            target_pages = [
                p
                for p in pages
                if isinstance(p, dict)
                and (
                    (
                        selected_url
                        and _url_path(p.get("url") or p.get("path") or p.get("url_n"))
                        and (
                            _url_path(p.get("url") or p.get("path") or p.get("url_n"))
                            == _url_path(selected_url)
                            or _url_path(p.get("url") or p.get("path") or p.get("url_n")).endswith(
                                _url_path(selected_url)
                            )
                            or _url_path(selected_url).endswith(
                                _url_path(p.get("url") or p.get("path") or p.get("url_n"))
                            )
                        )
                    )
                    or (
                        selected_keyword
                        and _norm_topic(
                            p.get("keyword") or p.get("primary_keyword") or p.get("title")
                        )
                        and (
                            _norm_topic(
                                p.get("keyword") or p.get("primary_keyword") or p.get("title")
                            )
                            == _norm_topic(selected_keyword)
                            or _norm_topic(selected_keyword)
                            in _norm_topic(
                                p.get("keyword") or p.get("primary_keyword") or p.get("title")
                            )
                        )
                    )
                )
            ]
            if target_pages:
                on_demand = await generate_briefs(
                    client_name=client_name,
                    industry=industry,
                    audience=(
                        format_audience_label(dict(seo_strategy or {}).get("target_audience"))
                        or None
                    ),
                    marketing=marketing,
                    seo_strategy=seo_strategy,
                    site_architecture=site_architecture,
                    content_audit=content_audit,
                    website=website,
                    search_demand=search_demand,
                    roadmap=target_pages[:1],
                    limit=1,
                )
                on_demand_briefs = [
                    b for b in (on_demand.get("briefs") or []) if isinstance(b, dict)
                ]
                briefs = list(briefs) + on_demand_briefs
                ready = [b for b in briefs if b.get("writer_ready")]
                chosen = pick_brief(
                    ready, selected_url=selected_url, selected_keyword=selected_keyword
                ) or next((b for b in on_demand_briefs if b.get("writer_ready")), None)
                brief_pack = {
                    **brief_pack,
                    "briefs": briefs,
                    "brief_count": len(briefs),
                    "writer_ready_count": len(ready),
                }
        if not chosen:
            write_refusal = {
                "refused": True,
                "reason": (
                    f"No writer-ready brief matched “{selected_keyword or selected_url}”. "
                    "Pick one of the listed topics."
                ),
                "route_to": None,
            }
            awaiting = True
        else:
            gate = draft_gate(chosen)
            if not gate.get("ok"):
                write_refusal = {
                    "refused": True,
                    "reason": gate.get("reason"),
                    "route_to": gate.get("route_to"),
                    "keyword": chosen.get("keyword"),
                    "url": chosen.get("url"),
                }
            else:
                excerpt = None
                if str(chosen.get("action") or "").lower() == "refresh":
                    excerpt = _existing_excerpt(
                        site, str(chosen.get("url") or chosen.get("path") or "")
                    )
                mkt = dict(marketing or {})
                intake = dict(mkt.get("client_intake") or {})
                location = str(
                    mkt.get("geographic_focus")
                    or mkt.get("location")
                    or intake.get("geographic_focus")
                    or (seo_strategy or {}).get("location")
                    or ""
                ).strip() or None
                content_context = None
                if content_context_builder is not None:
                    from app.services.create_content import resolve_page_type

                    try:
                        content_context = await content_context_builder(
                            keyword=str(chosen.get("keyword") or chosen.get("title") or ""),
                            page_type=resolve_page_type(chosen),
                            service_id=str(
                                chosen.get("service_id")
                                or chosen.get("service")
                                or ""
                            ).strip() or None,
                        )
                    except Exception as exc:  # noqa: BLE001
                        # Signals are an enhancement; losing them must not stop the draft.
                        log.warning("content_context_failed", error=str(exc)[:240])
                from app.services.create_content import resolve_page_type

                comparison_pages = None
                if resolve_page_type(chosen) == "comparison":
                    rivals = _comparison_rivals(
                        chosen,
                        competitors=competitors,
                        commercial=commercial,
                    )
                    comparison_pages = await _gather_comparison_pages(
                        rivals,
                        keyword=str(chosen.get("keyword") or chosen.get("title") or ""),
                        snapshots=list(demand.get("competitor_sites") or []),
                    )
                written = await write_one_page(
                    brief=chosen,
                    client_name=client_name,
                    content_context=content_context,
                    existing_excerpt=excerpt,
                    use_llm=True,
                    seo_strategy=seo_strategy,
                    marketing=marketing,
                    industry=industry,
                    location=location,
                    search_demand=search_demand,
                    google_policy=google_policy,
                    comparison_pages=comparison_pages,
                )
                if written.get("ok"):
                    # The client-site preview is built on demand from the draft card
                    # (app.services.site_preview), not during the write.
                    drafts.append(written)
                    queued_ready = [
                        {
                            "keyword": c.get("keyword"),
                            "url": c.get("url"),
                            "reason": "Queued — one page per create-content run (scaled content abuse).",
                        }
                        for c in choices
                        if (c.get("url") or c.get("keyword"))
                        not in {chosen.get("url"), chosen.get("keyword")}
                    ]
                else:
                    write_refusal = written
                    awaiting = True

    return {
        "client_name": client_name,
        "primary_url": primary_url,
        "briefs": briefs,
        "drafts": drafts,
        "held_briefs": held,
        "queued_for_next_write": queued_ready,
        "topic_choices": choices,
        "awaiting_topic_selection": awaiting,
        "selected_url": selected_url,
        "selected_keyword": selected_keyword,
        "write_refusal": write_refusal,
        "skipped_new_urls": brief_pack.get("skipped_new_urls") or [],
        "brief_count": len(briefs),
        "draft_count": len(drafts),
        "writer_ready_count": len(ready),
        "funnel_balance": _funnel_balance(briefs),
        "funnel_balance_warnings": _funnel_balance_warnings(_funnel_balance(briefs)),
        "one_page_per_run": True,
        "translation": "excluded",
        "source": "content_production",
        "note": (
            "Use the dropdown to pick one page, then draft it. create-content writes ONE full "
            "page per click (scaled content abuse). Named human must review before publish. "
            "Translation excluded."
            if awaiting
            else (
                "Full draft for the selected topic. Named human must review before publish. "
                "Pick another page from the dropdown to write the next one. Translation excluded."
            )
        ),
    }
