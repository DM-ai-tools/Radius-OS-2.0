"""Chromium child process used when uvicorn's Windows loop cannot spawn Playwright."""

from __future__ import annotations

import asyncio
import io
import json
import sys

from app.integrations.chromium_sync import ChromiumSession
from app.integrations.chromium_worker import playwright_loop_policy


def test_worker_policy_is_proactor_on_windows():
    policy = playwright_loop_policy()
    if sys.platform == "win32":
        assert isinstance(policy, asyncio.WindowsProactorEventLoopPolicy)


def test_siteground_meta_refresh_is_followed():
    from app.integrations.chromium_worker import _meta_refresh_target

    html = (
        '<html><head><link rel="icon" href="data:;"><meta http-equiv="refresh" '
        'content="0;/.well-known/sgcaptcha/?r=%2F"></head></html>'
    )
    target = _meta_refresh_target(html, "https://clicktrends.com.au/")
    assert target is not None
    assert target.startswith("https://clicktrends.com.au/.well-known/sgcaptcha/")


def test_chromium_session_starts_worker_module(monkeypatch):
    stdout_lines = [json.dumps({"ready": True}) + "\n"]
    writes: list[str] = []

    class FakeStdout:
        def readline(self):
            return stdout_lines.pop(0) if stdout_lines else ""

    class FakeStdin:
        def write(self, data: str):
            writes.append(data)
            msg = json.loads(data)
            if msg.get("op") == "post":
                stdout_lines.append(
                    json.dumps({"ok": True, "status": 200, "text": "<?xml version='1.0'?><ok/>"})
                    + "\n"
                )

        def flush(self):
            return None

    class FakeProc:
        def __init__(self, **kwargs):
            FakeProc.kwargs = kwargs
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = io.StringIO()

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(
        "app.integrations.chromium_sync.subprocess.Popen",
        lambda **kwargs: FakeProc(**kwargs),
    )
    session = ChromiumSession(user_agent="TestUA/1.0")
    args = FakeProc.kwargs["args"]
    assert args[0] == sys.executable
    assert args[1:] == ["-m", "app.integrations.chromium_worker"]
    assert FakeProc.kwargs["env"]["WP_CHROMIUM_UA"] == "TestUA/1.0"

    status, text = session.post_xmlrpc(
        "https://example.com/xmlrpc.php",
        {"Content-Type": "text/xml"},
        "<methodCall/>",
        origin="https://example.com/",
    )
    assert status == 200
    assert text.startswith("<?xml")
    posted = json.loads(writes[0])
    assert posted["op"] == "post"
    assert posted["url"] == "https://example.com/xmlrpc.php"
    session.close()


def test_worker_posts_xmlrpc_from_inside_the_page():
    """Playwright's APIRequestContext is what SiteGround hung up; fetch() runs in-page."""
    import asyncio
    from app.integrations.chromium_worker import xmlrpc_from_page

    calls = {"evaluate": 0, "request_post": 0}

    class _FakePage:
        url = "https://www.example.com/challenge"

        async def goto(self, url, **kwargs):
            self.url = url

        async def wait_for_timeout(self, _ms):
            return None

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        async def content(self):
            return "<html><body>" + ("welcome " * 80) + "</body></html>"

        async def evaluate(self, script, arg):
            calls["evaluate"] += 1
            assert "fetch" in script
            assert arg["body"] == "<methodCall/>"
            return {
                "status": 200,
                "text": "<?xml version='1.0'?><methodResponse/>",
                "url": "https://www.example.com/xmlrpc.php",
            }

        @property
        def request(self):
            class _Req:
                async def post(self, *args, **kwargs):
                    calls["request_post"] += 1
                    raise AssertionError("APIRequestContext.post must not be used")

            return _Req()

    result = asyncio.run(
        xmlrpc_from_page(
            _FakePage(),
            origin="https://www.example.com/",
            body="<methodCall/>",
            content_type="text/xml",
        )
    )
    assert calls["evaluate"] == 1
    assert calls["request_post"] == 0
    assert result["status"] == 200
    assert result["text"].startswith("<?xml")
