"""Client-site preview on Manus: the run against a scripted fake Manus task.

Nothing here calls Manus, Redis, the database or a model. The fake records every
request, so the tests check what the pipeline sends as well as what it stores.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from app.services.site_preview import brief as brief_mod
from app.services.site_preview import checks, design_page, events, images, run, store

REAL_LATEST_BLOG_POSTS = design_page.latest_blog_posts

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

DRAFT = {
    "title": "Emergency Plumbing in Brunswick",
    "url": "/services/emergency-plumbing",
    "keyword": "emergency plumber brunswick",
    "meta_description": "24/7 emergency plumbers in Brunswick.",
    "markdown": (
        "# Emergency Plumbing in Brunswick\n\n"
        "Burst pipes do not wait for business hours, and neither do we.\n\n"
        "![A plumber fixing a burst pipe](/media/drafts/hero.png)\n\n"
        "## What we fix after hours\n\n"
        "We repair burst pipes, blocked drains and failed hot water systems any time of night.\n\n"
        "Read about our [blocked drain service](/services/blocked-drains) for more detail.\n"
    ),
    "images": [{"src": "/media/drafts/hero.png", "alt": "A plumber fixing a burst pipe", "caption": "On site in Brunswick"}],
}


HEAD = """
<meta name="description" content="24/7 emergency plumbers in Brunswick.">
<link rel="canonical" href="https://acme.example/services/emergency-plumbing">
<meta property="og:title" content="Emergency Plumbing in Brunswick">
<meta property="og:description" content="24/7 emergency plumbers in Brunswick.">
<meta property="og:url" content="https://acme.example/services/emergency-plumbing">
<meta property="og:image" content="https://acme.example/share.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="https://acme.example/favicon.ico">
<script type="application/ld+json">{"@type": "Service"}</script>"""


def good_page(*, photo_twice: bool = False) -> str:
    photo = '<img src="https://cdn.example.com/photos/van.jpg" alt="Our van">'
    return f"""<!doctype html><html><head><title>Emergency Plumbing in Brunswick</title>{HEAD}</head><body>
