"""Project memory and system memory for QA.

Project memory is one file per client: the log of why that client's phase did not
pass the QA gate. System memory is one file for the whole app: the same failure
seen again, with the client name left out, so the next run of any client can
avoid it.

Agents act on the memory by receiving it at the start of the phase message.
A write failure never fails the phase.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger
from app.services.phase_validation.schema import CheckStatus, ValidationResult

log = get_logger("agent_memory")

_LOCK = threading.Lock()
_PROJECT_CAP = 80
_SYSTEM_CAP = 120
_PREAMBLE_CAP = 6

_RECORDED_DECISIONS = {"needs_revision", "reject", "pass_with_warnings"}


def memory_root() -> Path:
    configured = (get_settings().agent_memory_dir or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "data" / "agent_memory"


def system_memory_path() -> Path:
    return memory_root() / "system_memory.json"


def project_memory_path(client_id: str) -> Path:
    safe = "".join(ch for ch in str(client_id) if ch.isalnum() or ch in "-_")
    return memory_root() / "projects" / f"{safe or 'unknown'}.json"


def record_qa_result(
    *,
    client_id: str,
    result: ValidationResult,
) -> None:
    """Append a QA miss to this client's file and fold it into system memory."""
    if result.decision not in _RECORDED_DECISIONS:
        return
    findings = _findings(result)
    if not findings:
        return
    try:
        with _LOCK:
            _append_project(str(client_id), result, findings)
            _fold_system(result, findings)
    except Exception as exc:  # noqa: BLE001 — memory must not fail the phase
        log.warning("agent_memory_write_failed", error=f"{type(exc).__name__}: {exc}"[:200])


