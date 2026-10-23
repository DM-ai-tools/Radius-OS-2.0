"""Running a site preview on Manus: build, follow, answer, collect, edit, stop, undo.

A build starts a Manus task with the brief, the draft and its images, then a
background loop follows the task: it feeds a progress bar and activity list,
answers the agent's questions, settles confirmations by a fixed policy, and when
the agent stops, collects ``index.html``, checks it, and stores it. An edit sends
the user's change request to the same task, so the agent keeps what it learned
about the site. Everything the UI shows is in the stored run state, so polling is
cheap and a server restart resumes the loop on the next poll.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.config import get_settings
from app.integrations import manus
from app.integrations.manus import ManusError
from app.integrations.web_fetch import assert_safe_url
from app.logging_config import get_logger
from app.services.api_meter import api_meter_context, record_api_call
from app.services.site_preview import store
from app.services.site_preview.answer import answer_question
from app.services.site_preview.brief import (
    HTML_FILENAME,
    NO_DEPLOY,
    NUDGE_MESSAGE,
    RESULT_SCHEMA,
    PreviewBrief,
    answer_context,
    build_brief,
    build_edit_message,
    build_task_message,
    repeat_fix_message,
)
from app.services.site_preview.checks import (
    draft_problems,
    review_notes,
    strip_missing_figures,
)
from app.services.site_preview.design_page import DesignPage, choose_design_page
from app.services.site_preview.events import (
    QUESTION_TYPES,
    activity_items,
    confirmation_for,
    current_plan,
    guess_stage,
    progress_percent,
    stage_label,
)
from app.services.site_preview.images import (
    MAX_IMAGE_BYTES,
    find_repeated_images,
    sniff_image_type,
)

log = get_logger("site_preview.run")

ACTIVE = {"starting", "running", "waiting", "collecting"}
MAX_WAITS = 20
MAX_NUDGES = 2
MAX_IMAGE_FIXES = 1
MAX_FEED = 40
MAX_POLL_FAILURES = 6
MAX_IMAGES = 12
MAX_SCREENSHOTS = 4
REPORT_WAIT_SECONDS = 60
FILE_WAIT_SECONDS = 180
STALE_STOP_SECONDS = 60
BACKGROUND_GRACE_SECONDS = 600
CONTINUE_MESSAGE = (
    "Continue and use your best judgment, following brief.md. " + NO_DEPLOY + f" Deliver {HTML_FILENAME} when done."
)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/avif": "avif"}
_MD_IMG_RE = re.compile(r"""!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)""")
_FATAL_CODES = {"not_found", "permission_denied", "not_configured", "unauthenticated", "invalid_argument"}

_loops: dict[str, asyncio.Task[None]] = {}
_project_cache: dict[str, str] = {}
_connector_cache: dict[str, list[str]] = {}


class PreviewError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PreviewContext:
    """What the run needs to know about the client (read once by the API)."""

    client_id: UUID
    client_name: str
    primary_url: str
    site_architecture: dict[str, Any] = field(default_factory=dict)
    on_page_seo: dict[str, Any] = field(default_factory=dict)
    content_planning: dict[str, Any] = field(default_factory=dict)
    topic_plan: dict[str, Any] = field(default_factory=dict)
    # The live-site crawl (Website Situation sitemap): url, page_type, status per page.
    live_pages: list[dict[str, Any]] = field(default_factory=list)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _loop_key(client_id: str, key: str) -> str:
    return f"{client_id}:{key}"


# ------------------------------------------------------------------ the brief


def _absolute(url: str, primary_url: str) -> str:
    url = str(url or "").strip()
    if not url or url.startswith(("http://", "https://")):
        return url
    return primary_url.rstrip("/") + (url if url.startswith("/") else f"/{url}")


def _local_draft_image(src: str) -> tuple[bytes, str] | None:
    """Bytes and type of a generated draft image (``/media/drafts/<name>``). Only that
    directory is read, by basename, so a crafted address cannot walk out of it."""
    from app.integrations.llm import draft_images_dir

    path = urlparse(src).path if "://" in src else src
    if "/media/drafts/" not in path:
        return None
    name = path.rsplit("/", 1)[-1]
    if not name or name.startswith(".") or "\\" in name:
        return None
    file = draft_images_dir() / name
    try:
        if not file.is_file() or file.stat().st_size > MAX_IMAGE_BYTES:
            return None
        raw = file.read_bytes()
    except OSError:
        return None
    mime = sniff_image_type(raw)
    return (raw, mime) if mime else None


def _file_part(filename: str, mime: str, raw: bytes) -> dict[str, Any]:
    return {
        "type": "file",
        "filename": filename,
        "mime_type": mime,
        "file_data": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}",
    }


def _text_file(filename: str, text: str) -> dict[str, Any]:
    return _file_part(filename, "text/markdown", text.encode("utf-8"))


def screenshot_parts(screenshots: list[str] | None) -> tuple[list[dict[str, Any]], list[str]]:
    """Validated user screenshots (``data:image/...;base64,...``) as file parts."""
    parts: list[dict[str, Any]] = []
    names: list[str] = []
    for i, data_uri in enumerate((screenshots or [])[:MAX_SCREENSHOTS], start=1):
        match = re.match(r"^data:image/[a-z+.-]+;base64,([A-Za-z0-9+/=\s]+)$", str(data_uri or ""))
        if not match:
            raise PreviewError("bad_screenshot", f"Screenshot {i} is not an image.")
        try:
            raw = base64.b64decode(match.group(1), validate=False)
        except ValueError as exc:
            raise PreviewError("bad_screenshot", f"Screenshot {i} could not be read.") from exc
        mime = sniff_image_type(raw)
        if not mime or len(raw) > MAX_IMAGE_BYTES:
            raise PreviewError("bad_screenshot", f"Screenshot {i} must be a PNG, JPEG, WebP or GIF under 8 MB.")
        name = f"screenshot-{i}.{_EXT[mime]}"
        parts.append(_file_part(name, mime, raw))
        names.append(name)
    return parts, names


# Parent-type results of resolve_previous_design_page. Its other steps (the previous
# page in the publishing order or roadmap) can be a planned page or a different kind
# of page, so they are not used for the preview.
_PARENT_REASONS = {"internal_linking_parent", "on_page_parent", "ia_tree_parent", "ia_parent_pillar"}


async def pick_design_page(ctx: PreviewContext, draft: dict[str, Any]) -> DesignPage:
    """The live page the preview copies: see ``design_page`` for the order."""
    from app.services.design_capture import resolve_previous_design_page

    previous = resolve_previous_design_page(
        page_url=_absolute(str(draft.get("url") or ""), ctx.primary_url),
        page_keyword=str(draft.get("keyword") or ""),
        primary_url=ctx.primary_url,
        on_page_seo=ctx.on_page_seo,
        content_planning=ctx.content_planning,
        topic_plan=ctx.topic_plan,
        site_architecture=ctx.site_architecture,
    )
    parent = previous if previous.get("reason") in _PARENT_REASONS else None
    return await choose_design_page(draft, primary_url=ctx.primary_url, live_pages=ctx.live_pages, parent=parent)


def prepare_brief(
    ctx: PreviewContext,
    draft: dict[str, Any],
    *,
    design: DesignPage | None = None,
    instructions: str = "",
    screenshot_names: list[str] | None = None,
) -> tuple[PreviewBrief, list[dict[str, Any]]]:
    """The brief, and the draft's own images as file parts. Local draft images are
    attached and referenced in ``draft.md`` by file name; remote ones keep their address."""
    from app.services.publish_preview import (
        draft_body_markdown,
        sanitize_meta_description,
    )

    title = str(draft.get("title") or draft.get("keyword") or "New page").strip()
    page_url = _absolute(str(draft.get("url") or ""), ctx.primary_url)
    body = strip_missing_figures(draft_body_markdown(str(draft.get("markdown") or "")))
    if not re.search(r"(?m)^#\s+\S", body):
        body = f"# {title}\n\n{body}"

    captions = {
        str(img.get("src")): str(img.get("caption") or "")
        for img in (draft.get("images") or [])
        if isinstance(img, dict) and img.get("src")
    }
    parts: list[dict[str, Any]] = []
    image_files: list[dict[str, str]] = []
    remote: list[dict[str, str]] = []
    attached: dict[str, str] = {}

    def swap(match: re.Match[str]) -> str:
        alt, src = match.group(1), match.group(2)
        if src in attached:
            return f"![{alt}]({attached[src]})"
        local = _local_draft_image(src) if len(image_files) < MAX_IMAGES else None
        if local:
            raw, mime = local
            name = f"image-{len(image_files) + 1}.{_EXT[mime]}"
            attached[src] = name
            parts.append(_file_part(name, mime, raw))
            image_files.append({"filename": name, "alt": alt, "caption": captions.get(src, "")})
            return f"![{alt}]({name})"
        if src.startswith(("http://", "https://")) and all(r["src"] != src for r in remote):
            remote.append({"src": src, "alt": alt})
        return match.group(0)

    body = _MD_IMG_RE.sub(swap, body)

    design = design or DesignPage(ctx.primary_url, "the home page", "home")

    brief = PreviewBrief(
        client_name=ctx.client_name,
        client_website=ctx.primary_url,
        design_page_url=design.url,
        design_page_reason=design.reason,
        page_title=title,
        page_url=page_url,
        meta_description=sanitize_meta_description(draft.get("meta_description")),
        keyword=str(draft.get("keyword") or ""),
        draft_markdown=body,
        image_files=image_files,
        remote_images=remote,
        instructions=str(instructions or "").strip(),
        screenshot_names=list(screenshot_names or []),
    )
    return brief, parts


async def _project_id() -> str | None:
    settings = get_settings()
    if settings.manus_project_id:
        return settings.manus_project_id
    name = settings.manus_project_name.strip().lower()
    if not name:
        return None
    if name not in _project_cache:
        try:
            projects = await manus.list_projects()
        except ManusError as exc:
            log.warning("manus_project_lookup_failed", error=str(exc)[:200])
            return None
        for project in projects:
            if str(project.get("name") or "").strip().lower() == name and project.get("id"):
                _project_cache[name] = str(project["id"])
                break
        else:
            log.warning("manus_project_not_found", project=name)
            return None
    return _project_cache.get(name)


async def _connector_ids() -> list[str] | None:
    names = [n.strip().lower() for n in get_settings().manus_connectors.split(",") if n.strip()]
    if not names:
        return None
    cache_key = ",".join(names)
    if cache_key not in _connector_cache:
        try:
            installed = await manus.list_connectors()
        except ManusError as exc:
            log.warning("manus_connector_lookup_failed", error=str(exc)[:200])
            return None
        found = [str(c["id"]) for c in installed if c.get("id") and str(c.get("name") or "").strip().lower() in names]
        if len(found) < len(names):
            log.warning("manus_connector_missing", wanted=names, found=len(found))
        _connector_cache[cache_key] = found
    return _connector_cache[cache_key] or None


# ------------------------------------------------------------------- state


def public_state(state: dict[str, Any] | None, *, has_html: bool = False) -> dict[str, Any]:
    """What the UI may see: no task ids, event ids or the brief."""
    if not state:
        return {"status": "none", "hasHtml": has_html}
    return {
        "status": state.get("status"),
        "mode": state.get("mode"),
        "stage": state.get("stage"),
        "stageLabel": stage_label(str(state.get("stage") or "brief")),
        "progress": int(state.get("progress") or 0),
        "startedAt": state.get("started_at"),
        "updatedAt": state.get("updated_at"),
        "activity": list(state.get("activity") or [])[-MAX_FEED:],
        "questions": list(state.get("questions") or []),
        "reviewNotes": list(state.get("review_notes") or []),
        "report": state.get("report_summary") or None,
        "designPageUrl": state.get("design_page_url"),
        "designPageReason": state.get("design_page_reason"),
        "credits": state.get("credits"),
        "error": state.get("error"),
        "canUndo": bool(state.get("can_undo")),
        "hasHtml": has_html,
    }


async def _save(client_id: str, key: str, state: dict[str, Any]) -> None:
    state["updated_at"] = time.time()
    await store.save_state(client_id, key, state)


def _feed(state: dict[str, Any], kind: str, text: str) -> None:
    items = list(state.get("activity") or [])
    items.append({"id": f"sp-{len(items)}-{_now_ms()}", "at": _now_ms(), "kind": kind, "text": text[:240]})
    state["activity"] = items[-MAX_FEED:]


def _new_turn(state: dict[str, Any], message: str) -> None:
    """A new instruction message starts a turn: only events after it count."""
    state["turn_marker"] = " ".join(message.split())[:80]
    state["turn_started_ms"] = _now_ms() - 5000
    state["stopped_seen_at"] = None


async def _record_credits(client_id: str, state: dict[str, Any], outcome: str) -> None:
    """One meter row per run with the Manus credits it used. Dollars only when
    MANUS_USD_PER_CREDIT is set; otherwise the cost is recorded as unknown."""
    credits = state.get("credits")
    if state.get("credits_recorded") or credits is None:
        return
    state["credits_recorded"] = True
    rate = float(get_settings().manus_usd_per_credit or 0)
    try:
        cid: UUID | None = UUID(client_id)
    except ValueError:
        cid = None
    await record_api_call(
        provider="manus",
        operation=f"site_preview.{state.get('mode') or 'build'}",
        estimated_cost_usd=round(credits * rate, 4) if rate > 0 else None,
        status="success" if outcome == "done" else outcome,
        request_meta={"credits": credits, "task_id": state.get("task_id"), "priced": rate > 0},
        client_id=cid,
        agent_key="content_production",
    )


async def _fail(client_id: str, key: str, state: dict[str, Any], message: str) -> None:
    state["status"] = "failed"
    state["error"] = message[:400]
    _feed(state, "error", message)
    await _record_credits(client_id, state, "error")
    await _save(client_id, key, state)


# ------------------------------------------------------------------ starting


async def _halt(client_id: str, key: str) -> None:
    """Cancel this page's loop and stop its Manus task, if one is running."""
    loop = _loops.pop(_loop_key(client_id, key), None)
    if loop and not loop.done():
        loop.cancel()
    state = await store.load_state(client_id, key)
    if state and state.get("status") in ACTIVE and state.get("task_id"):
        try:
            await manus.stop_task(str(state["task_id"]))
        except ManusError as exc:
            log.warning("manus_stop_failed", error=str(exc)[:200])
        state["status"] = "stopped"
        try:
            detail = await manus.task_detail(str(state["task_id"]))
            if detail.get("credit_usage") is not None:
                state["credits"] = max(0, int(detail["credit_usage"]) - int(state.get("credits_base") or 0))
        except ManusError:
            pass
        await _record_credits(client_id, state, "stopped")
        await _save(client_id, key, state)