<header><img src="https://acme.example/logo.svg" alt="Acme logo"><nav><a href="/">Home</a></nav></header>
<main>
<h1>Emergency Plumbing in Brunswick</h1>
<p>Burst pipes do not wait for business hours, and neither do we.</p>
<figure><img src="data:image/png;base64,AAAA" alt="A plumber fixing a burst pipe"></figure>
<h2>What we fix after hours</h2>
<p>We repair burst pipes, blocked drains and failed hot water systems any time of night.</p>
<p>Read about our <a href="/services/blocked-drains">blocked drain service</a> for more detail.</p>
{photo}{photo if photo_twice else ""}
</main><footer>Acme Plumbing</footer></body></html>"""


REPORT = {
    "html_filename": "index.html",
    "summary": "Built in the site's service-page layout.",
    "design": {
        "reference_url": "https://acme.example/services/blocked-drains",
        "header_from_site": True,
        "footer_from_site": True,
        "fonts": ["Inter"],
        "colours": ["#0a2540"],
    },
    "content_check": {
        "all_headings_kept": True,
        "all_links_kept": True,
        "all_images_placed": True,
        "copy_changed": False,
        "notes": "",
    },
    "verification": {"desktop_compared": True, "mobile_compared": True, "notes": ""},
    "unresolved": [],
}


class FakeManus:
    """A Manus task driven by a script: one step runs on each ``task.detail`` poll."""

    def __init__(self) -> None:
        self.clock = int(time.time() * 1000)
        self.events: list[dict[str, Any]] = []
        self.status = "running"
        self.credit = 0
        self.created: dict[str, Any] | None = None
        self.sent: list[Any] = []
        self.sent_schemas: list[Any] = []
        self.confirms: list[tuple[str, dict[str, Any]]] = []
        self.stopped: list[str] = []
        self.files: dict[str, bytes] = {}
        self.steps: list[Any] = []
        self.detail_calls = 0

    # -- helpers for scripts
    def add(self, kind: str, **payload: Any) -> dict[str, Any]:
        self.clock += 1000
        event = {"id": f"ev{len(self.events)}", "type": kind, "timestamp": self.clock, kind: payload}
        self.events.append(event)
        return event

    def deliver(self, page: str, report: dict[str, Any] | None = REPORT, *, credit: int = 0) -> None:
        url = f"https://files.manuscdn.example/{len(self.files)}/index.html"
        self.files[url] = page.encode("utf-8")
        self.add("assistant_message", content="Done.", attachments=[{"type": "file", "filename": "index.html", "url": url}])
        self.add("status_update", agent_status="stopped")
        if report is not None:
            self.add("structured_output_result", success=True, value=report, error=None)
        self.status = "stopped"
        self.credit += credit

    def stop_without_file(self) -> None:
        self.add("assistant_message", content="All finished.")
        self.add("status_update", agent_status="stopped")
        self.status = "stopped"

    def ask(self, question: str, options: list[str] | None = None) -> None:
        msg: dict[str, Any] = {"content": question}
        if options:
            msg["question_expectation"] = {"options": options, "selection_mode": "single", "response_method": "send_message"}
        ask = self.add("assistant_message", **msg)
        self.add("status_update", agent_status="waiting", status_detail={"waiting_for_event_type": "messageAskUser", "waiting_for_event_id": ask["id"]})
        self.status = "waiting"

    def confirm(self, kind: str, description: str = "") -> None:
        target = self.add("tool_used", tool="terminal", brief="Running a command", message={"action": "Running a command"})
        self.add(
            "status_update",
            agent_status="waiting",
            status_detail={"waiting_for_event_type": kind, "waiting_for_event_id": target["id"], "waiting_description": description},
        )
        self.status = "waiting"

    # -- the API surface run.py uses
    async def create_task(self, **kwargs: Any) -> dict[str, Any]:
        self.created = kwargs
        self.add("user_message", content=kwargs["content"][0]["text"])
        return {"task_id": "task-1", "task_url": "https://manus.im/app/task-1"}

    async def send_message(self, task_id: str, content: Any, *, structured_output_schema: Any = None) -> dict[str, Any]:
        text = content if isinstance(content, str) else content[0]["text"]
        self.sent.append(content)
        self.sent_schemas.append(structured_output_schema)
        self.add("user_message", content=text)
        self.status = "running"
        return {"ok": True}

    async def confirm_action(self, task_id: str, event_id: str, input_: dict[str, Any]) -> dict[str, Any]:
        self.confirms.append((event_id, input_))
        self.status = "running"
        return {"ok": True, "confirmed": True}

    async def task_detail(self, task_id: str) -> dict[str, Any]:
        self.detail_calls += 1
        if self.steps:
            self.steps.pop(0)(self)
            self.idle_polls = 0
        else:
            # A run that keeps polling after the script ends would hang the suite.
            self.idle_polls = getattr(self, "idle_polls", 0) + 1
            if self.idle_polls > 20:
                raise RuntimeError("fake Manus script exhausted: the run never ended")
        return {"status": self.status, "credit_usage": self.credit, "has_running_background_jobs": False}

    async def list_messages(self, task_id: str, *, limit: int = 60, order: str = "desc", **_: Any) -> dict[str, Any]:
        ordered = sorted(self.events, key=lambda e: e["timestamp"], reverse=order == "desc")
        return {"messages": [dict(e) for e in ordered[:limit]]}

    async def stop_task(self, task_id: str) -> None:
        self.stopped.append(task_id)
        self.status = "stopped"

    async def download(self, url: str, **_: Any) -> bytes:
        return self.files[url]

    async def list_projects(self) -> list[dict[str, Any]]:
        return [{"id": "proj-9", "name": "Site previews"}]

    async def list_connectors(self) -> list[dict[str, Any]]:
        return [{"id": "conn-fc", "name": "Firecrawl"}]


class Settings:
    manus_api_key = "test-key"
    manus_project_id = ""
    manus_project_name = "Site previews"
    manus_connectors = "Firecrawl"
    manus_agent_profile = "standard"
    manus_timeout_minutes = 90
    manus_poll_seconds = 0.0
    manus_usd_per_credit = 0.0


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakeManus()
    for name in (
        "create_task", "send_message", "confirm_action", "task_detail", "list_messages",
        "stop_task", "download", "list_projects", "list_connectors",
    ):
        monkeypatch.setattr(run.manus, name, getattr(fake, name))
    monkeypatch.setattr(run.manus, "configured", lambda: True)
    settings = Settings()
    monkeypatch.setattr(run, "get_settings", lambda: settings)
    fake.settings = settings

    async def no_cache_get(_key):
        return None

    async def no_cache_set(_key, _value, _ttl=None):
        return None

    async def no_cache_delete(_key):
        return None

    monkeypatch.setattr(store, "cache_get", no_cache_get)
    monkeypatch.setattr(store, "cache_set", no_cache_set)
    monkeypatch.setattr(store, "cache_delete", no_cache_delete)
    store._memory.clear()
    run._loops.clear()
    run._project_cache.clear()
    run._connector_cache.clear()

    async def no_pause():
        return None

    monkeypatch.setattr(run, "_pause", no_pause)
    monkeypatch.setattr(run, "_spawn", lambda client_uuid, key: fake.__dict__.setdefault("spawned", []).append(key))
    monkeypatch.setattr(run, "assert_safe_url", lambda url: (True, url))

    async def every_page_loads(url, known_live=False):
        return True

    monkeypatch.setattr(design_page, "page_loads", every_page_loads)

    meter: list[dict[str, Any]] = []

    async def record(**kwargs):
        meter.append(kwargs)

    monkeypatch.setattr(run, "record_api_call", record)
    fake.meter = meter

    answers: list[dict[str, Any]] = []

    async def answer(question, **kwargs):
        answers.append({"question": question, **kwargs})
        return f"Use {kwargs['options'][0]}." if kwargs.get("options") else "Follow brief.md.", True

    monkeypatch.setattr(run, "answer_question", answer)
    fake.answers = answers

    (tmp_path / "hero.png").write_bytes(PNG)
    monkeypatch.setattr("app.integrations.llm.draft_images_dir", lambda: Path(tmp_path))
    return fake


def ctx(**overrides: Any) -> run.PreviewContext:
    base: dict[str, Any] = {"client_id": uuid4(), "client_name": "Acme Plumbing", "primary_url": "https://acme.example"}
    base.update(overrides)
    return run.PreviewContext(**base)


async def build_and_follow(fake: FakeManus, context: run.PreviewContext, draft=DRAFT, **kwargs) -> dict[str, Any]:
    await run.start_build(context, draft, **kwargs)
    await run._follow(context.client_id, store.page_key(draft))
    return await store.load_state(str(context.client_id), store.page_key(draft))


# --------------------------------------------------------------- the build


@pytest.mark.asyncio
async def test_build_sends_brief_draft_and_images_and_collects_the_page(fake):
    fake.steps = [lambda f: f.add("assistant_message", content="Opening the reference page."), lambda f: f.deliver(good_page(), credit=420)]
    context = ctx()
    state = await build_and_follow(fake, context)

    sent = fake.created
    assert sent["structured_output_schema"] == brief_mod.RESULT_SCHEMA
    assert sent["project_id"] == "proj-9"
    assert sent["connectors"] == ["conn-fc"]
    assert sent["agent_profile"] == "standard"
    parts = {p.get("filename"): p for p in sent["content"] if p["type"] == "file"}
    assert set(parts) == {"brief.md", "draft.md", "image-1.png"}
    draft_md = base64.b64decode(parts["draft.md"]["file_data"].split(",", 1)[1]).decode()
    assert "![A plumber fixing a burst pipe](image-1.png)" in draft_md
    assert "/media/drafts/" not in draft_md
    assert parts["image-1.png"]["file_data"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    brief_md = base64.b64decode(parts["brief.md"]["file_data"].split(",", 1)[1]).decode()
    assert "Do not deploy" in brief_md and 'caption "On site in Brunswick"' in brief_md
    assert "https://acme.example/services/emergency-plumbing" in brief_md

    assert state["status"] == "done"
    assert state["progress"] == 100
    assert state["review_notes"] == []
    assert await store.load_html(str(context.client_id), store.page_key(DRAFT)) == good_page()
    assert state["credits"] == 420
    assert fake.meter and fake.meter[0]["provider"] == "manus"
    assert fake.meter[0]["request_meta"]["credits"] == 420
    assert fake.meter[0]["estimated_cost_usd"] is None  # no dollar rate set: unknown, not $0
    public = run.public_state(state, has_html=True)
    assert "task_id" not in public and "brief" not in public and "handled_event_ids" not in public


@pytest.mark.asyncio
async def test_credits_are_priced_only_when_a_rate_is_set(fake):
    fake.settings.manus_usd_per_credit = 0.01
    fake.steps = [lambda f: f.deliver(good_page(), credit=300)]
    await build_and_follow(fake, ctx())
    assert fake.meter[0]["estimated_cost_usd"] == 3.0


@pytest.mark.asyncio
async def test_questions_are_answered_once_with_the_first_option(fake):
    fake.steps = [
        lambda f: f.ask("Which header layout should I copy?", ["Sticky header", "Static header"]),
        lambda f: None,  # still waiting: the same question must not be answered twice
        lambda f: f.deliver(good_page()),
    ]
    fake.steps.insert(1, lambda f: setattr(f, "status", "waiting"))
    state = await build_and_follow(fake, ctx())
    assert state["status"] == "done"
    assert len(fake.answers) == 1
    assert fake.answers[0]["options"] == ["Sticky header", "Static header"]
    assert fake.sent == ["Use Sticky header."]
    assert state["questions"][0]["question"] == "Which header layout should I copy?"


@pytest.mark.asyncio
async def test_confirmations_follow_the_policy_and_never_deploy(fake):
    fake.steps = [
        lambda f: f.confirm("terminalExecute", "python3 measure.py"),
        lambda f: f.confirm("deployAction", "Deploy the site"),
        lambda f: f.confirm("webdevRunAction", "Publish the web app publicly"),
        lambda f: f.deliver(good_page()),
    ]
    state = await build_and_follow(fake, ctx())
    assert state["status"] == "done"
    assert [c[1] for c in fake.confirms] == [{"accept": True, "always_allow": True}]
    assert len(fake.sent) == 2
    assert all("Skip that step" in str(m) and "Nothing may be deployed" in str(m) for m in fake.sent)


def test_confirmation_policy_table():
    assert events.confirmation_for({"waiting_for_event_type": "apiHighCreditNotice"}).input == {"action": "accept"}
    assert events.confirmation_for({"waiting_for_event_type": "webdevRequestSecrets"}).input == {"accept": False}
    assert events.confirmation_for({"waiting_for_event_type": "needConnectMyBrowser"}).input == {"action": "skip"}
    assert events.confirmation_for({"waiting_for_event_type": "webdevRunAction"}).input == {"accept": True, "mode": "speed"}
    assert events.confirmation_for({"waiting_for_event_type": "gmailSendAction"}).kind == "message"
    assert events.confirmation_for({"waiting_for_event_type": "videoGenerate"}).kind == "message"


@pytest.mark.asyncio
async def test_missing_file_is_asked_for_then_collected(fake, monkeypatch):
    monkeypatch.setattr(run, "FILE_WAIT_SECONDS", 0)
    fake.steps = [lambda f: f.stop_without_file(), lambda f: f.deliver(good_page())]
    state = await build_and_follow(fake, ctx())
    assert state["status"] == "done"
    assert state["nudges"] == 1
    assert fake.sent == [[{"type": "text", "text": brief_mod.NUDGE_MESSAGE}]]
    assert fake.sent_schemas == [brief_mod.RESULT_SCHEMA]


@pytest.mark.asyncio
async def test_no_file_after_two_nudges_fails_visibly(fake, monkeypatch):
    monkeypatch.setattr(run, "FILE_WAIT_SECONDS", 0)
    fake.steps = [lambda f: f.stop_without_file()] * 3
    state = await build_and_follow(fake, ctx())
    assert state["status"] == "failed"
    assert "without attaching the page" in state["error"]
    assert len(fake.sent) == 2


@pytest.mark.asyncio
async def test_repeated_photo_gets_one_fix_round(fake):
    fake.steps = [lambda f: f.deliver(good_page(photo_twice=True)), lambda f: f.deliver(good_page())]
    context = ctx()
    state = await build_and_follow(fake, context)
    assert state["status"] == "done"
    assert state["image_fixes"] == 1
    assert "same photo more than once" in fake.sent[0][0]["text"]
    assert await store.load_html(str(context.client_id), store.page_key(DRAFT)) == good_page()


@pytest.mark.asyncio
async def test_a_second_repeat_is_reported_not_looped(fake):
    fake.steps = [lambda f: f.deliver(good_page(photo_twice=True))] * 2
    state = await build_and_follow(fake, ctx())
    assert state["status"] == "done"
    assert len(fake.sent) == 1
    assert any("photo is used 2 times" in note for note in state["review_notes"])


@pytest.mark.asyncio
async def test_agent_error_fails_the_run(fake):
    def boom(f):
        f.add("error_message", error_type="internal", content="Sandbox crashed")
        f.status = "error"

    fake.steps = [boom]
    state = await build_and_follow(fake, ctx())
    assert state["status"] == "failed"
    assert "Sandbox crashed" in state["error"]
    assert fake.meter and fake.meter[0]["status"] == "error"


@pytest.mark.asyncio
async def test_timeout_stops_the_task(fake):
    context = ctx()
    cid, key = str(context.client_id), store.page_key(DRAFT)
    await run.start_build(context, DRAFT)
    state = await store.load_state(cid, key)
    state["started_at"] = time.time() - 91 * 60
    await store.save_state(cid, key, state)
    await run._follow(context.client_id, key)
    state = await store.load_state(cid, key)
    assert state["status"] == "failed"
    assert "90 minutes" in state["error"]
    assert fake.stopped == ["task-1"]


# ---------------------------------------------------------- user controls


@pytest.mark.asyncio
async def test_edit_reuses_the_task_and_undo_restores_the_earlier_page(fake):
    context = ctx()
    cid, key = str(context.client_id), store.page_key(DRAFT)
    fake.steps = [lambda f: f.deliver(good_page(), credit=100)]
    await build_and_follow(fake, context)
    first = await store.load_html(cid, key)

    edited = good_page().replace("<footer>", "<p>Bigger call button</p><footer>")
    fake.steps = [lambda f: f.add("assistant_message", content="Updating the button."), lambda f: f.deliver(edited, credit=40)]
    await run.start_edit(context, DRAFT, instructions="Make the call button bigger.")
    assert "Make the call button bigger." in fake.sent[-1][0]["text"]
    assert fake.sent_schemas[-1] == brief_mod.RESULT_SCHEMA
    await run._follow(context.client_id, key)
    state = await store.load_state(cid, key)
    assert state["status"] == "done" and state["mode"] == "edit"
    assert state["credits"] == 40  # this run's credits, not the task total
    assert await store.load_html(cid, key) == edited
    assert state["can_undo"] is True

    undone = await run.undo(cid, key)
    assert await store.load_html(cid, key) == first
    assert undone["canUndo"] is False
    with pytest.raises(run.PreviewError):
        await run.undo(cid, key)


@pytest.mark.asyncio
async def test_edit_ignores_the_previous_turns_stop(fake):
    """Right after an edit is sent, Manus can still report the last turn's stop. The
    old page must not be collected as the edit's result."""
    context = ctx()
    cid, key = str(context.client_id), store.page_key(DRAFT)
    fake.steps = [lambda f: f.deliver(good_page())]
    await build_and_follow(fake, context)
    edited = good_page().replace("<footer>", "<p>New banner</p><footer>")
    fake.steps = [
        lambda f: None,  # start_edit reads the credit baseline
        lambda f: setattr(f, "status", "stopped"),  # stale stop from the build's turn
        lambda f: f.deliver(edited),
    ]
    await run.start_edit(context, DRAFT, instructions="Add the new banner.")
    await run._follow(context.client_id, key)
    assert await store.load_html(cid, key) == edited


