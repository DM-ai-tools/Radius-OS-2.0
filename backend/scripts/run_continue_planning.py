#!/usr/bin/env python3
"""Continue Click Trends pipeline: approve upstream phases and run planning chain.

Usage (from backend/):
  python scripts/run_continue_planning.py <client_id>
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.agents.content_planning import run_content_planning
from app.agents.content_strategy import run_content_strategy
from app.agents.site_architecture import run_site_architecture
from app.db import AsyncSessionLocal
from app.models import ChatSession, Client, User
from app.orchestration.pipeline import AGENT_TIMEOUT_SECONDS
from app.services.review import approve_phase_batch

HOD_EMAIL = "hod@trafficradius.com"
CLIENT_ID = "1e7c471b-c85f-44ba-9ca4-484ebc3536a3"


async def _approve(
    db,
    hod: User,
    client_id: UUID,
    agent_key: str,
    profile,
    status_attr: str,
) -> bool:
    current = getattr(profile, status_attr, "not_started")
    if current == "complete":
        print(f"  {agent_key}: already complete")
        return True
    if current != "pending_signoff":
        print(f"  {agent_key}: skip approve (status={current})")
        return current == "complete"
    print(f"  approving {agent_key}…")
    try:
        await approve_phase_batch(
            db,
            user=hod,
            client_id=client_id,
            agent_key=agent_key,
            action="approve",
            note="Auto-approved via run_continue_planning harness",
        )
        await db.commit()
        await db.refresh(profile)
        print(f"  {agent_key} -> {getattr(profile, status_attr)}")
        return getattr(profile, status_attr) == "complete"
    except Exception as exc:  # noqa: BLE001
        print(f"  {agent_key} approve failed: {exc}")
        return False


async def _run_agent(name: str, coro, timeout: int) -> list[dict]:
    print(f"\nRunning {name}…")
    started = time.monotonic()
    try:
        events = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        print(f"  TIMED OUT after {time.monotonic() - started:.1f}s")
        return []
    elapsed = time.monotonic() - started
    print(f"  done in {elapsed:.1f}s ({len(events)} events)")
    for ev in events:
        if ev.get("type") in ("agent_message", "system_notice") and ev.get("content"):
            print(f"  [{ev.get('type')}] {str(ev.get('content'))[:240]}")
    return events


async def main() -> int:
    client_id = UUID(sys.argv[1] if len(sys.argv) > 1 else CLIENT_ID)

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

        print(f"client={client.display_name}")
        print(
            f"search_demand={profile.search_demand_status} "
            f"strategy={profile.seo_strategy_status} "
            f"architecture={profile.site_architecture_status} "
            f"content_audit={profile.content_audit_status} "
            f"planning={profile.content_planning_status}\n"
        )

        print("Step 1 — approve upstream packs")
        await _approve(db, hod, client_id, "content_audit", profile, "content_audit_status")
        await _approve(db, hod, client_id, "search_demand", profile, "search_demand_status")

        if profile.seo_strategy_status != "complete":
            await _run_agent(
                "content_strategy",
                run_content_strategy(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=hod.id,
                    message="run content strategy",
                ),
                AGENT_TIMEOUT_SECONDS,
            )
            await db.commit()
            await db.refresh(profile)
            await _approve(db, hod, client_id, "content_strategy", profile, "seo_strategy_status")

        if profile.site_architecture_status != "complete":
            await _run_agent(
                "site_architecture",
                run_site_architecture(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=hod.id,
                    message="run site architecture",
                ),
                AGENT_TIMEOUT_SECONDS,
            )
            await db.commit()
            await db.refresh(profile)
            await _approve(db, hod, client_id, "site_architecture", profile, "site_architecture_status")

        if profile.content_planning_status != "pending_signoff":
            await _run_agent(
                "content_planning",
                run_content_planning(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=hod.id,
                    message="run content planning",
                ),
                AGENT_TIMEOUT_SECONDS,
            )
            await db.commit()
            await db.refresh(profile)

        plan = dict(profile.content_planning_summary or {})
        ia = dict(profile.site_architecture_summary or {})
        print("\n=== Final status ===")
        print(f"search_demand={profile.search_demand_status}")
        print(f"seo_strategy={profile.seo_strategy_status}")
        print(f"site_architecture={profile.site_architecture_status} tree={len(ia.get('target_url_tree') or [])}")
        print(f"content_audit={profile.content_audit_status}")
        print(f"content_planning={profile.content_planning_status}")
        print(f"planned={plan.get('planned_count')} locked={plan.get('locked')} blocked={plan.get('blocked')}")
        if plan.get("lock_reason"):
            print(f"lock_reason={plan.get('lock_reason')}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
