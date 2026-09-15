#!/usr/bin/env python3
"""Run remaining phases for a client with auto-approve between stages.

Usage (from backend/):
  python scripts/run_all_phases.py <client_id> [--from N] [--to N]

Phases:
  3 website_situation_agent
  4 competitor_market_agent
  5 search_demand
  6 content_strategy + site_architecture
  7 technical_seo
  8 content_audit
  9 content_planning
  10 content_production
  11 on_page_seo
  12 publishing
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.agents import AGENT_RUNNERS  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import AsyncSessionLocal  # noqa: E402
from app.models import ChatSession, Client, User  # noqa: E402
from app.orchestration.pipeline import AGENT_TIMEOUT_SECONDS  # noqa: E402
from app.services.review import approve_phase_batch  # noqa: E402

HOD_EMAIL = "hod@trafficradius.com"

# (phase_num, agent_key, status_attr, message)
PHASES: list[tuple[int, str, str, str]] = [
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
    (12, "publishing", "publishing_status", "run publishing"),
]


async def _approve(db, hod: User, client_id: UUID, agent_key: str, profile, status_attr: str) -> bool:
    current = getattr(profile, status_attr, "not_started")
    if current == "complete":
        print(f"  approve skip: {agent_key} already complete")
        return True
    if current != "pending_signoff":
        print(f"  approve skip: {agent_key} status={current}")
        return current == "complete"
    print(f"  approving {agent_key}…")
    try:
        await approve_phase_batch(
            db,
            user=hod,
            client_id=client_id,
            agent_key=agent_key,
            action="approve",
            note="Auto-approved via run_all_phases harness",
        )
        await db.commit()
        await db.refresh(profile)
        print(f"  {agent_key} -> {getattr(profile, status_attr)}")
        return getattr(profile, status_attr) == "complete"
    except Exception as exc:  # noqa: BLE001
        print(f"  APPROVE FAILED {agent_key}: {exc}")
        traceback.print_exc()
        return False


async def _run(
    db,
    *,
    agent_key: str,
    runner,
    client,
    chat,
    user_id,
    message: str,
    timeout: int,
) -> bool:
    print(f"  running {agent_key} (timeout={timeout}s)…")
    started = time.monotonic()
    try:
        events = await asyncio.wait_for(
            runner(
                db,
                client=client,
                session_id=chat.id,
                user_id=user_id,
                message=message,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        print(f"  TIMEOUT {agent_key} after {time.monotonic() - started:.1f}s")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  RUN FAILED {agent_key}: {exc}")
        traceback.print_exc()
        return False

    await db.commit()
    elapsed = time.monotonic() - started
    print(f"  done {agent_key} in {elapsed:.1f}s ({len(events or [])} events)")
    for ev in events or []:
        if ev.get("type") in ("agent_message", "system_notice", "error") and ev.get("content"):
            print(f"    [{ev.get('type')}] {str(ev.get('content'))[:280]}")
    return True


def _timeout_for(agent_key: str) -> int:
    settings = get_settings()
    if agent_key == "technical_seo":
        return int(getattr(settings, "technical_seo_agent_timeout_seconds", 900) or 900)
    if agent_key in ("content_production", "on_page_seo", "publishing"):
        return int(getattr(settings, "content_agent_timeout_seconds", 1200) or 1200)
    return int(AGENT_TIMEOUT_SECONDS)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("client_id")
    parser.add_argument("--from", dest="from_phase", type=int, default=3)
    parser.add_argument("--to", dest="to_phase", type=int, default=12)
    parser.add_argument("--approve-only", action="store_true", help="Only approve pending packs")
    args = parser.parse_args()
    client_id = UUID(args.client_id)

    async with AsyncSessionLocal() as db:
        client = (
            await db.execute(
                select(Client).options(selectinload(Client.profile)).where(Client.id == client_id)
            )
        ).scalar_one_or_none()
        if client is None or not client.profile:
            print(f"client {client_id} not found")
            return 1
        profile = client.profile

        hod = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.email == HOD_EMAIL)
            )
        ).scalar_one_or_none()
        if hod is None:
            print(f"approver {HOD_EMAIL} not found")
            return 1

        chat = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.client_id == client_id)
                .order_by(ChatSession.started_at.desc())
            )
        ).scalars().first()
        if chat is None:
            print("no chat session")
            return 1

        print(f"client={client.display_name} url={client.primary_url}")
        print(f"from={args.from_phase} to={args.to_phase} approve_only={args.approve_only}\n")

        failures: list[str] = []

        for phase_num, agent_key, status_attr, message in PHASES:
            if phase_num < args.from_phase or phase_num > args.to_phase:
                continue

            status = getattr(profile, status_attr, "not_started")
            print(f"\n=== Phase {phase_num}: {agent_key} (status={status}) ===")

            if status == "complete":
                print("  already complete — skip")
                continue

            if status == "pending_signoff":
                ok = await _approve(db, hod, client_id, agent_key, profile, status_attr)
                if ok:
                    continue
                print("  approve failed while pending — will re-run agent")
                setattr(profile, status_attr, "in_progress")
                await db.commit()

            if args.approve_only:
                print("  not pending — skip (approve-only mode)")
                continue

            runner = AGENT_RUNNERS.get(agent_key)
            if runner is None:
                failures.append(f"missing_runner:{agent_key}")
                print("  STOP — no runner")
                break

            run_message = message
            if agent_key == "competitor_market_agent":
                run_message = "refresh competitor scan"

            # Phase 10 already briefed and is waiting for a topic click — draft top pick.
            if agent_key == "content_production" and status == "in_progress":
                prod = dict(profile.content_production_summary or {})
                choices = [
                    c for c in (prod.get("topic_choices") or []) if isinstance(c, dict)
                ]
                if choices and not prod.get("drafts"):
                    first = choices[0]
                    kw = str(first.get("keyword") or first.get("title") or "")
                    url = str(first.get("url") or "")
                    run_message = f"Write the full draft for: {kw} ({url})"
                    print(f"  drafting top topic: {kw} ({url})")

            ok = await _run(
                db,
                agent_key=agent_key,
                runner=runner,
                client=client,
                chat=chat,
                user_id=hod.id,
                message=run_message,
                timeout=_timeout_for(agent_key),
            )
            await db.refresh(profile)
            status = getattr(profile, status_attr, "not_started")
            print(f"  post-run status={status}")

            # After briefs-only run, immediately draft the first topic choice.
            if (
                ok
                and agent_key == "content_production"
                and status == "in_progress"
            ):
                prod = dict(profile.content_production_summary or {})
                choices = [
                    c for c in (prod.get("topic_choices") or []) if isinstance(c, dict)
                ]
                if choices and not prod.get("drafts"):
                    first = choices[0]
                    kw = str(first.get("keyword") or first.get("title") or "")
                    url = str(first.get("url") or "")
                    draft_msg = f"Write the full draft for: {kw} ({url})"
                    print(f"  drafting top topic: {kw} ({url})")
                    ok = await _run(
                        db,
                        agent_key=agent_key,
                        runner=runner,
                        client=client,
                        chat=chat,
                        user_id=hod.id,
                        message=draft_msg,
                        timeout=_timeout_for(agent_key),
                    )
                    await db.refresh(profile)
                    status = getattr(profile, status_attr, "not_started")
                    print(f"  post-draft status={status}")

            if not ok:
                failures.append(f"run:{agent_key}")
                print("  STOP — run failed")
                break

            if status == "pending_signoff":
                ok = await _approve(db, hod, client_id, agent_key, profile, status_attr)
                if not ok:
                    failures.append(f"approve:{agent_key}")
                    print("  STOP — cannot approve after run")
                    break
            elif status != "complete":
                failures.append(f"bad_status:{agent_key}:{status}")
                print("  STOP — expected pending_signoff or complete")
                break

        print("\n=== Final statuses ===")
        for _, agent_key, status_attr, _ in PHASES:
            print(f"  {agent_key}: {getattr(profile, status_attr)}")
        print(f"  ready_for_phase5: {profile.ready_for_phase5}")

        if failures:
            print(f"\nFAILURES: {failures}")
            return 1
        print("\nALL PHASES OK")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
