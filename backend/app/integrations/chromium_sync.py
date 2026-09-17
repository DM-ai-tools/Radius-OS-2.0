"""Playwright Chromium in a child process, off the uvicorn event loop.

Uvicorn on Windows uses SelectorEventLoopPolicy. That policy cannot spawn
subprocesses, so in-process Playwright raises ``NotImplementedError``. The
child started here is a fresh Python with a Proactor loop (see
``chromium_worker``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class ChromiumSession:
    """One Chromium child for a verify/publish burst."""

    def __init__(self, *, user_agent: str) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["WP_CHROMIUM_UA"] = user_agent or ""
        kwargs: dict[str, Any] = dict(
            args=[sys.executable, "-m", "app.integrations.chromium_worker"],
            cwd=str(_BACKEND_ROOT),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        self._proc = subprocess.Popen(**kwargs)
        self._err: list[str] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._drain_stderr, name="chromium-stderr", daemon=True).start()
        ready = self._readline(timeout=30)
        if not ready.get("ready"):
            raise RuntimeError(self._fail_message(ready.get("error") or "Chromium worker did not become ready"))

    def _drain_stderr(self) -> None:
        if self._proc.stderr is None:
            return
        try:
            for line in self._proc.stderr:
                self._err.append(line.rstrip())
                if len(self._err) > 40:
                    self._err.pop(0)
        except Exception:  # noqa: BLE001
            return

    def _fail_message(self, prefix: str) -> str:
        tail = " ".join(self._err[-8:]).strip()
        if tail:
            return f"{prefix} ({tail})"
        code = self._proc.poll()
        if code is not None:
            return f"{prefix} (worker exit {code})"
        return prefix

    def _readline(self, *, timeout: float) -> dict[str, Any]:
        if self._proc.stdout is None:
            raise RuntimeError("Chromium worker has no stdout")
        box: list[str] = []
        error: list[BaseException] = []

        def _read() -> None:
            try:
                line = self._proc.stdout.readline()
                box.append(line)
            except Exception as exc:  # noqa: BLE001
                error.append(exc)

        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout=timeout)
        if reader.is_alive():
            raise TimeoutError(self._fail_message("Chromium worker timed out"))
        if error:
            raise error[0]
        raw = (box[0] if box else "").strip()
        if not raw:
            raise RuntimeError(self._fail_message("Chromium worker closed stdout"))
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(self._fail_message(f"Chromium worker sent non-JSON: {raw[:80]}")) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError(self._fail_message("Chromium worker sent a non-object"))
        return parsed

    def post_xmlrpc(
        self,
        url: str,
        headers: dict[str, str],
        body: str,
        origin: str | None = None,
        *,
        timeout: float = 45.0,
    ) -> tuple[int, str]:
        payload = {
            "op": "post",
            "url": url,
            "headers": headers,
            "body": body,
            "origin": origin or "",
        }
        with self._lock:
            if self._proc.stdin is None:
                raise RuntimeError("Chromium worker has no stdin")
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            result = self._readline(timeout=timeout)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or self._fail_message("Chromium XML-RPC failed")))
        return int(result.get("status") or 0), str(result.get("text") or "")

    def rest_get(
        self,
        url: str,
        headers: dict[str, str],
        origin: str | None = None,
        *,
        timeout: float = 90.0,
    ) -> tuple[int, str]:
        payload = {
            "op": "rest",
            "url": url,
            "headers": headers,
            "origin": origin or "",
        }
        with self._lock:
            if self._proc.stdin is None:
                raise RuntimeError("Chromium worker has no stdin")
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
            result = self._readline(timeout=timeout)
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or self._fail_message("Chromium REST failed")))
        return int(result.get("status") or 0), str(result.get("text") or "")

    def close(self) -> None:
        try:
            if self._proc.stdin and self._proc.poll() is None:
                self._proc.stdin.write(json.dumps({"op": "close"}) + "\n")
                self._proc.stdin.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            self._proc.kill()
