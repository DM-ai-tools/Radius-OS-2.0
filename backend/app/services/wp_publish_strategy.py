"""Publishing strategy resolution and execution.

    content + target
          ↓
    validate (wp_content)
          ↓
    locate existing object (slug across pages AND posts)
          ↓
    resolve strategy ──┬── standard   → post_content HTML (Gutenberg/Classic both render it)
                       └── elementor  → merge into _elementor_data, preserving layout
          ↓
    media upload → content rewrite
          ↓
    write
          ↓
    verify (object + status + content + Elementor data + public page)

The resolver never assumes an editor. It reads what the target actually is and picks
accordingly, and where it cannot write safely it returns a blocker rather than a write
that would report success while changing nothing a visitor can see.

On browser automation: Playwright is available in this project (``live_site_scan``
uses it to render pages for reading). It is deliberately **not** used as a publishing
fallback here — driving wp-admin requires the account password, whereas this pipeline
only ever holds an Application Password, which cannot log into wp-admin. Adding that
fallback would mean asking clients to store their real WordPress password, which is a
larger security regression than the gap it closes. See ``ELEMENTOR_WRITE_BLOCKER``
for the supported remediation instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations import wordpress
from app.integrations.wordpress import WordPressConnection
from app.logging_config import get_logger
from app.services import wp_elementor
from app.services.wp_content import strip_unresolved_images, validate_publish_payload

log = get_logger("wp_publish")

STRATEGY_STANDARD = "standard_rest"
STRATEGY_ELEMENTOR = "elementor"
STRATEGY_BLOCKED = "blocked"

# SEO plugin → (title meta key, description meta key). Rank Math's keys are the only
# ones not underscore-prefixed, so they are the most likely to be REST-writable on a
# stock install; the others usually need the site to register them.
SEO_META_KEYS = {
    "yoast_seo": ("_yoast_wpseo_title", "_yoast_wpseo_metadesc"),
    "rank_math": ("rank_math_title", "rank_math_description"),
    "all_in_one_seo": ("_aioseo_title", "_aioseo_description"),
    "seopress": ("_seopress_titles_title", "_seopress_titles_desc"),
}

ELEMENTOR_WRITE_BLOCKER = {
    "blocker": "elementor_meta_not_writable",
    "detail": (
        "This page is built with Elementor, but '_elementor_data' is not exposed through "
        "the WordPress REST API. Writing only post_content would return HTTP 200 and "
        "change nothing on the live page."
    ),
    "resolution": (
        "On the target site, register the Elementor meta keys for REST "
        "(register_post_meta with show_in_rest and an auth_callback limited to "
        "edit_posts), or publish this URL as a standard (non-Elementor) page."
    ),
}


@dataclass
class PublishTarget:
    """Everything the publishing layer needs about one destination."""

    url: str
    slug: str
    title: str
    content_html: str
    excerpt: str = ""
    post_type: str | None = None
    page_type: str | None = None
    status: str = "draft"
    images: list[dict[str, Any]] = field(default_factory=list)
    featured_image: dict[str, Any] | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    categories: list[int] = field(default_factory=list)
    tags: list[int] = field(default_factory=list)

    def slot_key(self) -> str:
        """Stable identity for the managed Elementor element on this target.

        Deliberately the slug alone. ``post_type`` is unknown when the target is
        built and only resolved during strategy resolution, so including it would
        change the derived element id between runs — and a changed id means the next
        publish appends a second managed widget instead of updating the first.
        """
        return self.slug


@dataclass
class StrategyDecision:
    strategy: str
    post_type: str
    existing: dict[str, Any] | None
    reason: str
    elementor: dict[str, Any] = field(default_factory=dict)
    blockers: list[dict[str, Any]] = field(default_factory=list)


async def resolve_strategy(
    conn: WordPressConnection,
    target: PublishTarget,
    *,
    capabilities: dict[str, Any] | None = None,
) -> StrategyDecision:
    """Decide how to write this target, based on what the target actually is.

    ``capabilities`` (site-wide plugin detection) is context only. Whether a *page*
    is Elementor-managed is read from the object itself, because a site can have
    Elementor active while most of its pages are plain — and the reverse, where the
    plugin probe fails but the page is clearly builder-rendered.
    """
    site_elementor = bool((capabilities or {}).get("elementor_active"))
    existing, found_type = await wordpress.find_object_anywhere(conn, target.slug)
    post_type = (
        found_type
        or target.post_type
        or wordpress.infer_post_type(target.url, page_type=target.page_type)
    )

    if not existing:
        # Nothing there yet. A new object renders post_content under both Gutenberg
        # and the Classic editor, so standard REST is the simplest reliable path —
        # we do not impose Elementor on a page that has no reason to use it.
        return StrategyDecision(
            strategy=STRATEGY_STANDARD,
            post_type=post_type,
            existing=None,
            reason=(
                f"No existing object at slug '{target.slug}' — creating a new "
                f"{post_type[:-1]} whose post_content renders in Gutenberg and Classic alike."
            ),
        )

    if not wp_elementor.is_elementor_post(existing):
        return StrategyDecision(
            strategy=STRATEGY_STANDARD,
            post_type=post_type,
            existing=existing,
            reason=(
                f"Existing {post_type[:-1]} #{existing.get('id')} is not Elementor-managed — "
                "updating post_content directly."
            ),
        )

    # Elementor renders this page. post_content will not be shown, so we must be able
    # to write _elementor_data or we must not claim to have published.
    meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
    raw_data = meta.get(wp_elementor.DATA_KEY)
    writable = wp_elementor.DATA_KEY in meta

    if not writable:
        return StrategyDecision(
            strategy=STRATEGY_BLOCKED,
            post_type=post_type,
            existing=existing,
            reason=(
                f"Object #{existing.get('id')} is Elementor-managed but its Elementor data "
                "is not readable or writable over REST."
            ),
            elementor={
                "detected": True,
                "writable": False,
                "plugin_active_site_wide": site_elementor,
            },
            blockers=[dict(ELEMENTOR_WRITE_BLOCKER)],
        )

    tree = wp_elementor.parse_elementor_data(raw_data)
    if raw_data and not tree:
        # Data present but unparseable — refuse rather than replace a layout we
        # could not read.
        return StrategyDecision(
            strategy=STRATEGY_BLOCKED,
            post_type=post_type,
            existing=existing,
            reason=(
                f"Object #{existing.get('id')} has Elementor data that could not be parsed. "
                "Refusing to overwrite a layout we cannot read."
            ),
            elementor={"detected": True, "writable": True, "parse_failed": True},
            blockers=[
                {
                    "blocker": "elementor_data_unparseable",
                    "detail": "_elementor_data is present but is not valid JSON.",
                    "resolution": "Open the page in Elementor and re-save it, then re-run.",
                }
            ],
        )

    return StrategyDecision(
        strategy=STRATEGY_ELEMENTOR,
        post_type=post_type,
        existing=existing,
        reason=(
            f"Object #{existing.get('id')} is Elementor-managed with a readable layout "
            f"({wp_elementor.count_widgets(tree)} widgets) — merging content into it."
        ),
        elementor={
            "detected": True,
            "writable": True,
            "widget_count": wp_elementor.count_widgets(tree),
            "uses_containers": wp_elementor.document_uses_containers(tree),
            "tree": tree,
        },
    )


def build_seo_meta(
    plugin: str | None, *, seo_title: str | None, seo_description: str | None
) -> dict[str, Any]:
    """Meta payload for the detected SEO plugin. Empty when none is detected."""
    keys = SEO_META_KEYS.get(str(plugin or ""))
    if not keys:
        return {}
    title_key, desc_key = keys
    out: dict[str, Any] = {}
    if seo_title:
        out[title_key] = seo_title
    if seo_description:
        out[desc_key] = seo_description
    return out


def _read_local_media(src: str) -> bytes | None:
    """Bytes for a locally-persisted draft image, or None.

    ``create_content`` stores generated images under the server's draft-image
    directory and refers to them as ``/media/drafts/<name>``. Only that directory is
    read, and only by basename, so a crafted ``src`` cannot walk out of it.
    """
    path = str(src or "").strip()
    if not path.startswith("/media/drafts/"):
        return None
    name = path.rsplit("/", 1)[-1]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    try:
        from app.integrations.llm import draft_images_dir

        candidate = draft_images_dir() / name
        if not candidate.is_file():
            return None
        return candidate.read_bytes()
    except Exception as exc:  # noqa: BLE001
        log.warning("draft_image_read_failed", src=path, error=str(exc))
        return None


async def upload_target_media(
    conn: WordPressConnection, target: PublishTarget
) -> dict[str, Any]:
    """Upload any local images and return a src rewrite map + featured media id.

    Images that cannot be uploaded are reported, not silently dropped — the content
    renderer already omits unusable sources, so the reviewer needs to know why a
    figure is missing.
    """
    rewrites: dict[str, str] = {}
    uploaded: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    featured_id: int | None = None

    for image in target.images:
        if not isinstance(image, dict):
            continue
        src = str(image.get("src") or "")
        data = image.get("data")
        if not data:
            # Generated images are persisted to local disk as "/media/drafts/x.png",
            # which is a path only this server can resolve. Read the bytes so the
            # file actually lands in the client's media library.
            data = _read_local_media(src)
        if not data:
            if src and not src.startswith(("http://", "https://")):
                failures.append({"src": src, "reason": "no_binary_to_upload"})
            continue
        filename = str(image.get("filename") or "") or (src.rsplit("/", 1)[-1] or "image.png")
        result = await wordpress.upload_media(
            conn,
            filename=filename,
            data=data if isinstance(data, bytes) else bytes(data),
            alt_text=str(image.get("alt") or ""),
            title=str(image.get("title") or image.get("alt") or ""),
            caption=str(image.get("caption") or ""),
        )
        if not result.get("ok"):
            failures.append({"src": src or filename, "reason": result.get("error")})
            continue
        uploaded.append(result)
        if src and result.get("source_url"):
            rewrites[src] = str(result["source_url"])
        if image.get("role") == "hero" and featured_id is None:
            featured_id = result.get("media_id")

    if featured_id is None and uploaded:
        featured_id = uploaded[0].get("media_id")

    return {
        "rewrites": rewrites,
        "uploaded": uploaded,
        "failures": failures,
        "featured_media": featured_id,
    }


def apply_src_rewrites(content_html: str, rewrites: dict[str, str]) -> str:
    out = content_html
    for old, new in rewrites.items():
        if old:
            out = out.replace(f'src="{old}"', f'src="{new}"')
    return out


async def publish_to_wordpress(
    conn: WordPressConnection | None,
    target: PublishTarget,
    *,
    capabilities: dict[str, Any] | None = None,
    upload_media: bool = True,
    require_written_copy: bool = True,
    verify_public: bool = True,
) -> dict[str, Any]:
    """Publish one target. Returns a full trace of every stage, including failures."""
    trace: dict[str, Any] = {
        "url": target.url,
        "slug": target.slug,
        "stage": "started",
        "strategy": None,
        "ok": False,
    }
    log.info("publish_started", slug=target.slug, url=target.url, status=target.status)

    if not wordpress.connection_configured(conn):
        trace.update(stage="connection", error="wordpress_not_connected")
        return trace
    assert conn is not None

    # --- 1. validate before anything leaves the process ---------------------
    trace["stage"] = "validation"
    validation = validate_publish_payload(
        {
            "title": target.title,
            "content": target.content_html,
            "slug": target.slug,
            "excerpt": target.excerpt,
        },
        require_written_copy=require_written_copy,
    )
    trace["validation"] = validation
    if not validation["ok"]:
        log.warning("publish_validation_failed", slug=target.slug, errors=validation["errors"])
        trace.update(stage="validation", error="content_validation_failed")
        return trace

    # --- 2. resolve strategy from what the target actually is ---------------
    trace["stage"] = "strategy"
    try:
        decision = await resolve_strategy(conn, target, capabilities=capabilities)
    except wordpress.WordPressError as exc:
        trace.update(stage="strategy", error=str(exc))
        return trace
    trace["strategy"] = decision.strategy
    trace["post_type"] = decision.post_type
    trace["strategy_reason"] = decision.reason
    trace["elementor"] = {k: v for k, v in decision.elementor.items() if k != "tree"}
    log.info(
        "publish_strategy_resolved",
        slug=target.slug,
        strategy=decision.strategy,
        post_type=decision.post_type,
    )

    if decision.strategy == STRATEGY_BLOCKED:
        trace["blockers"] = decision.blockers
        trace.update(stage="strategy", error="publishing_blocked")
        log.warning("publish_blocked", slug=target.slug, reason=decision.reason)
        return trace

    # --- 3. media ------------------------------------------------------------
    content_html = target.content_html
    featured_media = None
    if upload_media and target.images:
        trace["stage"] = "media"
        media = await upload_target_media(conn, target)
        content_html = apply_src_rewrites(content_html, media["rewrites"])
        featured_media = media["featured_media"]
        trace["media"] = {
            "uploaded": len(media["uploaded"]),
            "failed": media["failures"],
            "featured_media": featured_media,
        }

    # Whatever did not get a public URL must not ship as a broken image.
    content_html, dropped = strip_unresolved_images(content_html)
    if dropped:
        trace.setdefault("media", {})["figures_dropped_unresolved"] = dropped
        log.warning("publish_dropped_unresolved_images", slug=target.slug, count=dropped)

    # --- 4. build the write payload -----------------------------------------
    trace["stage"] = "write"
    caps = capabilities or {}
    meta_payload = build_seo_meta(
        caps.get("seo_plugin"),
        seo_title=target.seo_title or target.title,
        seo_description=target.seo_description or target.excerpt,
    )
    requested_seo_keys = sorted(meta_payload)

    if decision.strategy == STRATEGY_ELEMENTOR:
        tree = decision.elementor.get("tree") or []
        merged, merge_action = wp_elementor.merge_managed_content(
            tree, content_html, slot_key=target.slot_key()
        )
        structural = wp_elementor.validate_document(merged)
        if structural:
            trace.update(stage="write", error="invalid_elementor_structure")
            trace["elementor_issues"] = structural
            log.warning("publish_elementor_invalid", slug=target.slug, issues=structural)
            return trace
        meta_payload.update(
            wp_elementor.build_meta_payload(
                merged,
                template_type="wp-page" if decision.post_type == "pages" else "wp-post",
            )
        )
        trace["elementor"] = dict(trace.get("elementor") or {}) | {
            "merge_action": merge_action,
            "widgets_before": wp_elementor.count_widgets(tree),
            "widgets_after": wp_elementor.count_widgets(merged),
        }

    result = await wordpress.upsert_object(
        conn,
        title=target.title,
        # For an Elementor page this is the search/SEO mirror, not what renders.
        content_html=content_html,
        slug=target.slug,
        excerpt=target.excerpt,
        status=target.status,
        meta=meta_payload or None,
        post_type=decision.post_type,
        featured_media=featured_media,
        categories=target.categories,
        tags=target.tags,
    )
    trace["write"] = {k: v for k, v in result.items() if k != "stored_meta"}
    if not result.get("ok"):
        trace.update(stage="write", error=result.get("error"), detail=result.get("detail"))
        log.warning("publish_write_failed", slug=target.slug, error=result.get("error"))
        return trace

    # --- 5. verify -----------------------------------------------------------
    trace["stage"] = "verification"
    verification = await verify_publication(
        conn,
        result,
        target=target,
        strategy=decision.strategy,
        content_html=content_html,
        requested_seo_keys=requested_seo_keys,
        verify_public=verify_public,
    )
    trace["verification"] = verification
    trace["ok"] = bool(verification.get("verified"))
    trace["stage"] = "completed" if trace["ok"] else "verification"
    log.info(
        "publish_completed",
        slug=target.slug,
        verified=trace["ok"],
        post_id=result.get("post_id"),
    )
    return trace


def _rendered(block: Any) -> str:
    if isinstance(block, dict):
        return str(block.get("raw") or block.get("rendered") or "")
    return str(block or "")


def _text_signature(content_html: str, *, words: int = 8) -> str:
    """A short run of real words from the content, for spotting it on a live page."""
    import html as _html
    import re as _re

    text = _re.sub(r"<[^>]+>", " ", content_html or "")
    text = _html.unescape(text)
    tokens = [t for t in _re.findall(r"[A-Za-z0-9']+", text) if len(t) > 2]
    return " ".join(tokens[:words])


async def verify_publication(
    conn: WordPressConnection,
    result: dict[str, Any],
    *,
    target: PublishTarget,
    strategy: str,
    content_html: str,
    requested_seo_keys: list[str] | None = None,
    verify_public: bool = True,
) -> dict[str, Any]:
    """Confirm the content is actually there — not that a request returned 200.

    Checks the stored object, then (for a public status) the page a visitor gets.
    Every check reports its own outcome so a partial success is visible as one.
    """
    checks: dict[str, Any] = {}
    post_id = result.get("post_id")
    post_type = result.get("post_type") or "posts"

    stored = await wordpress.fetch_object(conn, int(post_id), post_type=post_type) if post_id else None
    if not stored:
        return {
            "verified": False,
            "reason": "could_not_reread_object",
            "checks": checks,
            "action_required": "Open the object in WP Admin and confirm manually.",
        }

    checks["object_exists"] = True
    checks["status_matches"] = str(stored.get("status") or "") == str(result.get("status") or "")
    checks["title_matches"] = (
        _rendered(stored.get("title")).strip() == str(target.title or "").strip()
    )
    stored_slug = str(stored.get("slug") or "")
    checks["slug_matches"] = stored_slug == target.slug
    if not checks["slug_matches"]:
        checks["actual_slug"] = stored_slug

    stored_meta = stored.get("meta") if isinstance(stored.get("meta"), dict) else {}

    if strategy == STRATEGY_ELEMENTOR:
        stored_tree = wp_elementor.parse_elementor_data(stored_meta.get(wp_elementor.DATA_KEY))
        checks["elementor_data_present"] = bool(stored_tree)
        managed = wp_elementor.extract_managed_html(stored_tree, slot_key=target.slot_key())
        # The content must be inside the Elementor tree, not merely in post_content.
        checks["elementor_content_stored"] = bool(
            managed and _text_signature(managed) == _text_signature(content_html)
        )
        checks["elementor_edit_mode"] = (
            str(stored_meta.get(wp_elementor.EDIT_MODE_KEY) or "") == wp_elementor.BUILDER_MODE
        )
    else:
        stored_content = _rendered(stored.get("content"))
        signature = _text_signature(content_html)
        checks["content_stored"] = bool(signature and signature in _text_signature(stored_content, words=400))

    if requested_seo_keys:
        # Report honestly which SEO keys the site actually accepted.
        accepted = [k for k in requested_seo_keys if stored_meta.get(k)]
        checks["seo_meta_written"] = sorted(accepted)
        checks["seo_meta_rejected"] = sorted(set(requested_seo_keys) - set(accepted))

    if target.featured_image is not None or result.get("featured_media"):
        checks["featured_media_set"] = bool(stored.get("featured_media"))

    public_url = str(stored.get("link") or result.get("link") or "")
    is_public = str(stored.get("status") or "") == "publish"
    if verify_public and public_url and is_public:
        fetched = await wordpress.fetch_public_url(public_url)
        checks["public_fetch_ok"] = bool(fetched.get("ok"))
        checks["public_status_code"] = fetched.get("status_code")
        checks["final_url"] = fetched.get("final_url")
        if fetched.get("ok"):
            signature = _text_signature(content_html, words=6)
            body = _text_signature(fetched.get("html") or "", words=100000)
            checks["public_content_visible"] = bool(signature and signature in body)
    elif verify_public and not is_public:
        checks["public_fetch_skipped"] = (
            f"status is '{stored.get('status')}' — a non-public object has no live page to check"
        )

    # Only checks that are actually booleans gate the verdict.
    hard_fail = [k for k, v in checks.items() if isinstance(v, bool) and not v]
    return {
        "verified": not hard_fail,
        "post_id": post_id,
        "post_type": post_type,
        "strategy": strategy,
        "stored_status": stored.get("status"),
        "link": public_url,
        "checks": checks,
        "failed_checks": hard_fail,
        "action_required": (
            None
            if not hard_fail
            else f"Verification failed on: {', '.join(hard_fail)} — review before go-live."
        ),
    }
