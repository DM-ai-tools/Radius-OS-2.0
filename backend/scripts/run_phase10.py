#!/usr/bin/env python3
"""Phase 10 — approve Content Planning and run Content Production.

Usage (from backend/):
  python scripts/run_phase10.py <client_id> [--draft-first]
"""

from __future__ import annotations

import argparse
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

from app.agents.content_production import run_content_production
from app.config import get_settings
from app.db import AsyncSessionLocal
from app.models import ChatSession, Client, User
from app.services.review import approve_phase_batch

HOD_EMAIL = "hod@trafficradius.com"


async def _run_production(db, client, chat, user_id, message: str, timeout: int) -> list[dict]:
    print(f"\nContent production: {message[:80]}…")
    started = time.monotonic()
    try:
        events = await asyncio.wait_for(
            run_content_production(
                db,
                client=client,
                session_id=chat.id,
                user_id=user_id,
                message=message,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        print(f"  TIMED OUT after {time.monotonic() - started:.1f}s")
        return []
    print(f"  done in {time.monotonic() - started:.1f}s")
    for ev in events:
        if ev.get("type") in ("agent_message", "system_notice") and ev.get("content"):
            print(f"  [{ev.get('type')}] {str(ev.get('content'))[:260]}")
    return events


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("client_id")
    parser.add_argument("--draft-first", action="store_true", help="Draft the top priority topic")
    args = parser.parse_args()
    client_id = UUID(args.client_id)
    settings = get_settings()
    timeout = int(getattr(settings, "agent_timeout_seconds", 480) or 480)

    async with AsyncSessionLocal() as db:
        client = (
            await db.execute(
                select(Client).options(selectinload(Client.profile)).where(Client.id == client_id)
            )
        ).scalar_one_or_none()
        if not client or not client.profile:
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
            f"planning={profile.content_planning_status} locked="
            f"{(profile.content_planning_summary or {}).get('locked')} "
            f"production={profile.content_production_status}\n"
        )

        if profile.content_planning_status != "complete":
            print("Approving content_planning…")
            try:
                await approve_phase_batch(
                    db,
                    user=hod,
                    client_id=client_id,
                    agent_key="content_planning",
                    action="approve",
                    note="Auto-approved via run_phase10 harness",
                )
                await db.commit()
                await db.refresh(profile)
                print(f"  content_planning -> {profile.content_planning_status}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"  approve failed: {exc}")
                return 2

        await _run_production(db, client, chat, hod.id, "run content production briefs", timeout)
        await db.commit()
        await db.refresh(profile)

        summary = dict(profile.content_production_summary or {})
        print(
            f"\nBriefs: {summary.get('brief_count')} writer_ready={summary.get('writer_ready_count')} "
            f"awaiting={summary.get('awaiting_topic_selection')}"
        )
        choices = summary.get("topic_choices") or []
        for i, c in enumerate(choices[:6], 1):
            if isinstance(c, dict):
                print(f"  {i}. {c.get('keyword')} ({c.get('url')})")

        if args.draft_first and choices and not summary.get("drafts"):
            first = choices[0]
            kw = str(first.get("keyword") or first.get("title") or "")
            url = str(first.get("url") or "")
            msg = f"Write the full draft for: {kw} ({url})"
            await _run_production(db, client, chat, hod.id, msg, timeout)
            await db.commit()
            await db.refresh(profile)
            summary = dict(profile.content_production_summary or {})

        drafts = summary.get("drafts") or []
        print("\n=== Final ===")
        print(f"production_status={profile.content_production_status}")
        print(f"draft_count={summary.get('draft_count') or len(drafts)}")
        if summary.get("write_refusal"):
            print(f"write_refusal={summary.get('write_refusal')}")
        if drafts:
            d0 = drafts[0]
            print(f"draft_title={d0.get('title') or d0.get('keyword')}")
            print(f"draft_words={len(str(d0.get('markdown') or '').split())}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
