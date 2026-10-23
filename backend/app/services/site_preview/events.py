"""Turning Manus task events into decisions, a live feed and a progress bar."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

QUESTION_TYPES = {"messageAskUser", "cascadeAskUser"}

STAGES: list[tuple[str, str, int, int]] = [
    # key, label, floor %, ceiling %
    ("brief", "Sending the brief", 0, 6),
    ("study", "Studying the client's site design", 6, 35),
    ("build", "Building the page", 35, 75),
    ("check", "Checking it against the client's page", 75, 92),
    ("collect", "Collecting the finished page", 92, 98),
]
_STAGE_INDEX = {key: i for i, (key, *_rest) in enumerate(STAGES)}
_STAGE_WORDS: list[tuple[str, re.Pattern[str]]] = [
    ("check", re.compile(r"\b(compar|verif|check|screenshot of (my|the) (page|index)|visual check|review(ing)? the page)", re.IGNORECASE)),
    ("build", re.compile(r"\b(writ\w* (the )?(html|index|page|css)|build|creat\w* (the )?(page|file|index)|layout|embed|styl\w* the)", re.IGNORECASE)),
    ("study", re.compile(r"\b(brows|open\w* (the )?(site|page|reference)|scrap|measur|getcomputedstyle|inspect|reading the|analy[sz])", re.IGNORECASE)),
]
ELAPSED_CURVE_SECONDS = 30 * 60


@dataclass
class Decision:
    """How to settle a waiting confirmation: confirm with ``input``, or send ``message``."""

    kind: str  # "confirm" | "message"
    input: dict[str, Any] | None = None
    message: str = ""
    reason: str = ""


def _skip(what: str, reason: str) -> Decision:
    return Decision(
        "message",
        message=f"Skip that step ({what}). {reason} Carry on with brief.md and deliver index.html.",
        reason=reason,
    )


def confirmation_for(detail: dict[str, Any]) -> Decision:
    """The policy for every action the agent asks to confirm. Nothing outward-facing
    (deploying, publishing, secrets, video) is ever accepted."""
    kind = str(detail.get("waiting_for_event_type") or "")
    description = str(detail.get("waiting_description") or "")
    schema = detail.get("confirm_input_schema") or {}
    props = set((schema.get("properties") or {}).keys()) if isinstance(schema, dict) else set()
    outward = re.search(r"\b(deploy|publish|go live|make (it )?public)\b", description, re.IGNORECASE)
    if kind == "terminalExecute":
        return Decision("confirm", {"accept": True, "always_allow": True})
    if kind == "webdevRunAction":
        if outward:
            return _skip(description or "deploy", "Nothing may be deployed or published.")
        return Decision("confirm", {"accept": True, "mode": "speed"})
    if kind == "apiHighCreditNotice":
        return Decision("confirm", {"action": "accept"})
    if kind == "mapreduceAction":
        return Decision("confirm", {"accept": True})
    if kind == "needConnectMyBrowser":
        return Decision("confirm", {"action": "skip"}, reason="Use your own browser.")
    if kind == "webdevRequestSecrets":
        return Decision("confirm", {"accept": False}, reason="No secrets are available; the page needs none.")
    if kind == "videoGenerate":
        return _skip("video generation", "The page needs no video.")
    if kind == "deployAction":
        return _skip("deploy", "Nothing may be deployed or published.")
    if "accept" in props and not outward:
        return _skip(description or kind, "The preview does not need it.")
    return _skip(description or kind or "that action", "The preview does not need it.")


# ------------------------------------------------------------------- feed


def _clean(text: str, limit: int = 240) -> str:
    text = re.sub(r"`{3}.*?`{3}", "", text or "", flags=re.DOTALL)  # no code blocks in the feed
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def activity_items(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Feed items, oldest first: the agent's notes and its actions (no commands or file
    contents). Questions are recorded separately."""
    out: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda e: e.get("timestamp") or 0):
        kind = event.get("type")
        if kind == "assistant_message":
            msg = event.get("assistant_message") or {}
            if msg.get("question_expectation"):
                continue
            text = _clean(str(msg.get("content") or ""))
            if text:
                out.append({"id": event.get("id"), "at": event.get("timestamp"), "kind": "note", "text": text})
        elif kind == "tool_used":
            tool = event.get("tool_used") or {}
            label = _clean(str((tool.get("message") or {}).get("action") or tool.get("brief") or ""), 140)
            if label:
                out.append({"id": event.get("id"), "at": event.get("timestamp"), "kind": "action", "text": label})
    return out


def current_plan(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plans = [e for e in events if e.get("type") == "plan_update"]
    if not plans:
        return []
    latest = max(plans, key=lambda e: e.get("timestamp") or 0)
    return list((latest.get("plan_update") or {}).get("steps") or [])


def guess_stage(texts: list[str], plan: list[dict[str, Any]], current: str) -> str:
    """The furthest stage the recent activity points to; never moves back."""
    best = _STAGE_INDEX.get(current, 0)
    doing = [s.get("title", "") for s in plan if s.get("status") == "doing"]
    for text in [*texts[-8:], *doing]:
        for key, pattern in _STAGE_WORDS:
            if pattern.search(text or ""):
                best = max(best, _STAGE_INDEX[key])
                break
    return STAGES[best][0]


def progress_percent(stage: str, plan: list[dict[str, Any]], started_at: float, previous: int) -> int:
    """Highest of: the stage floor, plan steps done, elapsed time on a 30-minute curve.
    Capped at 95 until the page is collected; never moves back."""
    _key, _label, floor, ceiling = STAGES[_STAGE_INDEX.get(stage, 0)]
    done = sum(1 for s in plan if s.get("status") == "done")
    by_plan = int(100 * done / len(plan)) if plan else 0
    elapsed = max(0.0, time.time() - started_at)
    by_time = int(95 * (1 - 2 ** (-elapsed / (ELAPSED_CURVE_SECONDS / 2))))
    value = max(floor, min(ceiling, max(by_plan, by_time)), previous)
    return min(95, value)


def stage_label(stage: str) -> str:
    return STAGES[_STAGE_INDEX.get(stage, 0)][1]
