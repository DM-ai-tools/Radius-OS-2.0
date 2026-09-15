#!/usr/bin/env python3
"""Phase 11 — approve Content Production and run On-Page SEO (+ optional next draft).

Usage (from backend/):
  python scripts/run_phase11.py <client_id> [--next-draft]
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

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.agents.content_production import run_content_production  # noqa: E402
from app.agents.on_page_seo import run_on_page_seo  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import AsyncSessionLocal  # noqa: E402
from app.models import ChatSession, Client, User  # noqa: E402
from app.services.review import approve_phase_batch  # noqa: E402

HOD_EMAIL = "hod@trafficradius.com"
CLIENT_ID = "1e7c471b-c85f-44ba-9ca4-484ebc3536a3"


async def _run(name: str, coro, timeout: int) -> list[dict]:
    print(f"\nRunning {name}…")
    started = time.monotonic()
    try:
        events = await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        print(f"  TIMED OUT after {time.monotonic() - started:.1f}s")
        return []
    print(f"  done in {time.monotonic() - started:.1f}s")
    for ev in events:
        if ev.get("type") in ("agent_message", "system_notice") and ev.get("content"):
            print(f"  [{ev.get('type')}] {str(ev.get('content'))[:280]}")
    return events


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("client_id", nargs="?", default=CLIENT_ID)
    parser.add_argument("--next-draft", action="store_true")
    args = parser.parse_args()
    client_id = UUID(args.client_id)
    timeout = int(getattr(get_settings(), "agent_timeout_seconds", 480) or 480)

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

        prod = dict(profile.content_production_summary or {})
        print(f"client={client.display_name}")
        print(
            f"production={profile.content_production_status} "
            f"on_page={profile.on_page_seo_status} "
            f"drafts={prod.get('draft_count')}\n"
        )

        if profile.content_production_status == "pending_signoff":
            print("Approving content_production…")
            try:
                await approve_phase_batch(
                    db,
                    user=hod,
                    client_id=client_id,
                    agent_key="content_production",
                    action="approve",
                    note="Auto-approved via run_phase11 harness",
                )
                await db.commit()
                await db.refresh(profile)
                print(f"  content_production -> {profile.content_production_status}\n")
            except Exception as exc:  # noqa: BLE001
                print(f"  approve failed: {exc}")
                return 2

        if profile.on_page_seo_status != "pending_signoff":
            await _run(
                "on_page_seo",
                run_on_page_seo(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=hod.id,
                    message="run on-page seo",
                ),
                timeout,
            )
            await db.commit()
            await db.refresh(profile)

        on_page = dict(profile.on_page_seo_summary or {})
        print("\n=== On-Page SEO ===")
        print(f"status={profile.on_page_seo_status}")
        print(f"pages={len(on_page.get('pages') or [])}")
        print(f"internal_links={len(on_page.get('internal_links') or [])}")
        if on_page.get("pages"):
            p0 = on_page["pages"][0]
            if isinstance(p0, dict):
                print(f"title_tag={str(p0.get('title_tag') or p0.get('title') or '')[:70]}")
                print(f"meta={str(p0.get('meta_description') or '')[:100]}")

        if args.next_draft:
            choices = prod.get("topic_choices") or []
            drafted_url = str((prod.get("drafts") or [{}])[0].get("url") or "")
            nxt = None
            for c in choices:
                if isinstance(c, dict) and str(c.get("url") or "") != drafted_url:
                    nxt = c
                    break
            if nxt:
                kw = str(nxt.get("keyword") or nxt.get("title") or "")
                url = str(nxt.get("url") or "")
                msg = f"Write the full draft for: {kw} ({url})"
                await _run(
                    "content_production (next topic)",
                    run_content_production(
                        db,
                        client=client,
                        session_id=chat.id,
                        user_id=hod.id,
                        message=msg,
                    ),
                    timeout,
                )
                await db.commit()
                await db.refresh(profile)
                prod = dict(profile.content_production_summary or {})
                print(f"\nnew_draft_count={prod.get('draft_count')}")
                print(f"production_status={profile.content_production_status}")

        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
