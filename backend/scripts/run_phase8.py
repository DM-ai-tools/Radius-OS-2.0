#!/usr/bin/env python3
"""Phase 8 (content audit) harness — approves Phase 7 if needed, then runs audit.

Usage (from backend/):
  python scripts/run_phase8.py <client_id> [--skip-approve]
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

from app.agents.content_audit import run_content_audit
from app.db import AsyncSessionLocal
from app.models import ChatSession, Client, User
from app.services.review import approve_phase_batch

HOD_EMAIL = "hod@trafficradius.com"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("client_id")
    parser.add_argument("--skip-approve", action="store_true")
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
        print(f"client={client.display_name} url={client.primary_url}")
        print(
            f"technical_seo={profile.technical_seo_status} "
            f"content_audit={profile.content_audit_status}\n"
        )

        hod = (
            await db.execute(
                select(User).options(selectinload(User.role)).where(User.email == HOD_EMAIL)
            )
        ).scalar_one_or_none()
        if hod is None:
            print(f"approver user {HOD_EMAIL} not found — seed the database first")
            return 1

        if profile.technical_seo_status != "complete" and not args.skip_approve:
            print("Approving Phase 7 (technical_seo)…")
            try:
                result = await approve_phase_batch(
                    db,
                    user=hod,
                    client_id=client_id,
                    agent_key="technical_seo",
                    action="approve",
                    note="Auto-approved via run_phase8 harness",
                )
                await db.commit()
                print(f"  approved: {result.get('status') or result}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"  approve failed: {exc}")
                return 2
            await db.refresh(profile)
            print(f"technical_seo={profile.technical_seo_status}\n")

        if profile.technical_seo_status != "complete":
            print("Phase 7 not complete — approve technical SEO first")
            return 2

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

        started = time.monotonic()
        events = await run_content_audit(
            db,
            client=client,
            session_id=chat.id,
            user_id=hod.id,
            message="run content audit",
        )
        await db.commit()
        elapsed = time.monotonic() - started

        summary = dict(profile.content_audit_summary or {})
        counts = summary.get("summary_counts") or {}
        print(f"COMPLETED in {elapsed:.1f}s")
        print(f"status={profile.content_audit_status}")
        print(f"inventory={len(summary.get('inventory') or [])}")
        print(f"counts={counts}")
        print(f"qualitative={summary.get('qualitative')}")
        print(f"blocked={summary.get('blocked')}\n")

        for ev in events:
            kind = ev.get("type")
            body = str(ev.get("content") or "")[:280]
            print(f"[{kind}] {body}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
