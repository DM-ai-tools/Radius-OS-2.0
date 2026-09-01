"""Engine room — unified ops view of agents, integrations, jobs, and costs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AGENT_RUNNERS
from app.config import get_settings
from app.deps import agent_feature_enabled
from app.models import AgentJob, ApiUsageLog, Client, PhaseValidation
from app.services.role_skills import AGENT_LABELS, PHASE_AGENTS, PHASE_SKILL_COVERAGE


def _phase_for_agent(agent_key: str) -> int | None:
    for row in PHASE_SKILL_COVERAGE:
        skills = row.get("skills") or []
        if agent_key in skills:
            return int(row.get("phase") or 0) or None
    mapping = {
        "discovery_agent": 1,
        "tracking_access_agent": 2,
        "website_situation_agent": 3,
        "competitor_market_agent": 4,
        "readiness_gate": 4,
        "search_demand": 5,
        "content_strategy": 6,
        "site_architecture": 6,
        "technical_seo": 7,
        "content_audit": 8,
        "content_planning": 9,
        "content_production": 10,
        "on_page_seo": 11,
        "publishing": 12,
    }
    return mapping.get(agent_key)


async def build_engine_room(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    settings = get_settings()
    since = datetime.now(UTC) - timedelta(days=days)

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
                "label": AGENT_LABELS.get(key, key),
                "phase": _phase_for_agent(key),
                "registered": registered,
                "feature_enabled": enabled,
                "status": "ok" if registered and enabled else "disabled",
            }
        )

    # --- Recent jobs (failures surface as issues) -----------------------------
    job_rows = (
        await db.execute(
            select(AgentJob)
            .where(AgentJob.created_at >= since)
            .order_by(AgentJob.created_at.desc())
            .limit(40)
        )
    ).scalars().all()
    recent_jobs = [
        {
            "id": str(j.id),
            "agent_key": j.agent_key,
            "job_type": j.job_type,
            "status": j.status,
            "provider_used": j.provider_used,
            "error_detail": j.error_detail,
            "attempt_count": j.attempt_count,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in job_rows
    ]

    # --- Phase validation failures --------------------------------------------
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

    # --- API usage / cost -----------------------------------------------------
    usage_rows = (
        await db.execute(
            select(ApiUsageLog)
            .where(ApiUsageLog.created_at >= since)
            .order_by(ApiUsageLog.created_at.desc())
            .limit(100)
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
        for u in usage_rows
        if u.status != "success"
    ]

    cost_total = (
        await db.execute(
            select(func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0)).where(
                ApiUsageLog.created_at >= since,
                ApiUsageLog.status == "success",
            )
        )
    ).scalar()
    cost_by_provider = (
        await db.execute(
            select(
                ApiUsageLog.provider,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            )
            .where(ApiUsageLog.created_at >= since, ApiUsageLog.status == "success")
            .group_by(ApiUsageLog.provider)
        )
    ).all()
    cost_by_agent = (
        await db.execute(
            select(
                ApiUsageLog.agent_key,
                func.coalesce(func.sum(ApiUsageLog.estimated_cost_usd), 0),
                func.count(),
            )
            .where(
                ApiUsageLog.created_at >= since,
                ApiUsageLog.status == "success",
                ApiUsageLog.agent_key.isnot(None),
            )
            .group_by(ApiUsageLog.agent_key)
        )
    ).all()

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
        for u in usage_rows[:50]
    ]

    # --- Integration health ---------------------------------------------------
    integrations = [
        {
            "provider": "openrouter",
            "configured": bool(settings.openrouter_api_key),
            "mock": settings.use_mock_llm,
        },
        {
            "provider": "anthropic",
            "configured": bool(settings.anthropic_api_key),
            "mock": settings.use_mock_llm,
        },
        {
            "provider": "dataforseo",
            "configured": bool(settings.dataforseo_login and settings.dataforseo_password),
            "mock": settings.use_mock_providers,
        },
        {
            "provider": "ahrefs",
            "configured": bool(settings.ahrefs_api_key),
            "mock": settings.use_mock_providers,
        },
        {
            "provider": "redis_cache",
            "configured": bool(settings.redis_url),
            "mock": False,
        },
    ]

    # --- Issues rollup --------------------------------------------------------
    issues: list[dict[str, Any]] = []
    for j in recent_jobs:
        if j["status"] in ("failed", "error"):
            issues.append(
                {
                    "severity": "error",
                    "source": "agent_job",
                    "agent_key": j["agent_key"],
                    "message": j["error_detail"] or f"Job {j['job_type']} failed",
                    "at": j["created_at"],
                }
            )
    for v in validation_issues:
        issues.append(
            {
                "severity": "warning",
                "source": "phase_validation",
                "agent_key": v["agent_key"],
                "message": f"Validation {v['decision']} (iteration {v['iteration']})",
                "at": v["created_at"],
            }
        )
    for e in api_errors[:15]:
        issues.append(
            {
                "severity": "error",
                "source": "api_call",
                "agent_key": e.get("agent_key"),
                "message": f"{e['provider']}:{e['operation']} — {e.get('error_detail') or e['status']}",
                "at": e["created_at"],
            }
        )
    for a in agents:
        if a["status"] == "disabled":
            issues.append(
                {
                    "severity": "info",
                    "source": "agent_registry",
                    "agent_key": a["agent_key"],
                    "message": f"{a['label']} is disabled or not registered",
                    "at": None,
                }
            )

    client_count = (await db.execute(select(func.count()).select_from(Client))).scalar()

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "window_days": days,
        "summary": {
            "agents_total": len(agents),
            "agents_ok": sum(1 for a in agents if a["status"] == "ok"),
            "open_issues": len([i for i in issues if i["severity"] in ("error", "warning")]),
            "api_calls": len(usage_rows),
            "estimated_cost_usd": float(cost_total or 0),
            "clients": int(client_count or 0),
        },
        "agents": agents,
        "integrations": integrations,
        "recent_jobs": recent_jobs,
        "validation_issues": validation_issues,
        "api_errors": api_errors,
        "recent_api_calls": recent_calls,
        "cost_by_provider": [
            {"provider": p, "cost_usd": float(c or 0), "calls": int(n)}
            for p, c, n in cost_by_provider
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