def lessons_for_agent(*, client_id: str, agent_key: str) -> list[dict[str, Any]]:
    """Prior QA misses this agent should act on. Project lessons first, then system."""
    try:
        project = _read(project_memory_path(client_id))
        system = _read(system_memory_path())
    except Exception as exc:  # noqa: BLE001
        log.warning("agent_memory_read_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        return []
    out: list[dict[str, Any]] = []
    for entry in reversed(project.get("entries") or []):
        if entry.get("agent_key") != agent_key:
            continue
        for finding in entry.get("findings") or []:
            out.append({**finding, "scope": "project"})
            if len(out) >= _PREAMBLE_CAP:
                return out
    system_rows = [
        row
        for row in (system.get("lessons") or [])
        if row.get("agent_key") == agent_key
    ]
    system_rows.sort(key=lambda row: (-int(row.get("seen") or 0), str(row.get("last_seen") or "")))
    seen = {(row.get("check_id"), row.get("reason")) for row in out}
    for row in system_rows:
        key = (row.get("check_id"), row.get("reason"))
        if key in seen:
            continue
        out.append({**row, "scope": "system"})
        if len(out) >= _PREAMBLE_CAP:
            break
    return out


def with_memory(message: str, *, client_id: str, agent_key: str) -> str:
    """The phase message plus the QA lessons this agent must not repeat."""
    lessons = lessons_for_agent(client_id=client_id, agent_key=agent_key)
    if not lessons:
        return message
    lines = [
        (message or "").strip(),
        "",
        "--- MEMORY (act on this; do not repeat these QA misses) ---",
        "Fix the cause in place. Do not drop valid work to make a check pass.",
        "The next output must be at least as complete as the last one.",
    ]
    for lesson in lessons:
        where = "This client" if lesson.get("scope") == "project" else "Seen on other runs"
        seen = int(lesson.get("seen") or 0)
        times = f", {seen} times" if seen > 1 else ""
        layer = lesson.get("layer") or "qa"
        reason = str(lesson.get("reason") or "").strip()
        fix = str(lesson.get("correction") or "").strip()
        lines.append(f"- {where}{times} [{layer}] {reason}")
        if fix:
            lines.append(f"  Fix: {fix}")
    lines.append("--- END MEMORY ---")
    return "\n".join(line for line in lines if line is not None)


def _findings(result: ValidationResult) -> list[dict[str, str]]:
    wanted = {CheckStatus.FAILED.value, CheckStatus.WARNING.value}
    rows: list[dict[str, str]] = []
    for check in result.checks:
        if check.status not in wanted:
            continue
        rows.append(
            {
                "layer": check.layer or "",
                "check_id": check.check_id,
                "severity": check.severity,
                "reason": (check.message or "")[:400],
                "evidence": (check.evidence or "")[:240],
                "correction": (check.recommended_correction or "")[:400],
            }
        )
    return rows[:12]


def _append_project(client_id: str, result: ValidationResult, findings: list[dict[str, str]]) -> None:
    path = project_memory_path(client_id)
    data = _read(path)
    entries = list(data.get("entries") or [])
    entries.append(
        {
            "at": _now(),
            "agent_key": result.agent_key,
            "phase_label": result.phase_label,
            "iteration": result.iteration,
            "decision": result.decision,
            "summary": (result.summary or "")[:500],
            "findings": findings,
        }
    )
    data = {
        "kind": "project_memory",
        "client_id": client_id,
        "entries": entries[-_PROJECT_CAP:],
    }
    _write(path, data)
    _write_markdown(path.with_suffix(".md"), _project_markdown(data))


def _fold_system(result: ValidationResult, findings: list[dict[str, str]]) -> None:
    path = system_memory_path()
    data = _read(path)
    lessons = list(data.get("lessons") or [])
    index = {
        (row.get("agent_key"), row.get("check_id"), row.get("reason")): row
        for row in lessons
        if isinstance(row, dict)
    }
    now = _now()
    for finding in findings:
        # Evidence can name the client. System memory keeps the reason only.
        key = (result.agent_key, finding.get("check_id"), finding.get("reason"))
        existing = index.get(key)
        if existing is None:
            existing = {
                "agent_key": result.agent_key,
                "phase_label": result.phase_label,
                "layer": finding.get("layer") or "",
                "check_id": finding.get("check_id"),
                "reason": finding.get("reason"),
                "correction": finding.get("correction") or "",
                "decision": result.decision,
                "seen": 0,
                "last_seen": now,
            }
            lessons.append(existing)
            index[key] = existing
        existing["seen"] = int(existing.get("seen") or 0) + 1
        existing["last_seen"] = now
        existing["decision"] = result.decision
        if finding.get("correction"):
            existing["correction"] = finding["correction"]
    lessons.sort(key=lambda row: (-int(row.get("seen") or 0), str(row.get("last_seen") or "")))
    data = {"kind": "system_memory", "lessons": lessons[:_SYSTEM_CAP]}
    _write(path, data)
    _write_markdown(path.with_suffix(".md"), _system_markdown(data))


def _project_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Project memory",
        "",
        f"Client `{data.get('client_id')}`. Reasons a phase did not pass QA.",
        "",
    ]
    for entry in data.get("entries") or []:
        lines.append(
            f"## {entry.get('at')} — {entry.get('phase_label')} ({entry.get('decision')})"
        )
        lines.append("")
        lines.append(str(entry.get("summary") or "").strip())
        lines.append("")
        for finding in entry.get("findings") or []:
            lines.append(
                f"- [{finding.get('layer') or 'qa'}] {finding.get('check_id')}: {finding.get('reason')}"
            )
            if finding.get("correction"):
                lines.append(f"  - Fix: {finding['correction']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _system_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# System memory",
        "",
        "QA misses seen across clients. No client name is stored here.",
        "",
    ]
    for lesson in data.get("lessons") or []:
        lines.append(
            f"- ({lesson.get('seen')}×) {lesson.get('phase_label')} "
            f"[{lesson.get('layer') or 'qa'}] {lesson.get('reason')}"
        )
        if lesson.get("correction"):
            lines.append(f"  - Fix: {lesson['correction']}")
    lines.append("")
    return "\n".join(lines)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
