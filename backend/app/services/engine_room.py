"""Engine room — health of agents, data providers and runs.

Counts are database aggregates over the whole window (the summary used to count a
100-row sample). Provider health comes from what the provider actually did, not only
from whether a key is set: a configured key whose calls are failing is shown as
failing, with the provider's own error message.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AGENT_RUNNERS
from app.config import get_settings
from app.deps import agent_feature_enabled
from app.models import AgentJob, ApiUsageLog, ChatSession, Client, PhaseValidation
from app.services.agent_handoff import AGENT_LABELS as PHASE_LABELS
from app.services.agent_handoff import PHASE_ORDER
from app.services.role_skills import AGENT_LABELS, PHASE_AGENTS

# A run still "running" after its agent's time budget (plus this slack) was stopped
# by the pipeline without its job row being closed.
STOPPED_SLACK = timedelta(minutes=2)
RECENT_RUNS = 12


def _phase_for_agent(agent_key: str) -> int | None:
    """Position in the current 13-phase order (agent_handoff.PHASE_ORDER)."""
    if agent_key in PHASE_ORDER:
        return PHASE_ORDER.index(agent_key) + 1
    return None


def _label(agent_key: str | None) -> str:
    if not agent_key:
        return "Not linked to a phase"
    return PHASE_LABELS.get(agent_key) or AGENT_LABELS.get(agent_key) or agent_key.replace("_", " ").title()


def readable_error(detail: str | None) -> str:
    """The provider's own message from an error string, without the JSON wrapping.

    "Error code: 400 - {... 'message': 'Your credit balance is too low ...'}" becomes
    "Your credit balance is too low ...".
    """
    text = str(detail or "").strip()
    if not text:
        return "Unknown error"
    found = re.search(r"""['"]message['"]\s*:\s*['"]([^'"]{3,})['"]""", text)
    if found:
        text = found.group(1)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, plain in _EXCEPTION_NAMES:
        if re.fullmatch(pattern, text, flags=re.IGNORECASE):
            return plain
    return text[:160]


# Bare exception names (what httpx raises with no message) in plain language.
_EXCEPTION_NAMES: tuple[tuple[str, str], ...] = (
    (r"(httpx\.)?(Read|Write|Pool)?Timeout(Exception)?", "Requests timed out"),
    (r"(httpx\.)?ConnectTimeout", "Could not connect in time"),
    (r"(httpx\.)?(ConnectError|RemoteProtocolError)", "Could not connect"),
)

_DECISION_WORDS = {"needs_revision": "needed changes", "reject": "rejected"}


def _decision_summary(decisions: list[str]) -> str:
    """["needs_revision", "needs_revision", "reject"] -> "2 needed changes, 1 rejected"."""
    counts: dict[str, int] = {}
    for d in decisions:
        counts[d] = counts.get(d, 0) + 1
    return ", ".join(
        f"{n} {_DECISION_WORDS.get(d, d.replace('_', ' '))}" for d, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )


def _timeout_for(agent_key: str) -> timedelta:
    from app.orchestration.pipeline import agent_timeout_seconds

    return timedelta(seconds=agent_timeout_seconds(agent_key))


def _run_state(job: AgentJob, now: datetime) -> str:
    """succeeded | failed | running | stopped (still 'running' long past its budget)."""
    status = str(job.status or "")
    if status in ("failed", "error"):
        return "failed"
    if status in ("succeeded", "complete", "completed", "success"):
        return "succeeded"
    if status in ("running", "queued"):
        created = job.created_at
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if created is not None and now - created > _timeout_for(job.agent_key) + STOPPED_SLACK:
            return "stopped"
        return "running"
    return status or "unknown"


# Providers whose calls are recorded in api_usage_log; only these have call history.
METERED = {"anthropic", "openrouter", "dataforseo", "ahrefs"}
# A provider is failing when its latest calls are failing, not when one call did.
FAILING_LAST = 3
FAILING_WINDOW = 10


# Providers the app uses, with how to tell whether each is configured.
def _provider_catalog() -> list[dict[str, Any]]:
    s = get_settings()
    rows = [
        ("anthropic", "Claude", bool(s.anthropic_api_key), s.use_mock_llm),
        ("openrouter", "OpenRouter", bool(s.openrouter_api_key), s.use_mock_llm),
        ("dataforseo", "DataForSEO", bool(s.dataforseo_login and s.dataforseo_password), s.use_mock_providers),
        ("ahrefs", "Ahrefs", bool(s.ahrefs_api_key), s.use_mock_providers),
        ("firecrawl", "Firecrawl", bool(getattr(s, "firecrawl_api_key", "")), s.use_mock_providers),
        ("brandfetch", "Brandfetch", bool(getattr(s, "brandfetch_api_key", "")), s.use_mock_providers),
        ("redis_cache", "Redis cache", bool(s.redis_url), False),
    ]
    # Only shown when a key is actually set; neither is part of the current pipeline.
    if s.semrush_api_key:
        rows.append(("semrush", "SEMrush", True, s.use_mock_providers))
    if getattr(s, "context_dev_api_key", ""):
        rows.append(("context_dev", "Context.dev", True, s.use_mock_providers))
    return [
        {"provider": p, "name": n, "configured": c, "mock": m} for p, n, c, m in rows
    ]


async def _provider_health(db: AsyncSession, since: datetime) -> list[dict[str, Any]]:
    """Each provider's calls, failures, last success and last error in the window."""
    stats = {
        p: (int(n), int(failed or 0), last_ok)
        for p, n, failed, last_ok in (
            await db.execute(
                select(
                    ApiUsageLog.provider,
                    func.count(),
                    func.sum(case((ApiUsageLog.status != "success", 1), else_=0)),
                    func.max(case((ApiUsageLog.status == "success", ApiUsageLog.created_at), else_=None)),
                )
                .where(ApiUsageLog.created_at >= since)
                .group_by(ApiUsageLog.provider)
            )
        ).all()
    }
    out: list[dict[str, Any]] = []
    for row in _provider_catalog():
        calls, failed, last_ok = stats.get(row["provider"], (0, 0, None))
        last_error = None
        if failed:
            err = (
                await db.execute(
                    select(ApiUsageLog.error_detail, ApiUsageLog.created_at)
                    .where(
                        ApiUsageLog.provider == row["provider"],
                        ApiUsageLog.status != "success",
                        ApiUsageLog.created_at >= since,
                    )
                    .order_by(ApiUsageLog.created_at.desc())
                    .limit(1)
                )
            ).first()
            if err is not None:
                last_error = {"message": readable_error(err[0]), "at": err[1]}
        recent: list[str] = []
        if calls and row["provider"] in METERED:
            recent = list(
                (
                    await db.execute(
                        select(ApiUsageLog.status)
                        .where(ApiUsageLog.provider == row["provider"], ApiUsageLog.created_at >= since)
                        .order_by(ApiUsageLog.created_at.desc())
                        .limit(FAILING_WINDOW)
                    )
                ).scalars()
            )
        recent_failed = [s != "success" for s in recent]
        if row["mock"]:
            state = "test_mode"
        elif not row["configured"]:
            state = "no_key"
        elif row["provider"] not in METERED:
            state = "set_up"  # its calls are not recorded, so only the key can be checked
        elif (
            len(recent_failed) >= FAILING_LAST
            and all(recent_failed[:FAILING_LAST])
            and sum(recent_failed) * 2 >= len(recent_failed)
        ):
            state = "failing"
        elif failed:
            state = "intermittent"
        elif calls:
            state = "working"
        else:
            state = "idle"
        out.append(
            {
                **row,
                "state": state,
                "calls": calls,
                "failed": failed,
                "last_success_at": last_ok.isoformat() if last_ok else None,
                "last_error": (
                    {"message": last_error["message"], "at": last_error["at"].isoformat()}
                    if last_error
                    else None
                ),
            }
        )
    return out


async def build_engine_room(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    settings = get_settings()
    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    # --- Agent registry -------------------------------------------------------
    agents: list[dict[str, Any]] = []
    for key in PHASE_AGENTS:
        if key == "readiness_gate":
            continue
        enabled = agent_feature_enabled(key)
        registered = key in AGENT_RUNNERS
        agents.append(
            {
                "agent_key": key,
                "label": _label(key),
                "phase": _phase_for_agent(key),
                "registered": registered,
                "feature_enabled": enabled,
                "status": "ok" if registered and enabled else "disabled",
            }
        )
    agents.sort(key=lambda a: (a["phase"] or 99, a["label"]))

    # --- Runs -----------------------------------------------------------------
    job_rows = (
        await db.execute(
            select(AgentJob, Client.display_name)
            .outerjoin(ChatSession, ChatSession.id == AgentJob.session_id)
            .outerjoin(Client, Client.id == ChatSession.client_id)
            .where(AgentJob.created_at >= since)
            .order_by(AgentJob.created_at.desc())
        )
    ).all()
    runs: list[dict[str, Any]] = []
    for job, client_name in job_rows:
        state = _run_state(job, now)
        seconds = None
        if job.completed_at and job.created_at:
            seconds = round((job.completed_at - job.created_at).total_seconds())
        runs.append(
            {
                "id": str(job.id),
                "agent_key": job.agent_key,
                "label": _label(job.agent_key),
                "client_name": client_name,
                "job_type": job.job_type,
                "status": job.status,
                "state": state,
                "seconds": seconds,
                "error": readable_error(job.error_detail) if job.error_detail else None,
                "created_at": job.created_at.isoformat() if job.created_at else None,
            }
        )
    recent_jobs = [
        {
            "id": r["id"],
            "agent_key": r["agent_key"],
            "job_type": r["job_type"],
            "status": r["status"],
            "error_detail": r["error"],
            "created_at": r["created_at"],
        }
        for r in runs[:40]
    ]

    # Per-phase health in pipeline order.
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for r in runs:
        by_agent.setdefault(r["agent_key"], []).append(r)
    phases: list[dict[str, Any]] = []
    for a in agents:
        rs = by_agent.get(a["agent_key"], [])
        done = [r["seconds"] for r in rs if r["state"] == "succeeded" and r["seconds"] is not None]
        last = rs[0] if rs else None
        phases.append(
            {
                "agent_key": a["agent_key"],
                "label": a["label"],
                "phase": a["phase"],
                "enabled": a["status"] == "ok",
                "runs": len(rs),
                "failed": sum(1 for r in rs if r["state"] in ("failed", "stopped")),
                "typical_seconds": round(sorted(done)[len(done) // 2]) if done else None,
                "last_state": last["state"] if last else None,
                "last_run_at": last["created_at"] if last else None,
                "last_client": last["client_name"] if last else None,
            }
        )

    # --- Phase validation -----------------------------------------------------
    val_rows = (
        await db.execute(
            select(PhaseValidation)
            .where(
                PhaseValidation.created_at >= since,
                PhaseValidation.decision.in_(("reject", "needs_revision")),
            )
            .order_by(PhaseValidation.created_at.desc())
            .limit(25)
        )
    ).scalars().all()
    validation_issues = [
        {
            "id": str(v.id),
            "agent_key": v.agent_key,
            "decision": v.decision,
            "iteration": v.iteration,
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in val_rows
    ]

    # --- API usage ------------------------------------------------------------
    calls_total, calls_failed, cost_total = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((ApiUsageLog.status != "success", 1), else_=0)),
                func.coalesce(
                    func.sum(case((ApiUsageLog.status == "success", ApiUsageLog.estimated_cost_usd), else_=0)),
                    0,
                ),
            ).where(ApiUsageLog.created_at >= since)
        )
    ).one()
    error_groups = (
        await db.execute(
            select(
                ApiUsageLog.provider,
                ApiUsageLog.error_detail,
                ApiUsageLog.agent_key,
                func.count(),
                func.max(ApiUsageLog.created_at),
            )
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status != "success")
            .group_by(ApiUsageLog.provider, ApiUsageLog.error_detail, ApiUsageLog.agent_key)
        )
    ).all()
    recent_rows = (
        await db.execute(
            select(ApiUsageLog)
            .where(ApiUsageLog.created_at >= since)
            .order_by(ApiUsageLog.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    api_errors = [
        {
            "id": str(u.id),
            "provider": u.provider,
            "operation": u.operation,
            "agent_key": u.agent_key,
            "status": u.status,
            "error_detail": u.error_detail,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in recent_rows
        if u.status != "success"
    ]
    recent_calls = [
        {
            "id": str(u.id),
            "provider": u.provider,
            "operation": u.operation,
            "model": u.model,
            "agent_key": u.agent_key,
            "client_id": str(u.client_id) if u.client_id else None,
            "prompt_tokens": u.prompt_tokens,
            "completion_tokens": u.completion_tokens,
            "estimated_cost_usd": float(u.estimated_cost_usd or 0),
            "latency_ms": u.latency_ms,
            "status": u.status,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in recent_rows
    ]
    cost_by_provider = (
        await db.execute(
            select(ApiUsageLog.provider, func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0), func.count())
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success")
            .group_by(ApiUsageLog.provider)
        )
    ).all()
    cost_by_agent = (
        await db.execute(
            select(ApiUsageLog.agent_key, func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0), func.count())
            .where(
                ApiUsageLog.created_at >= since,
                ApiUsageLog.status == "success",
                ApiUsageLog.agent_key.isnot(None),
            )
            .group_by(ApiUsageLog.agent_key)
        )
    ).all()

    providers = await _provider_health(db, since)
    provider_names = {p["provider"]: p["name"] for p in providers}

    # --- Problems: grouped, most serious first ----------------------------------
    problems: list[dict[str, Any]] = []
    for p in providers:
        if p["state"] == "failing":
            problems.append(
                {
                    "severity": "error",
                    "kind": "provider",
                    "title": f"{p['name']} calls are failing",
                    "detail": p["last_error"]["message"] if p["last_error"] else "",
                    "count": p["failed"],
                    "last_at": p["last_error"]["at"] if p["last_error"] else None,
                }
            )
        elif p["state"] == "no_key" and p["provider"] in ("anthropic", "dataforseo"):
            problems.append(
                {
                    "severity": "error",
                    "kind": "provider",
                    "title": f"{p['name']} has no API key",
                    "detail": "The pipeline needs this provider. Add the key to the environment.",
                    "count": 1,
                    "last_at": None,
                }
            )
    stopped: dict[str, list[dict[str, Any]]] = {}
    failed_runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in runs:
        if r["state"] == "stopped":
            stopped.setdefault(r["agent_key"], []).append(r)
        elif r["state"] == "failed":
            failed_runs.setdefault((r["agent_key"], r["error"] or "Failed without an error message"), []).append(r)
    for agent_key, rs in stopped.items():
        problems.append(
            {
                "severity": "error",
                "kind": "run",
                "title": f"{_label(agent_key)} runs stopped before finishing",
                "detail": (
                    "Still marked running long after the "
                    f"{int(_timeout_for(agent_key).total_seconds() // 60)}-minute limit, so the "
                    "run was cut off and nothing was saved."
                ),
                "count": len(rs),
                "last_at": rs[0]["created_at"],
                "clients": sorted({r["client_name"] for r in rs if r["client_name"]}),
            }
        )
    for (agent_key, error), rs in failed_runs.items():
        problems.append(
            {
                "severity": "error",
                "kind": "run",
                "title": f"{_label(agent_key)} runs failed",
                "detail": error,
                "count": len(rs),
                "last_at": rs[0]["created_at"],
                "clients": sorted({r["client_name"] for r in rs if r["client_name"]}),
            }
        )
    grouped_errors: dict[tuple[str, str], dict[str, Any]] = {}
    for provider, detail, agent_key, n, last in error_groups:
        message = readable_error(detail)
        bucket = grouped_errors.setdefault(
            (provider, message),
            {"count": 0, "last": last, "agents": set()},
        )
        bucket["count"] += int(n)
        bucket["last"] = max(bucket["last"], last) if bucket["last"] and last else (bucket["last"] or last)
        if agent_key:
            bucket["agents"].add(_label(agent_key))
    failing_now = {p["provider"] for p in providers if p["state"] == "failing"}
    for (provider, message), b in sorted(grouped_errors.items(), key=lambda kv: -kv[1]["count"]):
        # The failing-provider problem above already carries its latest error.
        if provider in failing_now and b["count"] and message == next(
            (p["last_error"]["message"] for p in providers if p["provider"] == provider and p["last_error"]),
            None,
        ):
            continue
        problems.append(
            {
                "severity": "warning",
                "kind": "api",
                "title": f"{provider_names.get(provider, provider)}: {message}",
                "detail": ("During " + ", ".join(sorted(b["agents"]))) if b["agents"] else "",
                "count": b["count"],
                "last_at": b["last"].isoformat() if b["last"] else None,
            }
        )
    val_by_agent: dict[str, list[dict[str, Any]]] = {}
    for v in validation_issues:
        val_by_agent.setdefault(v["agent_key"], []).append(v)
    for agent_key, vs in val_by_agent.items():
        problems.append(
            {
                "severity": "warning",
                "kind": "validation",
                "title": f"{_label(agent_key)} output was sent back by quality checks",
                "detail": _decision_summary([str(v["decision"]) for v in vs]),
                "count": len(vs),
                "last_at": vs[0]["created_at"],
            }
        )
    for a in agents:
        if a["status"] == "disabled":
            problems.append(
                {
                    "severity": "info",
                    "kind": "agent",
                    "title": f"{a['label']} is turned off",
                    "detail": "Disabled by a feature flag or not registered.",
                    "count": 1,
                    "last_at": None,
                }
            )
    rank = {"error": 0, "warning": 1, "info": 2}
    problems.sort(key=lambda p: (rank.get(p["severity"], 3), -(p["count"] or 0)))

    # Legacy flat issue list, kept for existing consumers.
    issues = [
        {
            "severity": p["severity"],
            "source": p["kind"],
            "agent_key": None,
            "message": f"{p['title']}" + (f" — {p['detail']}" if p["detail"] else ""),
            "at": p["last_at"],
        }
        for p in problems
    ]

    client_count = (await db.execute(select(func.count()).select_from(Client))).scalar()

    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "summary": {
            "agents_total": len(agents),
            "agents_ok": sum(1 for a in agents if a["status"] == "ok"),
            "open_issues": sum(1 for p in problems if p["severity"] in ("error", "warning")),
            "api_calls": int(calls_total or 0),
            "failed_calls": int(calls_failed or 0),
            "estimated_cost_usd": float(cost_total or 0),
            "clients": int(client_count or 0),
            "runs": len(runs),
            "failed_runs": sum(1 for r in runs if r["state"] in ("failed", "stopped")),
            "running_runs": sum(1 for r in runs if r["state"] == "running"),
        },
        "problems": problems,
        "providers": providers,
        "phases": phases,
        "recent_runs": runs[:RECENT_RUNS],
        "agents": agents,
        "integrations": [
            {"provider": p["provider"], "configured": p["configured"], "mock": p["mock"]}
            for p in providers
        ],
        "recent_jobs": recent_jobs,
        "validation_issues": validation_issues,
        "api_errors": api_errors,
        "recent_api_calls": recent_calls,
        "cost_by_provider": [
            {"provider": p, "cost_usd": float(c or 0), "calls": int(n)} for p, c, n in cost_by_provider
        ],
        "cost_by_agent": [
            {"agent_key": k or "unattributed", "cost_usd": float(c or 0), "calls": int(n)}
            for k, c, n in cost_by_agent
        ],
        "issues": issues[:40],
        "feature_flags": {
            "use_mock_llm": settings.use_mock_llm,
            "use_mock_providers": settings.use_mock_providers,
            "auth_disabled": settings.auth_disabled,
        },
    }