def _spawn(client_uuid: UUID, key: str) -> None:
    name = _loop_key(str(client_uuid), key)
    current = _loops.get(name)
    if current and not current.done():
        return
    _loops[name] = asyncio.create_task(_follow(client_uuid, key), name=f"site-preview-{name}")


async def start_build(
    ctx: PreviewContext, draft: dict[str, Any], *, instructions: str = "", screenshots: list[str] | None = None
) -> dict[str, Any]:
    """Start a new Manus build for this draft (stopping any build already running)."""
    if not manus.configured():
        raise PreviewError("not_configured", "Manus is not set up. Add MANUS_API_KEY to the server's .env file.")
    if not str(draft.get("markdown") or "").strip():
        raise PreviewError("no_draft", "The draft has no text yet.")
    from app.services.publish_preview import draft_body_markdown

    problems = draft_problems(draft_body_markdown(str(draft.get("markdown") or "")))
    if problems:
        # The preview copies the draft word for word, so it would show all of this.
        raise PreviewError(
            "draft_not_ready",
            "This draft isn't ready to preview: " + "; ".join(problems) + ". "
            "The preview copies the draft word for word, so these would appear on the page.",
        )
    client_id = str(ctx.client_id)
    key = store.page_key(draft)
    shots, shot_names = screenshot_parts(screenshots)
    design = await pick_design_page(ctx, draft)
    brief, image_parts = prepare_brief(
        ctx, draft, design=design, instructions=instructions, screenshot_names=shot_names
    )
    await _halt(client_id, key)
    previous = await store.load_state(client_id, key) or {}

    message = build_task_message(brief)
    content = [
        {"type": "text", "text": message},
        _text_file("brief.md", build_brief(brief)),
        _text_file("draft.md", brief.draft_markdown),
        *image_parts,
        *shots,
    ]
    state: dict[str, Any] = {
        "status": "starting",
        "mode": "build",
        "stage": "brief",
        "progress": 1,
        "started_at": time.time(),
        "activity": [],
        "questions": [],
        "answers_used": 0,
        "handled_event_ids": [],
        "nudges": 0,
        "image_fixes": 0,
        "review_notes": [],
        "credits": None,
        "credits_base": 0,
        "error": None,
        "brief": asdict(brief),
        "design_page_url": brief.design_page_url,
        "design_page_reason": brief.design_page_reason,
        "can_undo": bool(previous.get("can_undo")),
    }
    _feed(state, "note", f"Sending the brief. Design from {brief.design_page_url}: {brief.design_page_reason}.")
    _new_turn(state, message)
    await _save(client_id, key, state)
    try:
        created = await manus.create_task(
            content=content,
            title=f"Site preview: {brief.page_title}"[:120],
            project_id=await _project_id(),
            connectors=await _connector_ids(),
            agent_profile=get_settings().manus_agent_profile or None,
            structured_output_schema=RESULT_SCHEMA,
        )
    except ManusError as exc:
        await _fail(client_id, key, state, f"Manus did not accept the build: {exc}")
        raise PreviewError("manus_error", f"Manus did not accept the build: {exc}") from exc
    state.update(task_id=created.get("task_id"), task_url=created.get("task_url"), status="running", progress=3)
    await _save(client_id, key, state)
    _spawn(ctx.client_id, key)
    return public_state(state, has_html=bool(await store.load_html(client_id, key)))


