#!/usr/bin/env python3
"""Phase 5 (search demand) timing harness.

Usage (from backend/):
  python scripts/run_phase5.py <client_id>
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

from uuid import UUID

from sqlalchemy import select

from app.agents.search_demand import run_search_demand
from app.db import AsyncSessionLocal
from app.models import ChatSession, Client
from app.orchestration.pipeline import AGENT_TIMEOUT_SECONDS


async def main() -> int:
    client_id = UUID(sys.argv[1])

    async with AsyncSessionLocal() as db:
        client = (
            await db.execute(select(Client).where(Client.id == client_id))
        ).scalar_one_or_none()
        if client is None:
            print(f"client {client_id} not found")
            return 1

        chat = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.client_id == client_id)
                .order_by(ChatSession.started_at.desc())
            )
        ).scalars().first()
        if chat is None:
            print("no chat session for this client")
            return 1

        print(f"client={client.display_name} url={client.primary_url}")
        print(f"budget={AGENT_TIMEOUT_SECONDS}s\n")

        started = time.monotonic()
        try:
            events = await asyncio.wait_for(
                run_search_demand(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=chat.user_id,
                    message="run keyword research",
                ),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            print(f"TIMED OUT after {time.monotonic() - started:.1f}s")
            return 2

        elapsed = time.monotonic() - started
        print(f"COMPLETED in {elapsed:.1f}s ({elapsed / 60:.1f} min)")
        print(f"budget used: {elapsed / AGENT_TIMEOUT_SECONDS * 100:.0f}%\n")

        for ev in events:
            kind = ev.get("type")
            body = str(ev.get("content") or "")[:300]
            print(f"[{kind}] {body}")
            payload = ev.get("payload") or {}
            if isinstance(payload, dict):
                seeding = payload.get("keyword_seeding")
                if seeding:
                    print(f"    keyword_seeding={seeding}")
                for key in ("providers_used", "provider_errors"):
                    if payload.get(key):
                        print(f"    {key}={payload[key]}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