@pytest.mark.asyncio
async def test_edit_without_a_page_starts_a_build_with_the_request(fake):
    context = ctx()
    await run.start_edit(context, DRAFT, instructions="Use the dark header.")
    assert fake.created is not None
    assert "Use the dark header." in fake.created["content"][0]["text"]


@pytest.mark.asyncio
async def test_stop_halts_the_task_and_records_credits(fake):
    context = ctx()
    cid, key = str(context.client_id), store.page_key(DRAFT)
    await run.start_build(context, DRAFT)
    fake.credit = 75
    state = await run.stop(cid, key)
    assert fake.stopped == ["task-1"]
    assert state["status"] == "stopped"
    assert fake.meter[0]["request_meta"]["credits"] == 75


@pytest.mark.asyncio
async def test_a_new_build_stops_the_running_one(fake):
    context = ctx()
    await run.start_build(context, DRAFT)
    await run.start_build(context, DRAFT)
    assert fake.stopped == ["task-1"]


@pytest.mark.asyncio
async def test_state_poll_resumes_an_active_run(fake):
    context = ctx()
    await run.start_build(context, DRAFT)
    fake.spawned = []
    state = await run.get_state(context.client_id, store.page_key(DRAFT))
    assert state["status"] == "running"
    assert fake.spawned == [store.page_key(DRAFT)]