async def start_edit(
    ctx: PreviewContext, draft: dict[str, Any], *, instructions: str, screenshots: list[str] | None = None
) -> dict[str, Any]:
    """Send a change request to the task that built the page. Falls back to a fresh
    build (with the request as instructions) when that task can no longer continue."""
    if not str(instructions or "").strip():
        raise PreviewError("no_instructions", "Describe the changes you want.")
    client_id = str(ctx.client_id)
    key = store.page_key(draft)
    state = await store.load_state(client_id, key)
    if state and state.get("status") in ACTIVE:
        raise PreviewError("busy", "A build is still running. Wait for it or stop it first.")
    if not state or not state.get("task_id") or not await store.load_html(client_id, key):
        return await start_build(ctx, draft, instructions=instructions, screenshots=screenshots)
    if not manus.configured():
        raise PreviewError("not_configured", "Manus is not set up. Add MANUS_API_KEY to the server's .env file.")
    shots, shot_names = screenshot_parts(screenshots)
    message = build_edit_message(instructions, shot_names)
    try:
        detail = await manus.task_detail(str(state["task_id"]))
        await manus.send_message(
            str(state["task_id"]),
            [{"type": "text", "text": message}, *shots],
            structured_output_schema=RESULT_SCHEMA,
        )
    except ManusError as exc:
        log.info("preview_edit_falls_back_to_build", error=str(exc)[:200])
        return await start_build(ctx, draft, instructions=instructions, screenshots=screenshots)
    state.update(
        status="running",
        mode="edit",
        stage="study",
        progress=6,
        started_at=time.time(),
        handled_event_ids=[],
        nudges=0,
        image_fixes=0,
        error=None,
        credits=None,
        credits_base=int(detail.get("credit_usage") or 0),
        credits_recorded=False,
        undo_saved=False,
    )
    _feed(state, "note", "Change request sent: " + " ".join(instructions.split())[:180])
    _new_turn(state, message)
    await _save(client_id, key, state)
    _spawn(ctx.client_id, key)
    return public_state(state, has_html=True)


