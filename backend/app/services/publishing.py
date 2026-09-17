"""Phase 12 — Publishing & indexation.

Three modes, matching Architecture v1.9 steps 12→14:

* ``preview``  (default) — dry-run only. Fetches brand + reference layout, renders the
  exact CMS payload for review. No write of any kind.
* ``draft``    — writes to WordPress as a draft/pending post, then re-reads it back.
* ``publish``  — same, requesting live status. Honoured only when the deployment sets
  ``WORDPRESS_ALLOW_LIVE_PUBLISH``; otherwise the write is downgraded to draft and the
  downgrade is reported, never silently swallowed.

Verification is mandatory after any write (v1.9 step 14: "never a silent republish") —
what the CMS actually stored is read back and compared to what was sent. IndexNow stays a
payload preview; nothing is submitted to a search engine from this build.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.integrations import brandfetch, firecrawl, wordpress
from app.integrations.wordpress import WordPressConnection
from app.logging_config import get_logger
from app.services.publish_preview import build_content_html, build_page_preview, preview_design_meta
from app.services.wp_content import validate_publish_payload
from app.services.wp_publish_strategy import PublishTarget, publish_to_wordpress

log = get_logger("publishing")

MODE_PREVIEW = "preview"
MODE_DRAFT = "draft"
MODE_PUBLISH = "publish"
VALID_MODES = (MODE_PREVIEW, MODE_DRAFT, MODE_PUBLISH)

MAX_PAGES = 8


def _host(url: str) -> str:
    return urlparse(url if "://" in url else f"https://{url}").netloc.lower()


def on_page_gate_ok(on_page_status: str | None, on_page: dict[str, Any]) -> bool:
    if (on_page_status or "") == "complete":
        return True
    return bool(on_page.get("pages") or on_page.get("queue"))


def _matches_url(candidate: str, want: str) -> bool:
    cand = (candidate or "").rstrip("/").lower()
    if not cand:
        return False
    return cand == want or cand.endswith(urlparse(want).path or "\x00")


def _brief_for(url: str, production: dict[str, Any]) -> dict[str, Any]:
    want = (url or "").rstrip("/").lower()
    for b in production.get("briefs") or []:
        if isinstance(b, dict) and _matches_url(str(b.get("url") or ""), want):
            return b
    return {}


def _draft_for(url: str, production: dict[str, Any]) -> dict[str, Any]:
    """The written Phase 10 draft for this URL, if one exists.

    Without this the publisher only ever saw the *brief*, and rendered its outline
    into heading-plus-placeholder markup — publishing a skeleton where the finished
    article already existed alongside it in the same pack.
    """
    want = (url or "").rstrip("/").lower()
    for d in production.get("drafts") or []:
        if isinstance(d, dict) and _matches_url(str(d.get("url") or ""), want):
            return d
    return {}


def _content_for(
    page: dict[str, Any], brief: dict[str, Any], draft: dict[str, Any]
) -> tuple[str, str]:
    """(content_html, source). Prefers the written draft over the brief outline."""
    from app.services.wp_content import markdown_to_publish_html

    markdown = str(draft.get("markdown") or "")
    if markdown.strip():
        rendered = markdown_to_publish_html(
            markdown,
            images=draft.get("images") if isinstance(draft.get("images"), list) else None,
            media_base=str(draft.get("media_base") or "") or None,
        )
        if rendered.strip():
            return rendered, "phase10_draft"
    return build_content_html(page, brief), "brief_outline"


def platform_capability_check(
    connection: dict[str, Any], *, mode: str, has_connection: bool
) -> list[dict[str, Any]]:
    """v1.9 guardrail — surface CMS limits as external blockers, not mid-publish failures."""
    blockers: list[dict[str, Any]] = []
    if mode == MODE_PREVIEW:
        return blockers
    if not has_connection:
        blockers.append(
            {
                "blocker": "wordpress_not_connected",
                "detail": "No WordPress site connected for this client.",
                "resolution": "Connect WordPress from Workspace → WordPress, then re-run.",
            }
        )
        return blockers
    if not connection.get("ok"):
        blockers.append(
            {
                "blocker": "cms_unreachable",
                "detail": str(connection.get("error") or "WordPress did not respond"),
                "resolution": "Reconnect WordPress — the site or Application Password may have changed.",
            }
        )
        return blockers
    if mode == MODE_PUBLISH and connection.get("capabilities_publish") is False:
        blockers.append(
            {
                "blocker": "insufficient_cms_rights",
                "detail": f"WordPress user '{connection.get('user')}' cannot publish posts.",
                "resolution": "Grant the Editor/Author role, or publish as draft.",
            }
        )
    return blockers


async def run_publishing_plan(
    *,
    client_name: str,
    primary_url: str,
    on_page_seo_status: str | None = None,
    on_page_seo: dict[str, Any] | None = None,
    site_architecture: dict[str, Any] | None = None,
    content_production: dict[str, Any] | None = None,
    mode: str = MODE_PREVIEW,
    reference_url: str | None = None,
    wordpress_connection: WordPressConnection | None = None,
) -> dict[str, Any]:
    on_page = dict(on_page_seo or {})
    ia = dict(site_architecture or {})
    production = dict(content_production or {})
    mode = (mode or MODE_PREVIEW).lower()
    if mode not in VALID_MODES:
        mode = MODE_PREVIEW

    if not on_page_gate_ok(on_page_seo_status, on_page):
        return {
            "blocked": True,
            "reason": (
                "Publishing needs an approved On-Page SEO package. "
                "Run and approve Phase 11 first."
            ),
        }

    pages = [
        p
        for p in (on_page.get("pages") or on_page.get("queue") or [])
        if isinstance(p, dict) and (p.get("url") or p.get("path"))
    ]
    if not pages:
        return {"blocked": True, "reason": "On-Page package has no URLs to publish."}

    host = _host(primary_url)

    # --- design fetch (brand + live reference layout) ---------------------------------
    brand = await brandfetch.fetch_brand(primary_url)
    from app.services.wp_site_design import fetch_wordpress_design

    site_design = await fetch_wordpress_design(primary_url)
    from app.services.design_capture import capture_page_design, design_source_url, merge_site_design

    ref_url = reference_url or _pick_reference_url(ia, primary_url)
    source_url = design_source_url(
        existing_page_url=str((pages[0] if pages else {}).get("existing_page_url") or ""),
        parent_pillar_page_url=str(ia.get("parent_pillar_page_url") or ""),
        reference_design_source=ref_url,
        client_website_url=primary_url,
    )
    site_design = merge_site_design(site_design, await capture_page_design(source_url))
    scrape = await firecrawl.scrape_page(ref_url) if ref_url else {"available": False}
    layout = firecrawl.layout_hints(scrape)

    has_connection = wordpress.connection_configured(wordpress_connection)
    connection = (
        await wordpress.verify_connection(wordpress_connection)
        if mode != MODE_PREVIEW and has_connection
        else {"ok": has_connection, "skipped": True}
    )
    capability_blockers = platform_capability_check(
        connection, mode=mode, has_connection=has_connection
    )
    effective_mode = MODE_PREVIEW if capability_blockers else mode

    # What this particular site can accept — Elementor, which SEO plugin, whether the
    # pages/media endpoints exist. Probed once per run, and only when we intend to write.
    site_capabilities: dict[str, Any] = {}
    if effective_mode != MODE_PREVIEW and wordpress_connection is not None:
        try:
            site_capabilities = await wordpress.detect_site_capabilities(wordpress_connection)
        except Exception as exc:  # noqa: BLE001
            log.warning("capability_probe_failed", error=str(exc))
            site_capabilities = {"error": str(exc)}

    target_status = {
        MODE_PREVIEW: "draft",
        MODE_DRAFT: "draft",
        MODE_PUBLISH: "publish",
    }[effective_mode]

    previews: list[dict[str, Any]] = []
    publish_queue: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    url_list: list[str] = []

    # Pages beyond the per-run cap used to vanish with no trace — a reviewer saw a
    # "complete" run that had quietly skipped most of the queue.
    deferred = [
        {
            "url": str(p.get("url") or p.get("path") or ""),
            "keyword": p.get("keyword"),
            "reason": f"Beyond the {MAX_PAGES}-page per-run cap — re-run to continue.",
        }
        for p in pages[MAX_PAGES:]
    ]

    for page in pages[:MAX_PAGES]:
        url = str(page.get("url") or "")
        if not url.startswith("http"):
            url = primary_url.rstrip("/") + (url if url.startswith("/") else f"/{url}")
        url_list.append(url)
        slug = wordpress.slug_from_url(url) or wordpress.slugify(str(page.get("keyword") or ""))
        brief = _brief_for(url, production)
        draft = _draft_for(url, production)
        content_html, content_source = _content_for(page, brief, draft)

        preview = build_page_preview(
            client_name=client_name,
            page={**page, "url": url},
            brief=brief,
            brand=brand,
            layout=layout,
            site_design=site_design,
            target_status=target_status,
            slug=slug,
            content_html=content_html,
        )
        preview["content_source"] = content_source
        payload = preview["cms_payload"]

        # Run the same gate in preview so a reviewer sees what would block a real run.
        preview["validation"] = validate_publish_payload(
            payload, require_written_copy=(content_source == "phase10_draft")
        )
        previews.append(preview)

        if effective_mode == MODE_PREVIEW:
            publish_queue.append(
                {
                    "url": url,
                    "slug": slug,
                    "title": payload["title"],
                    "keyword": page.get("keyword"),
                    "status": "preview_only",
                    "cms": "wordpress" if has_connection else "not_connected",
                    "content_source": content_source,
                    "would_block": not preview["validation"]["ok"],
                    "note": "Dry run — no CMS write attempted",
                }
            )
            continue

        target = PublishTarget(
            url=url,
            slug=slug,
            title=payload["title"],
            content_html=payload["content"],
            excerpt=payload["excerpt"],
            page_type=str(page.get("page_type") or draft.get("page_type") or "") or None,
            status=target_status,
            images=[i for i in (draft.get("images") or []) if isinstance(i, dict)],
            seo_description=payload["excerpt"],
        )
        trace = await publish_to_wordpress(
            wordpress_connection,
            target,
            capabilities=site_capabilities,
            # A brief outline is a skeleton, not an article — never write it live.
            require_written_copy=True,
        )
        write = trace.get("write") or {}
        entry = {
            "url": url,
            "slug": slug,
            "title": payload["title"],
            "keyword": page.get("keyword"),
            "status": write.get("status") if trace.get("ok") or write.get("ok") else "failed",
            "cms": "wordpress",
            "content_source": content_source,
            "strategy": trace.get("strategy"),
            "post_type": trace.get("post_type"),
            "post_id": write.get("post_id"),
            "link": write.get("link"),
            "edit_link": write.get("edit_link"),
            "action": write.get("action"),
        }
        if trace.get("error"):
            entry["error"] = trace.get("error")
            entry["detail"] = trace.get("detail") or trace.get("strategy_reason")
            entry["failed_stage"] = trace.get("stage")
            if trace.get("validation") and not trace["validation"].get("ok"):
                entry["validation_errors"] = trace["validation"]["errors"]
            if trace.get("blockers"):
                entry["blockers"] = trace["blockers"]
                capability_blockers.extend(trace["blockers"])
        if write.get("downgraded"):
            entry["downgraded"] = True
            entry["downgrade_reason"] = write.get("downgrade_reason")
        publish_queue.append(entry)

        verification.append(
            trace.get("verification")
            or {
                "slug": slug,
                "verified": False,
                "reason": trace.get("error") or "write_failed",
                "failed_stage": trace.get("stage"),
                "action_required": "Human review — the CMS write did not succeed.",
            }
        )

    checklist = _checklist(ia, effective_mode)
    indexnow_preview = {
        "host": host,
        "key": "<generate-and-host-before-first-submit>",
        "keyLocation": f"https://{host}/<indexnow-key>.txt",
        "urlList": url_list[:100],
        "note": "Preview only — IndexNow is never submitted from this build.",
    }

    design = preview_design_meta(brand, site_design)
    summary: dict[str, Any] = {
        "client_name": client_name,
        "primary_url": primary_url,
        "mode_requested": mode,
        "mode_effective": effective_mode,
        "cms_mode": "wordpress" if has_connection else "not_connected",
        "cms_connection": {k: v for k, v in connection.items() if k != "error"}
        | ({"error": connection.get("error")} if connection.get("error") else {}),
        "capability_blockers": capability_blockers,
        "site_capabilities": site_capabilities,
        "design": {
            "brand_available": bool(brand.get("available")),
            "brand_error": brand.get("error"),
            "brand_name": brand.get("name"),
            "logo_url": (brand.get("logo") or {}).get("url"),
            "primary_color": site_design.get("primary_color") or brand.get("primary_color"),
            "palette": brand.get("palette"),
            "font": site_design.get("font") or brand.get("font"),
            "design_source": design["source"],
            "design_fallback": design.get("fallback") or None,
            "wordpress_kit_id": site_design.get("kit_id") or None,
            "design_md": ((site_design.get("design_capture") or {}).get("design_md") or "")[:8000],
            "screenshots": (site_design.get("design_capture") or {}).get("screenshots") or [],
            "reference_url": layout.get("reference_url") or ref_url,
            "reference_available": bool(layout.get("available")),
            "reference_error": scrape.get("error") if not layout.get("available") else None,
        },
        "previews": previews,
        "publish_queue": publish_queue,
        "deferred_pages": deferred,
        "pages_in_package": len(pages),
        "verification": verification,
        "publish_checklist": checklist,
        "indexnow_preview": indexnow_preview,
        "gsc_recrawl": [
            {"url": u, "action": "URL_INSPECTION_REQUEST", "status": "queued_manual"}
            for u in url_list[:20]
        ],
        "qa_checklist": _qa_checklist(effective_mode),
        "source": "publishing",
    }
    summary["note"] = _note(effective_mode, mode, capability_blockers, verification)
    return summary


def _pick_reference_url(ia: dict[str, Any], primary_url: str) -> str:
    """A live page to frame the preview against — prefer an existing hub, else the home."""
    for node in ia.get("target_url_tree") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "").lower() in ("hub", "service") and node.get("url"):
            url = str(node["url"])
            if url.startswith("http"):
                return url
            return primary_url.rstrip("/") + (url if url.startswith("/") else f"/{url}")
    return primary_url


# Verification moved to wp_publish_strategy.verify_publication(), which checks the
# stored content, Elementor data, SEO meta and the live public page rather than
# only the title and status this used to compare.
def _checklist(ia: dict[str, Any], mode: str) -> list[str]:
    items = [
        "Confirm title / H1 / meta match the approved on-page package",
        "Embed approved JSON-LD schema",
        "Verify internal links resolve 200",
        "Replace outlined sections with written copy before go-live",
        "Confirm canonical + sitemap entry post-publish",
        "Spot-check robots.txt does not block the new URLs",
    ]
    if mode == MODE_PREVIEW:
        items.insert(0, "Review dry-run preview and approve before any CMS write")
    else:
        items.insert(0, "Re-read verification block — confirm CMS stored what was sent")
    rollout = ia.get("rollout_plan") or ia.get("implementation_waves") or []
    if rollout:
        items.append(f"Follow IA rollout notes ({len(rollout)} items)")
    return items


def _qa_checklist(mode: str) -> list[dict[str, Any]]:
    return [
        {"item": "Live title matches package", "status": "pending"},
        {"item": "Canonical self-references", "status": "pending"},
        {"item": "Sitemap includes new URLs", "status": "pending"},
        {"item": "No accidental noindex", "status": "pending"},
        {"item": "Schema validates in Rich Results test", "status": "pending"},
        {
            "item": "IndexNow key hosted before first submission",
            "status": "not_applicable" if mode == MODE_PREVIEW else "pending",
        },
    ]


def _note(
    effective: str,
    requested: str,
    blockers: list[dict[str, Any]],
    verification: list[dict[str, Any]],
) -> str:
    if blockers:
        return (
            f"Requested '{requested}' but fell back to preview — "
            f"{blockers[0]['blocker']}: {blockers[0]['detail']}"
        )
    if effective == MODE_PREVIEW:
        return "Dry-run preview only — no CMS write attempted. Approve, then run publish."
    failed = [v for v in verification if not v.get("verified")]
    downgraded = any(v.get("stored_status") == "draft" for v in verification)
    base = f"WordPress write complete ({len(verification)} page(s))."
    if failed:
        return base + f" {len(failed)} could not be verified — human review required."
    if downgraded and effective == MODE_PUBLISH:
        return base + " Live publish was downgraded to draft by configuration."
    return base + " All writes verified against the CMS."
