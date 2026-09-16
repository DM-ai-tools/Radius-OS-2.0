"""Adversarial validation harness — run phases 1..12 and capture everything.

Usage (from backend/):
  python scripts/validation_harness.py --fixture <slug> --url <url> \
      --name "<Business Name>" --keywords "kw1, kw2" [--industry X] \
      [--audience "..."] [--geo "..."] [--from 1] [--to 12]

Writes to ../validation/runs/<slug>/:
  phase<NN>_<agent>.json   per-phase: events, summary, status transitions,
                           wall time, provider calls + cost delta, findings rows
  providers.jsonl          every outbound HTTP request/response (raw)
  run.json                 run-level manifest: timings, costs, stall point

Design notes
------------
* One shot per fixture. The harness never re-runs a phase; if a phase fails it
  records the failure and continues so later boundaries are still observed.
* Raw provider capture patches httpx.AsyncClient.send, which is below every
  integration in this codebase, so nothing has to opt in.
* Phase 12 is forced to PREVIEW: the harness asserts
  wordpress_allow_live_publish is False and passes a message that
  agents/publishing.py resolve_mode() maps to preview.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.agents import AGENT_RUNNERS
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import ChatSession, Client, ClientDigitalProfile, User
from app.orchestration.pipeline import AGENT_TIMEOUT_SECONDS
from app.services.review import approve_phase_batch

OUT_ROOT = ROOT.parent / "validation" / "runs"
HOD_EMAIL = "hod@trafficradius.com"

# (phase number, agent key, status attribute, chat message)
PHASES: list[tuple[int, str, str, str]] = [
    (1, "discovery_agent", "discovery_status", "run discovery"),
    (2, "tracking_access_agent", "tracking_status", "run tracking access audit"),
    (3, "website_situation_agent", "website_status", "run website situation"),
    (4, "competitor_market_agent", "competitor_status", "run competitor analysis"),
    (5, "search_demand", "search_demand_status", "run keyword research"),
    (6, "content_strategy", "seo_strategy_status", "run content strategy"),
    (6, "site_architecture", "site_architecture_status", "run site architecture"),
    (7, "technical_seo", "technical_seo_status", "run technical seo audit"),
    (8, "content_audit", "content_audit_status", "run content audit"),
    (9, "content_planning", "content_planning_status", "run content planning"),
    (10, "content_production", "content_production_status", "run content production"),
    (11, "on_page_seo", "on_page_seo_status", "run on-page seo"),
    # Deliberately NOT a write request — resolve_mode() maps this to preview.
    (12, "publishing", "publishing_status", "run publishing checklist"),
]

SUMMARY_ATTR = {
    "discovery_agent": ("commercial_scope", "marketing_context"),
    "tracking_access_agent": ("tracking_summary",),
    "website_situation_agent": ("website_situation_summary",),
    "competitor_market_agent": ("competitive_landscape_summary",),
    "search_demand": ("search_demand_summary",),
    "content_strategy": ("seo_strategy_summary",),
    "site_architecture": ("site_architecture_summary",),
    "technical_seo": ("technical_seo_summary",),
    "content_audit": ("content_audit_summary",),
    "content_planning": ("content_planning_summary",),
    "content_production": ("content_production_summary",),
    "on_page_seo": ("on_page_seo_summary",),
    "publishing": ("publishing_summary",),
}

# --- raw provider capture ---------------------------------------------------

_capture_path: Path | None = None
_hosts_path: Path | None = None
_current_phase: str = "setup"
_orig_send = httpx.AsyncClient.send
_REDACT = ("authorization", "x-api-key", "cookie", "set-cookie", "proxy-authorization")
TAB = chr(9)      # hosts.txt is tab-separated
NEWLINE = chr(10)


def _install_capture(path: Path, hosts_path: Path) -> None:
    global _capture_path, _hosts_path
    _capture_path = path
    _hosts_path = hosts_path
    path.write_text("", encoding="utf-8")
    hosts_path.write_text('# phase\tmethod\thost\tstatus\turl\n', encoding="utf-8")

    async def send(self, request, **kwargs):
        started = time.monotonic()
        entry: dict = {
            "ts": time.time(),
            "phase": _current_phase,
            "method": request.method,
            "url": str(request.url),
        }
        try:
            resp = await _orig_send(self, request, **kwargs)
        except Exception as exc:  # noqa: BLE001
            entry |= {"error": f"{type(exc).__name__}: {exc}"[:300],
                      "ms": round((time.monotonic() - started) * 1000)}
            _write(entry)
            _write_host(entry)
            raise
        body = ""
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            body = "<unreadable>"
        entry |= {
            "status": resp.status_code,
            "ms": round((time.monotonic() - started) * 1000),
            "resp_headers": {k: v for k, v in resp.headers.items()
                             if k.lower() not in _REDACT},
            # Full body up to 200k so accuracy checks can re-read real payloads.
            "body": body[:200_000],
            "body_truncated": len(body) > 200_000,
        }
        _write(entry)
        _write_host(entry)
        return resp

    httpx.AsyncClient.send = send


def _write_host(entry: dict) -> None:
    """One line per outbound request. This is how we prove what we did and did
    not touch, and how any other host-leaking path gets caught."""
    if _hosts_path is None:
        return
    host = urlparse(str(entry.get("url") or "")).hostname or "?"
    with _hosts_path.open("a", encoding="utf-8") as fh:
        row = TAB.join([
            str(entry.get("phase")), str(entry.get("method")), host,
            str(entry.get("status", entry.get("error", "?"))), str(entry.get("url")),
        ])
        fh.write(row + NEWLINE)


def _write(entry: dict) -> None:
    if _capture_path is None:
        return
    with _capture_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")


# --- helpers ----------------------------------------------------------------


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


async def _cost_snapshot(db) -> tuple[int, float]:
    row = (await db.execute(text(
        "select count(*), coalesce(sum(estimated_cost_usd),0) from api_usage_log"
    ))).first()
    return int(row[0] or 0), float(row[1] or 0)


async def _findings(db, client_id: UUID, agent_key: str) -> list[dict]:
    rows = (await db.execute(text(
        "select agent_key, confidence, status, created_at from findings_ledger "
        "where client_id = :c and agent_key = :a order by created_at"
    ), {"c": str(client_id), "a": agent_key})).all()
    return [{"agent_key": r[0], "confidence": r[1], "status": r[2], "at": str(r[3])} for r in rows]


async def _submit_questionnaire(db, client, args) -> str:
    """Simulate the human questionnaire submission that Phase 1 waits on.

    Phase 1 is deliberately interactive: `run_discovery` emits a
    `discovery_report` card with actions ['submit_questionnaire', 'upload_cdd']
    and parks at `in_progress`. Without this step every fixture stops at Phase 1
    and no later boundary is ever observed. Mirrors
    `api/findings.py::submit_questionnaire` — the same DiscoveryResponse rows,
    then the same `build_discovery_signoff_events` call.
    """
    from app.agents.discovery import build_discovery_signoff_events
    from app.models import DiscoveryResponse

    answers = {
        "business_goal": f"Grow qualified organic enquiries for {args.name}.",
        "objectives": "organic growth",
        "strategy_approach": "Organic search led.",
        "lead_modes": "web form, phone",
        "sales_cycle": "30-60 days",
        "seasonality": "none known",
        "average_ticket_size": None,
        "lifetime_value": None,
        "analytics_access": "not granted",
        "search_console_access": "not granted",
    }
    if args.keywords:
        answers["business_keywords"] = args.keywords
    if args.audience:
        answers["target_demographic"] = args.audience
    if args.geo:
        answers["geographic_focus"] = args.geo
    if args.model:
        answers["business_model"] = args.model

    for key, value in answers.items():
        db.add(DiscoveryResponse(
            client_id=client.id, source="client_questionnaire", field_key=key,
            field_value={"value": value}, confidence=None,
            discrepancy_flag=False, status="pending",
        ))
    await db.flush()
    await build_discovery_signoff_events(db, client_id=client.id)
    await db.commit()
    profile = (await db.execute(
        select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
    )).scalar_one()
    return profile.discovery_status


def _timeout_for(agent_key: str) -> int:
    s = get_settings()
    if agent_key == "technical_seo":
        return int(getattr(s, "technical_seo_agent_timeout_seconds", 900) or 900)
    if agent_key in ("content_production", "on_page_seo", "publishing"):
        return int(getattr(s, "content_agent_timeout_seconds", 1200) or 1200)
    return int(AGENT_TIMEOUT_SECONDS)


async def _ensure_fixture(db, slug: str, args) -> tuple[Client, ChatSession, User]:
    hod = (await db.execute(select(User).where(User.email == HOD_EMAIL))).scalar_one_or_none()
    if hod is None:
        raise SystemExit(f"HOD user {HOD_EMAIL} not found — seed the database first")

    existing = (await db.execute(
        select(Client).options(selectinload(Client.profile))
        .where(Client.display_name == args.name)
    )).scalars().first()
    if existing is not None:
        if not args.resume:
            raise SystemExit(
                f"Fixture client '{args.name}' already exists ({existing.id}). "
                "One shot per fixture — pass --resume to continue this run from a later "
                "phase, or delete the client if you really mean to start over."
            )
        chat = (await db.execute(
            select(ChatSession).where(ChatSession.client_id == existing.id)
            .order_by(ChatSession.started_at)
        )).scalars().first()
        if chat is None:
            chat = ChatSession(client_id=existing.id, user_id=hod.id, is_onboarding=True)
            db.add(chat)
            await db.commit()
        print(f"  resuming existing fixture client {existing.id}")
        return existing, chat, hod

    client = Client(
        legal_name=args.name, display_name=args.name,
        primary_url=args.url, industry=args.industry,
        tier="B", status="onboarding", is_onboarding=True,
    )
    db.add(client)
    await db.flush()

    intake = {
        "business_keywords": args.keywords,
        "target_audience": args.audience,
        "geographic_focus": args.geo,
        "business_model": args.model,
        "objectives": ["organic growth"],
    }
    profile = ClientDigitalProfile(
        client_id=client.id, is_onboarding=True,
        marketing_context={"client_intake": {k: v for k, v in intake.items() if v}},
        commercial_scope={"business_keywords": args.keywords} if args.keywords else {},
    )
    db.add(profile)
    chat = ChatSession(client_id=client.id, user_id=hod.id, is_onboarding=True)
    db.add(chat)
    await db.commit()
    await db.refresh(client)
    return client, chat, hod


# --- main -------------------------------------------------------------------


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--fixture", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--keywords", default="")
    p.add_argument("--industry", default=None)
    p.add_argument("--audience", default=None)
    p.add_argument("--geo", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--from", dest="start", type=int, default=1)
    p.add_argument("--to", dest="end", type=int, default=12)
    p.add_argument("--resume", action="store_true",
                   help="continue an existing fixture client instead of creating one")
    p.add_argument("--budget", type=float, default=8.64,
                   help="abort the run if this run's cost exceeds this (3x median)")
    args = p.parse_args()

    settings = get_settings()
    if settings.wordpress_allow_live_publish:
        raise SystemExit("REFUSING: WORDPRESS_ALLOW_LIVE_PUBLISH is true")

    out = OUT_ROOT / args.fixture
    out.mkdir(parents=True, exist_ok=True)
    _install_capture(out / "providers.jsonl", out / "hosts.txt")

    manifest: dict = {
        "fixture": args.fixture, "url": args.url, "name": args.name,
        "started": time.time(), "phases": [], "aborted": None,
        "env": {"mock_providers": settings.use_mock_providers,
                "mock_llm": settings.use_mock_llm,
                "live_publish": settings.wordpress_allow_live_publish},
    }

    async with AsyncSessionLocal() as db:
        client, chat, hod = await _ensure_fixture(db, args.fixture, args)
        print(f"fixture {args.fixture}: client {client.id}  {args.url}")
        base_calls, base_cost = await _cost_snapshot(db)
        run_cost = 0.0

        for num, agent_key, status_attr, message in PHASES:
            if num < args.start or num > args.end:
                continue
            profile = (await db.execute(
                select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
            )).scalar_one()
            before_status = getattr(profile, status_attr, "not_started")
            c0, k0 = await _cost_snapshot(db)
            t0 = time.monotonic()
            events, error = [], None
            global _current_phase
            _current_phase = f"p{num:02d}_{agent_key}"

            try:
                events = await asyncio.wait_for(
                    AGENT_RUNNERS[agent_key](
                        db, client=client, session_id=chat.id,
                        user_id=hod.id, message=message,
                    ),
                    timeout=_timeout_for(agent_key),
                )
                await db.commit()
            except asyncio.TimeoutError:
                error = f"TIMEOUT after {_timeout_for(agent_key)}s"
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
                await db.rollback()

            elapsed = time.monotonic() - t0
            await db.refresh(profile)
            after_run = getattr(profile, status_attr, "not_started")

            # Phases 1 and 2 are human-in-the-loop: they park at in_progress
            # awaiting a REST submission before their sign-off gate appears.
            questionnaire = None
            if after_run == "in_progress" and agent_key in ("discovery_agent", "tracking_access_agent"):
                try:
                    if agent_key == "discovery_agent":
                        questionnaire = await _submit_questionnaire(db, client, args)
                    else:
                        from app.agents.tracking import build_tracking_signoff_events

                        await build_tracking_signoff_events(
                            db, client_id=client.id,
                            known_changes={"known_changes": "none reported"},
                        )
                        await db.commit()
                        questionnaire = "known_changes submitted"
                except Exception as exc:  # noqa: BLE001
                    questionnaire = f"GATE SUBMIT FAILED: {type(exc).__name__}: {exc}"
                    await db.rollback()
                await db.refresh(profile)
                after_run = getattr(profile, status_attr, "not_started")

            approved = None
            if after_run == "pending_signoff":
                try:
                    await approve_phase_batch(
                        db, user=hod, client_id=client.id, agent_key=agent_key,
                        action="approve", note="validation harness auto-approve",
                    )
                    await db.commit()
                    await db.refresh(profile)
                    approved = True
                except Exception as exc:  # noqa: BLE001
                    approved = f"APPROVE FAILED: {exc}"
                    await db.rollback()
            after_approve = getattr(profile, status_attr, "not_started")

            c1, k1 = await _cost_snapshot(db)
            run_cost += (k1 - k0)

            # Guard the CLIENT RESOLUTION path only. Phases 1-2 must stay on the
            # fixture's registrable domain. Deliberately NOT a blanket egress
            # allowlist: competitor analysis crawling third parties is the
            # product working correctly, and an allowlist would corrupt
            # fixtures 5, 7 and 10.
            egress_violations: list[str] = []
            if num <= 2 and _hosts_path is not None:
                from app.integrations.web_fetch import registrable_domain

                want = registrable_domain(args.url)
                for line in _hosts_path.read_text(encoding="utf-8").splitlines():
                    if not line.startswith(f"p{num:02d}_"):
                        continue
                    parts = line.split("	")
                    if len(parts) < 3:
                        continue
                    host = parts[2]
                    # Provider APIs are expected egress; the client's own site is not.
                    if any(p in host for p in ("dataforseo", "ahrefs", "openrouter",
                                               "anthropic", "firecrawl", "brandfetch",
                                               "googleapis", "seomoz")):
                        continue
                    if registrable_domain(host) != want:
                        egress_violations.append(host)
                if egress_violations:
                    print(f"  !! CLIENT-RESOLUTION EGRESS off {want}: "
                          f"{sorted(set(egress_violations))}")

            summaries = {}
            for attr in SUMMARY_ATTR.get(agent_key, ()):
                summaries[attr] = _jsonable(getattr(profile, attr, None))

            record = {
                "phase": num, "agent_key": agent_key, "message": message,
                "status": {"before": before_status, "after_run": after_run,
                           "after_approve": after_approve, "approved": approved,
                           "questionnaire": questionnaire},
                "error": error,
                "wall_seconds": round(elapsed, 2),
                "provider_calls": c1 - c0,
                "cost_usd": round(k1 - k0, 4),
                "events": _jsonable(events or []),
                "event_types": [e.get("type") for e in (events or [])],
                "blocked": any(e.get("type") == "error" or "blocked" in str(e.get("content", "")).lower()
                               for e in (events or [])),
                "egress_violations": sorted(set(egress_violations)) if num <= 2 else None,
                "findings_ledger": await _findings(db, client.id, agent_key),
                "summaries": summaries,
            }
            (out / f"phase{num:02d}_{agent_key}.json").write_text(
                json.dumps(record, indent=1, default=str), encoding="utf-8")
            manifest["phases"].append({k: record[k] for k in
                ("phase", "agent_key", "status", "error", "wall_seconds",
                 "provider_calls", "cost_usd", "blocked")})

            flag = "ERR" if error else ("BLOCKED" if record["blocked"] else "ok")
            print(f"  p{num:02d} {agent_key:<24} {flag:<8} {elapsed:6.1f}s "
                  f"{c1-c0:>4} calls  ${k1-k0:.4f}  {before_status} -> {after_approve}")

            if run_cost > args.budget:
                manifest["aborted"] = f"budget exceeded: ${run_cost:.2f} > ${args.budget:.2f}"
                print(f"  ABORT — {manifest['aborted']}")
                break

        total_calls, total_cost = await _cost_snapshot(db)
        manifest |= {
            "client_id": str(client.id),
            "finished": time.time(),
            "run_provider_calls": total_calls - base_calls,
            "run_cost_usd": round(total_cost - base_cost, 4),
        }
        (out / "run.json").write_text(json.dumps(manifest, indent=1, default=str), encoding="utf-8")
        print(f"fixture {args.fixture}: {manifest['run_provider_calls']} calls "
              f"${manifest['run_cost_usd']:.4f} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