# ----------------------------------------------------------------- following


async def _pause() -> None:
    await asyncio.sleep(max(1.0, float(get_settings().manus_poll_seconds or 10)))


async def _follow(client_uuid: UUID, key: str) -> None:
    client_id = str(client_uuid)
    failures = 0
    async with api_meter_context(client_id=client_uuid, agent_key="content_production"):
        while True:
            state = await store.load_state(client_id, key)
            if not state or state.get("status") not in ACTIVE:
                return
            try:
                finished = await _step(client_id, key, state)
                failures = 0
            except asyncio.CancelledError:
                raise
            except PreviewError as exc:
                await _fail(client_id, key, state, exc.message)
                return
            except ManusError as exc:
                failures += 1
                log.warning("preview_poll_failed", error=str(exc)[:200], attempt=failures)
                if exc.code in _FATAL_CODES or failures >= MAX_POLL_FAILURES:
                    await _fail(client_id, key, state, f"Lost contact with the Manus task: {exc}")
                    return
                finished = False
            except Exception as exc:
                # The run must end visibly, never hang.
                log.exception("preview_run_crashed")
                await _fail(client_id, key, state, f"The preview run failed: {type(exc).__name__}")
                return
            if finished:
                return
            await _pause()


def _turn_events(state: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Events since our latest instruction message (found by its opening words)."""
    marker = str(state.get("turn_marker") or "")
    start = int(state.get("turn_started_ms") or 0)
    for event in sorted(events, key=lambda e: e.get("timestamp") or 0, reverse=True):
        if event.get("type") != "user_message":
            continue
        text = " ".join(str((event.get("user_message") or {}).get("content") or "").split())
        if marker and text.startswith(marker):
            start = int(event.get("timestamp") or start)
            state["turn_started_ms"] = start
            break
    return [e for e in events if int(e.get("timestamp") or 0) >= start]


def _newest(events: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
    found = [e for e in events if e.get("type") == kind]
    return max(found, key=lambda e: e.get("timestamp") or 0) if found else None


def _update_progress(state: dict[str, Any], turn: list[dict[str, Any]]) -> None:
    seen = {item.get("id") for item in state.get("activity") or []}
    for item in activity_items(turn):
        if item["id"] not in seen:
            state.setdefault("activity", []).append(item)
    state["activity"] = list(state.get("activity") or [])[-MAX_FEED:]
    plan = current_plan(turn)
    texts = [str(item.get("text") or "") for item in state["activity"]]
    if state.get("status") == "collecting":
        state["stage"] = "collect"
    else:
        state["stage"] = guess_stage(texts, plan, str(state.get("stage") or "brief"))
    state["progress"] = progress_percent(
        state["stage"], plan, float(state.get("started_at") or time.time()), int(state.get("progress") or 0)
    )


def _brief_of(state: dict[str, Any]) -> PreviewBrief:
    return PreviewBrief(**(state.get("brief") or {}))


async def _step(client_id: str, key: str, state: dict[str, Any]) -> bool:
    """One poll. True when the run has ended."""
    task_id = str(state["task_id"])
    timeout = int(get_settings().manus_timeout_minutes or 90) * 60
    if time.time() - float(state.get("started_at") or time.time()) > timeout:
        try:
            await manus.stop_task(task_id)
        except ManusError:
            pass
        await _fail(client_id, key, state, f"The build took longer than {timeout // 60} minutes and was stopped.")
        return True

    detail = await manus.task_detail(task_id)
    events = list((await manus.list_messages(task_id, limit=100, order="desc")).get("messages") or [])
    turn = _turn_events(state, events)
    if detail.get("credit_usage") is not None:
        state["credits"] = max(0, int(detail["credit_usage"]) - int(state.get("credits_base") or 0))
    status = str(detail.get("status") or "")

    if status == "waiting":
        state["status"] = "waiting"
        _update_progress(state, turn)
        await _settle_wait(state, turn, events)
    elif status == "error":
        err = _newest(turn, "error_message") or _newest(events, "error_message")
        text = str(((err or {}).get("error_message") or {}).get("content") or "no details given")
        await _fail(client_id, key, state, f"Manus reported an error: {text[:300]}")
        return True
    elif status == "stopped":
        stopped_in_turn = any(
            e.get("type") == "status_update" and (e.get("status_update") or {}).get("agent_status") == "stopped"
            for e in turn
        )
        turn_age = time.time() - int(state.get("turn_started_ms") or 0) / 1000
        if not stopped_in_turn and turn_age < STALE_STOP_SECONDS:
            pass  # the stop belongs to the previous turn; our message is still being picked up
        elif detail.get("has_running_background_jobs") and _stopped_for(state) < BACKGROUND_GRACE_SECONDS:
            state["status"] = "running"
        else:
            state["status"] = "collecting"
            _update_progress(state, turn)
            return await _collect(client_id, key, state, turn)
    else:
        state["status"] = "running"
    _update_progress(state, turn)
    await _save(client_id, key, state)
    return False


def _stopped_for(state: dict[str, Any]) -> float:
    if not state.get("stopped_seen_at"):
        state["stopped_seen_at"] = time.time()
    return time.time() - float(state["stopped_seen_at"])


async def _settle_wait(state: dict[str, Any], turn: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    task_id = str(state["task_id"])
    waits = [
        e
        for e in (turn or events)
        if e.get("type") == "status_update" and (e.get("status_update") or {}).get("agent_status") == "waiting"
    ] or [
        e
        for e in events
        if e.get("type") == "status_update" and (e.get("status_update") or {}).get("agent_status") == "waiting"
    ]
    latest = max(waits, key=lambda e: e.get("timestamp") or 0) if waits else {}
    detail = (latest.get("status_update") or {}).get("status_detail") or {}
    kind = str(detail.get("waiting_for_event_type") or "")
    question = _newest(turn, "assistant_message") if kind in QUESTION_TYPES else None
    handle_id = str(
        (question or {}).get("id") or detail.get("waiting_for_event_id") or latest.get("id") or ""
    )
    handled = list(state.get("handled_event_ids") or [])
    if not handle_id or handle_id in handled:
        return  # already answered; the agent has not picked it up yet
    if len(handled) >= MAX_WAITS:
        raise PreviewError("too_many_waits", "The agent kept stopping for input, so the build was ended.")
    state["handled_event_ids"] = [*handled, handle_id]

    if kind in QUESTION_TYPES:
        msg = (question or {}).get("assistant_message") or {}
        text = str(msg.get("content") or detail.get("waiting_description") or "").strip()
        options = list((msg.get("question_expectation") or {}).get("options") or []) or None
        answer, from_model = await answer_question(
            text,
            options=options,
            history=list(state.get("questions") or []),
            context=answer_context(_brief_of(state)),
            answers_used=int(state.get("answers_used") or 0),
        )
        if from_model:
            state["answers_used"] = int(state.get("answers_used") or 0) + 1
        await manus.send_message(task_id, answer)
        state.setdefault("questions", []).append({"question": text[:600], "answer": answer[:1200], "at": _now_ms()})
        _feed(state, "answer", f"Answered: {text[:110]}")
        return
    if not detail.get("waiting_for_event_id"):
        await manus.send_message(task_id, CONTINUE_MESSAGE)
        _feed(state, "decision", "Told the agent to continue on its own judgement.")
        return
    decision = confirmation_for(detail)
    if decision.kind == "confirm":
        result = await manus.confirm_action(task_id, str(detail["waiting_for_event_id"]), decision.input or {})
        if result.get("confirmed") is False:
            await manus.send_message(task_id, CONTINUE_MESSAGE)
        accepted = bool((decision.input or {}).get("accept") or (decision.input or {}).get("action") == "accept")
        _feed(state, "decision", f"{'Allowed' if accepted else 'Declined'}: {kind}" + (f" ({decision.reason})" if decision.reason else ""))
    else:
        await manus.send_message(task_id, decision.message)
        _feed(state, "decision", f"Skipped: {kind or 'an action'}. {decision.reason}")


# ---------------------------------------------------------------- collecting


def _html_attachment(turn: list[dict[str, Any]], preferred: str) -> dict[str, Any] | None:
    for event in sorted(turn, key=lambda e: e.get("timestamp") or 0, reverse=True):
        if event.get("type") != "assistant_message":
            continue
        files = [
            a
            for a in (event.get("assistant_message") or {}).get("attachments") or []
            if str(a.get("filename") or "").lower().endswith((".html", ".htm")) and a.get("url")
        ]
        if not files:
            continue
        for name in (preferred, HTML_FILENAME):
            for att in files:
                if name and str(att.get("filename") or "").lower() == name.lower():
                    return att
        return files[0]
    return None


def _report(turn: list[dict[str, Any]]) -> dict[str, Any] | None:
    event = _newest(turn, "structured_output_result")
    if not event:
        return None
    result = event.get("structured_output_result") or {}
    value = result.get("value") if isinstance(result.get("value"), dict) else {}
    if not result.get("success"):
        value = {**value, "unresolved": [*list(value.get("unresolved") or []), str(result.get("error") or "no report")]}
    return value


async def _send_turn(state: dict[str, Any], message: str) -> None:
    await manus.send_message(
        str(state["task_id"]), [{"type": "text", "text": message}], structured_output_schema=RESULT_SCHEMA
    )
    _new_turn(state, message)
    state["status"] = "running"


async def _collect(client_id: str, key: str, state: dict[str, Any], turn: list[dict[str, Any]]) -> bool:
    waited = _stopped_for(state)
    report = _report(turn)
    attachment = _html_attachment(turn, str((report or {}).get("html_filename") or ""))
    if attachment is None and waited < FILE_WAIT_SECONDS:
        await _save(client_id, key, state)
        return False
    if attachment is not None and report is None and waited < REPORT_WAIT_SECONDS:
        await _save(client_id, key, state)
        return False
    if attachment is None:
        if int(state.get("nudges") or 0) >= MAX_NUDGES:
            await _fail(client_id, key, state, "The agent finished without attaching the page.")
            return True
        state["nudges"] = int(state.get("nudges") or 0) + 1
        await _send_turn(state, NUDGE_MESSAGE)
        _feed(state, "decision", "Asked the agent to attach the finished page.")
        await _save(client_id, key, state)
        return False

    ok, safe_url = assert_safe_url(str(attachment["url"]))
    if not ok:
        await _fail(client_id, key, state, "The page's download link was not a public address.")
        return True
    raw = await manus.download(safe_url)
    page = raw.decode("utf-8", errors="replace")
    if not re.search(r"(?i)<(html|body|h1)\b", page[:200_000]):
        await _fail(client_id, key, state, "The attached file is not an HTML page.")
        return True
    await _store_page(client_id, key, state, page)

    repeats = find_repeated_images(page)
    if repeats and int(state.get("image_fixes") or 0) < MAX_IMAGE_FIXES:
        state["image_fixes"] = int(state.get("image_fixes") or 0) + 1
        await _send_turn(state, repeat_fix_message(repeats))
        _feed(state, "decision", f"Asked the agent to replace {len(repeats)} repeated photo(s).")
        await _save(client_id, key, state)
        return False

    brief = _brief_of(state)
    state["review_notes"] = review_notes(
        page,
        draft_markdown=brief.draft_markdown,
        title=brief.page_title,
        report=report,
        client_name=brief.client_name,
    )
    rep = report or {}
    state["report_summary"] = {
        "summary": str(rep.get("summary") or "")[:600],
        "fonts": list((rep.get("design") or {}).get("fonts") or [])[:6],
        "colours": list((rep.get("design") or {}).get("colours") or [])[:10],
        "referenceUrl": (rep.get("design") or {}).get("reference_url") or brief.design_page_url,
    }
    state.update(status="done", stage="collect", progress=100, error=None)
    _feed(state, "note", "Page collected and checked.")
    await _record_credits(client_id, state, "done")
    await _save(client_id, key, state)
    return True


async def _store_page(client_id: str, key: str, state: dict[str, Any], page: str) -> None:
    """Save the page. The first save of a run keeps the page it replaces for Undo."""
    if not state.get("undo_saved"):
        old = await store.load_html(client_id, key)
        await store.save_html(client_id, key, old, previous=True)
        state["undo_saved"] = True
        state["can_undo"] = bool(old)
    await store.save_html(client_id, key, page)


# ------------------------------------------------------------ user controls


async def stop(client_id: str, key: str) -> dict[str, Any]:
    await _halt(client_id, key)
    state = await store.load_state(client_id, key)
    if state and state.get("status") == "stopped":
        _feed(state, "note", "Stopped by the user.")
        await _save(client_id, key, state)
    return public_state(state, has_html=bool(await store.load_html(client_id, key)))


async def undo(client_id: str, key: str) -> dict[str, Any]:
    state = await store.load_state(client_id, key)
    if state and state.get("status") in ACTIVE:
        raise PreviewError("busy", "Wait for the build to finish or stop it before undoing.")
    previous = await store.load_html(client_id, key, previous=True)
    if not state or not previous:
        raise PreviewError("nothing_to_undo", "There is no earlier version to go back to.")
    await store.save_html(client_id, key, previous)
    await store.save_html(client_id, key, "", previous=True)
    state.update(can_undo=False, review_notes=[], report_summary=None)
    _feed(state, "note", "Went back to the previous version.")
    await _save(client_id, key, state)
    return public_state(state, has_html=True)


async def get_state(client_uuid: UUID, key: str) -> dict[str, Any]:
    """The run as the UI sees it. Restarts the follow loop if the server restarted mid-run."""
    client_id = str(client_uuid)
    state = await store.load_state(client_id, key)
    if state and state.get("status") in ACTIVE and state.get("task_id") and manus.configured():
        _spawn(client_uuid, key)
    return public_state(state, has_html=bool(await store.load_html(client_id, key)))


async def get_html(client_id: str, key: str) -> str:
    return await store.load_html(client_id, key)