@pytest.mark.asyncio
async def test_not_configured_is_a_clear_error(fake, monkeypatch):
    monkeypatch.setattr(run.manus, "configured", lambda: False)
    with pytest.raises(run.PreviewError) as err:
        await run.start_build(ctx(), DRAFT)
    assert err.value.code == "not_configured"


def test_screenshots_must_be_real_images():
    png = "data:image/png;base64," + base64.b64encode(PNG).decode()
    parts, names = run.screenshot_parts([png])
    assert names == ["screenshot-1.png"] and parts[0]["mime_type"] == "image/png"
    svg = "data:image/png;base64," + base64.b64encode(b"<svg onload=alert(1)>").decode()
    with pytest.raises(run.PreviewError):
        run.screenshot_parts([svg])


# ------------------------------------------------------------------ checks


def test_review_notes_catch_rewritten_copy_and_missing_links():
    page = good_page().replace("We repair burst pipes", "Our team fixes burst pipes").replace(
        'href="/services/blocked-drains"', 'href="/contact"'
    )
    notes = checks.review_notes(page, draft_markdown=DRAFT["markdown"], title=DRAFT["title"], report=REPORT)
    assert any("sentences appear word for word" in n for n in notes)
    assert any("draft link(s) missing" in n for n in notes)


def test_review_notes_flag_two_h1s_and_unverified_work():
    page = good_page().replace("<main>", "<main><h1>Another</h1>")
    report = {**REPORT, "verification": {"desktop_compared": True, "mobile_compared": False, "notes": ""}}
    notes = checks.review_notes(page, draft_markdown=DRAFT["markdown"], title=DRAFT["title"], report=report)
    assert any("2 h1 headings" in n for n in notes)
    assert any("desktop and mobile" in n for n in notes)


