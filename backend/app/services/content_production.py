"""Phase 10 — Content briefs + one create-content draft from a locked roadmap."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.services.create_topic import funnel_balance as _funnel_balance
from app.services.create_topic import funnel_balance_warnings as _funnel_balance_warnings

_SELECT_HINTS = (
    "write the full draft",
    "write full draft",
    "write the content for",
    "write content for",
    "draft this topic",
    "draft topic",
    "select topic",
)


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
    rows = []
    for r in pages:
        if not isinstance(r, dict):
            continue
        action = str(r.get("action") or "").lower()
        if action not in ("create", "refresh"):
            continue
        if action == "create":
            check = str(r.get("existing_content_check") or "").lower()
            basis = str(r.get("decision_basis") or "").lower()
            if check not in ("no_match", "audit_not_available") or basis not in (
                "new_content_gap",
                "strategy_gap",
            ):
                # Existing-content-first: don't brief net-new pages without explicit gap evidence.
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
    m2 = re.search(r"(?:url|path)\s*[:=]\s*(\S+)", text, re.I)
    if m2:
        url = m2.group(1).strip().rstrip(".,)")
    keyword = ""
    m3 = re.search(r"(?:for|topic)\s*[:]\s*(.+?)(?:\s*\(|$)", text, re.I)
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


def topic_choices(briefs: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    ready = [b for b in briefs if isinstance(b, dict) and b.get("writer_ready")]
    ready.sort(key=lambda r: int(r.get("priority_rank") or r.get("wave") or 999))
    rows: list[dict[str, Any]] = []
    for i, b in enumerate(ready[:limit]):
        kw = str(b.get("keyword") or b.get("title") or "topic")
        url = str(b.get("url") or b.get("path") or "")
        rows.append(
            {
                "rank": i + 1,
                "keyword": kw,
                "title": b.get("title") or kw,
                "url": url,
                "action": b.get("action"),
                "funnel": b.get("funnel"),
                "intent": b.get("search_intent"),
                "prompt": f"Write the full draft for: {kw} ({url})" if url else f"Write the full draft for: {kw}",
            }
        )
    return rows


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
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    limit: int = 8,
    write_all: bool = False,
    selected_url: str | None = None,
    selected_keyword: str | None = None,
    prior_briefs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Brief the locked queue; wait for a topic click; write **one** full draft."""
    planning = dict(content_planning or {})
    site = dict(website or {})
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
        briefs = reuse
        brief_pack: dict[str, Any] = {"briefs": briefs, "skipped_new_urls": []}
    else:
        brief_pack = await generate_briefs(
            client_name=client_name,
            industry=industry,
            audience=str((dict(seo_strategy or {}).get("target_audience") or "") or "") or None,
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
    choices = topic_choices(ready)
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

    if not ready:
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
                written = await write_one_page(
                    brief=chosen,
                    client_name=client_name,
                    existing_excerpt=excerpt,
                    use_llm=True,
                    seo_strategy=seo_strategy,
                    marketing=marketing,
                    industry=industry,
                    search_demand=search_demand,
                )
                if written.get("ok"):
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
            "Pick one priority topic to draft. create-content writes ONE full page per click "
            "(scaled content abuse). Named human must review before publish. Translation excluded."
            if awaiting
            else (
                "Full draft for the selected topic. Named human must review before publish. "
                "Pick another topic to write the next page. Translation excluded."
            )
        ),
    }
