#!/usr/bin/env python3
"""Phase 6 (site architecture) harness — verify crawl → URL mapping feed.

Usage (from backend/):
  python scripts/run_phase6.py <client_id>
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
from sqlalchemy.orm import selectinload

from app.agents.site_architecture import run_site_architecture
from app.db import AsyncSessionLocal
from app.models import ChatSession, Client
from app.orchestration.pipeline import AGENT_TIMEOUT_SECONDS
from app.services.url_mapping import collect_crawl_pages


async def main() -> int:
    client_id = UUID(sys.argv[1])

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
        website = dict(profile.website_situation_summary or {})
        seo = dict(profile.seo_strategy_summary or {})
        crawled = collect_crawl_pages(website=website, content_audit=seo.get("content_audit"))
        print(f"client={client.display_name} url={client.primary_url}")
        print(f"search_demand={profile.search_demand_status} seo_strategy={profile.seo_strategy_status}")
        print(f"crawl_pages_for_url_mapping={len(crawled)}")
        print(f"budget={AGENT_TIMEOUT_SECONDS}s\n")

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

        started = time.monotonic()
        try:
            events = await asyncio.wait_for(
                run_site_architecture(
                    db,
                    client=client,
                    session_id=chat.id,
                    user_id=chat.user_id,
                    message="run site architecture",
                ),
                timeout=AGENT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            print(f"TIMED OUT after {time.monotonic() - started:.1f}s")
            return 2

        await db.commit()
        elapsed = time.monotonic() - started
        print(f"COMPLETED in {elapsed:.1f}s ({elapsed / 60:.1f} min)\n")

        summary = dict(profile.site_architecture_summary or {})
        url_map = summary.get("url_map_report") or {}
        rows = list(url_map.get("rows") or url_map.get("mappings") or [])
        comp_tree = summary.get("competitor_service_tree")
        comp_nodes = summary.get("competitor_ia_nodes") or []
        print(f"status={profile.site_architecture_status}")
        print(f"target_url_tree={len(summary.get('target_url_tree') or [])}")
        print(f"url_map_rows={len(rows)}")
        print(f"competitor_service_tree={'yes' if comp_tree else 'no'}")
        print(f"competitor_ia_nodes={len(comp_nodes)}")
        if rows:
            sample = rows[0]
            print(f"sample_url_map_keys={list(sample.keys())[:8]}")

        for ev in events:
            kind = ev.get("type")
            body = str(ev.get("content") or "")[:240]
            print(f"[{kind}] {body}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