def test_progress_never_moves_back_and_caps_before_collection():
    started = time.time() - 3600
    assert 75 <= events.progress_percent("check", [], started, 0) <= 92  # within the stage's band
    assert events.progress_percent("brief", [], started, 0) == 6  # capped at the stage ceiling
    assert events.progress_percent("study", [], time.time(), 50) == 50
    assert events.progress_percent("collect", [{"status": "done"}], started, 0) <= 95


def test_repeated_images_ignore_logos_and_size_variants():
    page = (
        '<img src="https://a.example/logo.png" alt="logo"><img src="https://a.example/logo.png" alt="logo">'
        '<img src="https://a.example/team-300x200.jpg"><img src="https://a.example/team.jpg?w=800">'
    )
    repeats = images.find_repeated_images(page)
    assert len(repeats) == 1 and repeats[0]["count"] == 2


# ------------------------------------------------------- safe image fetch


@pytest.mark.asyncio
async def test_attachment_download_rechecks_every_redirect_and_caps_size(monkeypatch):
    from app.integrations import manus

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/hop":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 2048)
        return httpx.Response(200, content=b"<html><h1>ok</h1></html>")

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        assert kwargs.get("follow_redirects") is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    def safe(url: str):
        return (True, url) if httpx.URL(url).host == "files.manuscdn.example" else (False, "blocked_unsafe_host")

    monkeypatch.setattr(manus.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(manus, "assert_safe_url", safe)

    assert await manus.download("https://files.manuscdn.example/page") == b"<html><h1>ok</h1></html>"
    with pytest.raises(manus.ManusError) as err:
        await manus.download("https://files.manuscdn.example/hop")
    assert err.value.code == "unsafe_url"
    assert not any("169.254" in c for c in calls)
    with pytest.raises(manus.ManusError) as err:
        await manus.download("https://files.manuscdn.example/big", max_bytes=1024)
    assert err.value.code == "too_large"


# ------------------------------------------------------------- design page

SITE = "https://acme.example"
CRAWL = [
    {"url": f"{SITE}/", "page_type": "home", "status": "200"},
    {"url": f"{SITE}/services/blocked-drains", "page_type": "service", "status": "200"},
    {"url": f"{SITE}/services/hot-water", "page_type": "service", "status": None},
    {"url": f"{SITE}/services/hot-water/gas", "page_type": "sub_service", "status": "200"},
    {"url": f"{SITE}/blog/why-pipes-burst", "page_type": "page", "status": "200"},
    {"url": f"{SITE}/blog/old-post", "page_type": "page", "status": "500"},
    {"url": f"{SITE}/about", "page_type": "company", "status": "200"},
]


def loads_only(*urls):
    async def check(url, known_live=False):
        return url in urls

    return check


@pytest.mark.asyncio
async def test_an_optimised_page_copies_its_own_live_page(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(f"{SITE}/services/emergency-plumbing", f"{SITE}/services/blocked-drains"))
    draft = {"url": "/services/emergency-plumbing", "page_type": "service", "action": "refresh"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert (picked.url, picked.kind) == (f"{SITE}/services/emergency-plumbing", "own_page")


@pytest.mark.asyncio
async def test_an_optimised_page_that_no_longer_loads_falls_back_to_the_same_kind(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(f"{SITE}/services/blocked-drains"))
    draft = {"url": "/services/emergency-plumbing", "page_type": "service", "action": "refresh"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert (picked.url, picked.kind) == (f"{SITE}/services/blocked-drains", "same_kind")


@pytest.mark.asyncio
async def test_a_new_blog_post_copies_a_live_blog_post_not_a_service_page(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(*(p["url"] for p in CRAWL)))
    draft = {"url": "/blog/how-to-clear-a-drain", "page_type": "blog", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert picked.url == f"{SITE}/blog/why-pipes-burst"  # typed "page" by the crawl, found by its address
    assert "blog post" in picked.reason


@pytest.mark.asyncio
async def test_a_new_service_page_copies_a_live_service_page(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(*(p["url"] for p in CRAWL)))
    draft = {"url": "/services/gas-fitting", "page_type": "service", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert picked.url == f"{SITE}/services/blocked-drains"  # loaded in the crawl beats unchecked


@pytest.mark.asyncio
async def test_a_sub_service_prefers_a_sub_service_page(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(*(p["url"] for p in CRAWL)))
    draft = {"url": "/services/hot-water/electric", "page_type": "sub_service", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert picked.url == f"{SITE}/services/hot-water/gas"


@pytest.fixture(autouse=True)
def no_wordpress_api(monkeypatch):
    async def none(_primary_url, limit=3):
        return []

    monkeypatch.setattr(design_page, "latest_blog_posts", none)


@pytest.mark.asyncio
async def test_pages_that_do_not_load_are_skipped_then_parent_then_home(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(f"{SITE}/services"))
    draft = {"url": "/blog/new-post", "page_type": "blog", "action": "create"}
    picked = await design_page.choose_design_page(
        draft, primary_url=SITE, live_pages=CRAWL, parent={"url": f"{SITE}/services"}
    )
    assert (picked.url, picked.kind) == (f"{SITE}/services", "parent")
    monkeypatch.setattr(design_page, "page_loads", loads_only())
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert (picked.url, picked.kind) == (SITE, "home")


def test_dead_pages_and_the_home_page_are_never_candidates():
    urls = [u for u, _k in design_page.same_kind_candidates("blog", f"{SITE}/blog/x", CRAWL, SITE)]
    assert f"{SITE}/blog/old-post" not in urls
    assert f"{SITE}/" not in urls and SITE not in urls


@pytest.mark.asyncio
async def test_roadmap_neighbours_are_not_used_as_the_parent(monkeypatch):
    seen: dict = {}

    async def fake_choose(draft, *, primary_url, live_pages, parent=None):
        seen["parent"] = parent
        return design_page.DesignPage(primary_url, "home", "home")

    monkeypatch.setattr(
        "app.services.design_capture.resolve_previous_design_page",
        lambda **_: {"url": f"{SITE}/some-planned-page", "reason": "roadmap_previous"},
    )
    monkeypatch.setattr(run, "choose_design_page", fake_choose)
    await run.pick_design_page(run.PreviewContext(client_id=uuid4(), client_name="Acme", primary_url=SITE), {"url": "/x"})
    assert seen["parent"] is None


def test_same_kind_beats_a_closer_page_of_another_kind():
    crawl = [
        {"url": f"{SITE}/plumbing", "page_type": "hub", "status": "200"},
        {"url": f"{SITE}/other/roof-repair", "page_type": "service", "status": "200"},
    ]
    urls = [u for u, _k in design_page.same_kind_candidates("service", f"{SITE}/plumbing/leak-repair", crawl, SITE)]
    assert urls[0] == f"{SITE}/other/roof-repair"


def test_among_same_kind_pages_the_nearest_in_the_url_tree_wins():
    crawl = [
        {"url": f"{SITE}/roofing/roof-repair", "page_type": "service", "status": "200"},
        {"url": f"{SITE}/plumbing/drain-repair", "page_type": "service", "status": "200"},
    ]
    urls = [u for u, _k in design_page.same_kind_candidates("service", f"{SITE}/plumbing/leak-repair", crawl, SITE)]
    assert urls[0] == f"{SITE}/plumbing/drain-repair"


ROOT_POSTS_CRAWL = [
    {"url": f"{SITE}/7-steps-to-a-better-mortgage", "page_type": "page", "status": "200"},
    {"url": f"{SITE}/blog", "page_type": "conversion", "status": "200"},
    {"url": f"{SITE}/contact-us", "page_type": "conversion", "status": "200"},
]


@pytest.mark.asyncio
async def test_root_level_blog_posts_are_found_through_wordpress(monkeypatch):
    async def posts(_primary_url, limit=3):
        return [f"{SITE}/7-steps-to-a-better-mortgage"]

    monkeypatch.setattr(design_page, "latest_blog_posts", posts)
    monkeypatch.setattr(design_page, "page_loads", loads_only(f"{SITE}/7-steps-to-a-better-mortgage", f"{SITE}/blog"))
    draft = {"url": "/blog/new-post", "page_type": "blog", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=ROOT_POSTS_CRAWL)
    assert (picked.url, picked.kind) == (f"{SITE}/7-steps-to-a-better-mortgage", "same_kind")


@pytest.mark.asyncio
async def test_without_wordpress_a_blog_draft_uses_the_blog_index(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(f"{SITE}/blog"))
    draft = {"url": "/blog/new-post", "page_type": "blog", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=ROOT_POSTS_CRAWL)
    assert picked.url == f"{SITE}/blog"
    assert "open one of the recent posts" in picked.reason


@pytest.mark.asyncio
async def test_a_landing_page_does_not_copy_the_contact_page(monkeypatch):
    monkeypatch.setattr(design_page, "page_loads", loads_only(*(p["url"] for p in ROOT_POSTS_CRAWL)))
    draft = {"url": "/pricing/ppc", "page_type": "landing", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=ROOT_POSTS_CRAWL)
    assert picked.url != f"{SITE}/contact-us"


@pytest.mark.asyncio
async def test_wordpress_post_links_must_be_on_the_clients_site(monkeypatch):
    async def fake_fetch(url, timeout=0):
        assert url == f"{SITE}/wp-json/wp/v2/posts?per_page=3&_fields=link"
        body = [{"link": f"{SITE}/a-post"}, {"link": "https://evil.example/x"}, {"link": "javascript:alert(1)"}]
        return {"status_code": 200, "text": json.dumps(body)}

    monkeypatch.setattr(design_page, "fetch_url", fake_fetch)
    assert await REAL_LATEST_BLOG_POSTS(SITE) == [f"{SITE}/a-post"]


@pytest.mark.asyncio
async def test_a_site_that_blocks_this_server_still_gets_its_listed_pages(monkeypatch):
    """Client sites often answer this server with 403; the agent's browser gets through."""

    async def blocked(url):
        return "blocked"

    monkeypatch.setattr(design_page, "page_status", blocked)
    draft = {"url": "/services/gas-fitting", "page_type": "service", "action": "create"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert picked.url == f"{SITE}/services/blocked-drains"
    # An unlisted guess (the parent from a plan) is not trusted when blocked.
    picked = await design_page.choose_design_page(
        {"url": "/tools/calc", "page_type": "tool"}, primary_url=SITE, live_pages=CRAWL, parent={"url": f"{SITE}/planned-hub"}
    )
    assert picked.kind == "home"


@pytest.mark.asyncio
async def test_missing_pages_are_never_used_even_when_listed(monkeypatch):
    async def missing(url):
        return "missing"

    monkeypatch.setattr(design_page, "page_status", missing)
    draft = {"url": "/services/emergency-plumbing", "page_type": "service", "action": "refresh"}
    picked = await design_page.choose_design_page(draft, primary_url=SITE, live_pages=CRAWL)
    assert picked.kind == "home"


# ------------------------------------------------- unfinished drafts and page checks

TEMPLATE_DRAFT = dict(
    DRAFT,
    markdown=(
        "# AI Marketing\n\n## What is included\n\nClick Trends helps founders with AI Marketing. "
        "What is included explains the practical detail a buyer needs before they enquire.\n\n"
        "Priorities under this heading include Best 20+ AI Marketing Tools. Click Trends keeps this "
        "section on AI Marketing \u2014 not adjacent services.\n\n## FAQ\n\n### Can I use AI?\n\n"
        "[AUTHOR INPUT REQUIRED \u2014 write a direct, self-contained answer]\n"
    ),
)


@pytest.mark.asyncio
async def test_a_template_draft_is_refused_before_any_manus_call(fake):
    with pytest.raises(run.PreviewError) as err:
        await run.start_build(ctx(), TEMPLATE_DRAFT)
    assert err.value.code == "draft_not_ready"
    assert "template filler" in err.value.message and "AUTHOR INPUT REQUIRED" in err.value.message
    assert fake.created is None


def test_draft_problems_and_missing_figures():
    assert checks.draft_problems(DRAFT["markdown"]) == []
    assert checks.draft_problems("Prices start at $99 [VERIFY].") == []
    md = "## A\n\n[FIGURE hero] AI Marketing \u2014 hero\n\nText.\n"
    assert "[FIGURE" not in checks.strip_missing_figures(md)
    assert "Text." in checks.strip_missing_figures(md)


@pytest.mark.asyncio
async def test_missing_image_slots_are_not_sent_to_the_agent(fake):
    draft = dict(DRAFT, markdown=DRAFT["markdown"] + "\n[FIGURE supporting] AI Marketing \u2014 supporting\n")
    await run.start_build(ctx(), draft)
    parts = {p.get("filename"): p for p in fake.created["content"] if p["type"] == "file"}
    draft_md = base64.b64decode(parts["draft.md"]["file_data"].split(",", 1)[1]).decode()
    assert "[FIGURE" not in draft_md


def test_page_checks_flag_what_the_reviewer_found():
    bad = good_page().replace(HEAD, "").replace(
        '<nav><a href="/">Home</a></nav>',
        '<nav><a href="https://acme.example/?page_id=15596">Google Ads</a><a href="#services">Services</a></nav>',
    ).replace('alt="Acme logo"', 'alt="Mobile-friendly Magento store preview"').replace(
        '<p>Read about our <a href="/services/blocked-drains">blocked drain service</a> for more detail.</p>', ""
    ) + "<p>[FIGURE hero] AI Marketing</p>"
    notes = " | ".join(checks.page_problems(bad, client_name="Acme Plumbing"))
    for expected in ("canonical link", "og:image", "twitter:card", "favicon", "JSON-LD", "?page_id=",
                     "#services", "logo's alt text", "no button or link", "[FIGURE"):
        assert expected in notes, expected
    assert checks.page_problems(good_page(), client_name="Acme Plumbing") == []
