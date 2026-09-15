#!/usr/bin/env python3
"""Phase 7 (technical SEO) harness.

Usage (from backend/):
  python scripts/run_phase7.py <client_id>
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from uuid import UUID  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.agents.technical_seo import run_technical_seo  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import AsyncSessionLocal  # noqa: E402
from app.models import ChatSession, Client  # noqa: E402


async def main() -> int:
    client_id = UUID(sys.argv[1])
    settings = get_settings()
    timeout = int(getattr(settings, "technical_seo_agent_timeout_seconds", 900) or 900)

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
            f"website={profile.website_status} architecture={profile.site_architecture_status} "
            f"search_demand={profile.search_demand_status}"
        )
        print(f"budget={timeout}s\n")

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
        try:
            events = await asyncio.wait_for(
                run_technical_seo(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=chat.user_id,
                    message="run technical seo audit",
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            print(f"TIMED OUT after {time.monotonic() - started:.1f}s")
            return 2

        await db.commit()
        elapsed = time.monotonic() - started
        summary = dict(profile.technical_seo_summary or {})
        print(f"COMPLETED in {elapsed:.1f}s")
        print(f"status={profile.technical_seo_status}")
        print(f"score={summary.get('score')}")
        print(f"issues={len(summary.get('issues') or [])}")
        print(f"broken={summary.get('broken_link_count')}")
        print(f"phase6_fallback={summary.get('phase6_fallback_ia')}")
        print(f"providers={summary.get('providers_used')}\n")

        for ev in events:
            kind = ev.get("type")
            body = str(ev.get("content") or "")[:240]
            print(f"[{kind}] {body}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
