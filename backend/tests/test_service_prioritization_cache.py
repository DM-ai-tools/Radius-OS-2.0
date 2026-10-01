"""Competitor service-tree discovery must not re-crawl on every read.

`GET /api/v1/clients/{id}/service-prioritization` took 21.4s against the
running app, repeatably. The handler calls `discover_competitor_service_trees`,
which fetches up to 8 competitor sitemaps live with a 20s per-competitor
timeout, and nothing cached the result — so every page view re-crawled the
whole competitor set and one slow competitor pinned the request at the timeout.

That is long enough to trip a proxy/gateway read timeout in production, on an
endpoint the UI opens as a checkpoint card.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import service_prioritization as sp


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Never let a live Redis leak results between these tests."""
    store: dict[str, object] = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, value, ttl):
        store[key] = value

    with patch.object(sp, "cache_get", fake_get), patch.object(sp, "cache_set", fake_set):
        yield store


COMPETITORS = [
    {"name": "Alpha", "domain": "alpha.com", "url": "https://alpha.com"},
    {"name": "Beta", "domain": "beta.com", "url": "https://beta.com"},
]


async def test_a_second_call_does_not_re_crawl():
    calls: list[str] = []

    async def fake_discover(start, max_pages=35):
        calls.append(start)
        return [f"{start}/services/seo", f"{start}/services/ppc"]

    with patch.object(sp, "discover_site_urls", fake_discover):
        first = await sp.discover_competitor_service_trees(COMPETITORS, client_id="c1")
        second = await sp.discover_competitor_service_trees(COMPETITORS, client_id="c1")

    assert len(calls) == 2, "first call crawls each competitor once"
    assert second == first


async def test_a_changed_competitor_set_is_not_served_from_the_old_cache():
    calls: list[str] = []

    async def fake_discover(start, max_pages=35):
        calls.append(start)
        return [f"{start}/services/seo"]

    with patch.object(sp, "discover_site_urls", fake_discover):
        await sp.discover_competitor_service_trees(COMPETITORS, client_id="c1")
        await sp.discover_competitor_service_trees(
            COMPETITORS + [{"name": "Gamma", "domain": "gamma.com", "url": "https://gamma.com"}],
            client_id="c1",
        )

    assert len(calls) == 5, "a new competitor invalidates the entry and re-crawls"


async def test_two_clients_do_not_share_an_entry():
    calls: list[str] = []

    async def fake_discover(start, max_pages=35):
        calls.append(start)
        return [f"{start}/services/seo"]

    with patch.object(sp, "discover_site_urls", fake_discover):
        await sp.discover_competitor_service_trees(COMPETITORS, client_id="c1")
        await sp.discover_competitor_service_trees(COMPETITORS, client_id="c2")

    assert len(calls) == 4


async def test_without_a_client_id_it_still_works_and_still_crawls():
    """Callers that pass no client id keep the old uncached behaviour."""
    calls: list[str] = []

    async def fake_discover(start, max_pages=35):
        calls.append(start)
        return [f"{start}/services/seo"]

    with patch.object(sp, "discover_site_urls", fake_discover):
        trees = await sp.discover_competitor_service_trees(COMPETITORS)

    assert len(calls) == 2
    assert len(trees) == 2


async def test_a_crawl_failure_is_not_cached_as_a_success():
    """Caching an empty result would hide a transient outage for a whole TTL."""
    attempt = {"n": 0}

    async def flaky(start, max_pages=35):
        attempt["n"] += 1
        if attempt["n"] <= 2:
            raise TimeoutError("slow competitor")
        return [f"{start}/services/seo"]

    with patch.object(sp, "discover_site_urls", flaky):
        first = await sp.discover_competitor_service_trees(COMPETITORS, client_id="c1")
        second = await sp.discover_competitor_service_trees(COMPETITORS, client_id="c1")

    assert all(t["url_count"] == 0 for t in first)
    assert any(t["url_count"] > 0 for t in second), "a failed crawl must be retried"
