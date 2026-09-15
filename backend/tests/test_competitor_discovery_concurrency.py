"""AUDIT-025: discover_competitor_service_trees used to crawl competitors
one at a time inside a GET handler, so latency was the sum of every
competitor's sitemap discovery instead of the max. These pin: order is
preserved, one competitor's failure doesn't affect the others, and the calls
actually run concurrently (not just "look concurrent")."""

from __future__ import annotations

import asyncio
import time

from app.services import service_prioritization


async def test_preserves_order_and_isolates_per_competitor_failures(monkeypatch):
    async def _fake_discover(url, *, max_pages):
        if "slow" in url:
            raise RuntimeError("boom")
        return [f"{url}/services", f"{url}/about"]

    monkeypatch.setattr(service_prioritization, "discover_site_urls", _fake_discover)

    competitors = [
        {"name": "A", "domain": "a.example", "url": "https://a.example"},
        {"name": "Slow", "domain": "slow.example", "url": "https://slow.example"},
        {"name": "B", "domain": "b.example", "url": "https://b.example"},
    ]
    trees = await service_prioritization.discover_competitor_service_trees(competitors)
    assert [t["name"] for t in trees] == ["A", "Slow", "B"]
    assert trees[1]["url_count"] == 0
    assert trees[0]["url_count"] == 2


async def test_runs_concurrently_not_sequentially(monkeypatch):
    async def _slow_discover(url, *, max_pages):
        await asyncio.sleep(0.2)
        return [f"{url}/services"]

    monkeypatch.setattr(service_prioritization, "discover_site_urls", _slow_discover)
    competitors = [
        {"name": n, "domain": f"{n}.example", "url": f"https://{n}.example"}
        for n in ("a", "b", "c", "d")
    ]

    start = time.monotonic()
    trees = await service_prioritization.discover_competitor_service_trees(competitors)
    elapsed = time.monotonic() - start

    assert len(trees) == 4
    assert elapsed < 0.5, "4x0.2s discoveries should overlap, not sum to ~0.8s"


async def test_respects_max_competitors_cap(monkeypatch):
    calls: list[str] = []

    async def _fake_discover(url, *, max_pages):
        calls.append(url)
        return []

    monkeypatch.setattr(service_prioritization, "discover_site_urls", _fake_discover)
    competitors = [
        {"name": n, "domain": f"{n}.example", "url": f"https://{n}.example"}
        for n in ("a", "b", "c", "d", "e")
    ]
    trees = await service_prioritization.discover_competitor_service_trees(
        competitors, max_competitors=2
    )
    assert len(trees) == 2
    assert len(calls) == 2
