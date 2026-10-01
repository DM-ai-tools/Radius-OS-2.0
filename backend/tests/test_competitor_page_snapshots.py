"""Competitor page snapshots keep the on-page signals keyword intel needs."""

from app.services import competitive_context


async def test_snapshot_pages_capture_headings_and_meta(monkeypatch):
    async def _fake_discover(start_url, *, max_pages=0):
        return [
            "https://rival.example/services/drain-cleaning",
            "https://rival.example/blog/how-to-unclog",
        ]

    async def _fake_fetch(url, *, timeout=8, **kwargs):
        return {
            "url": url,
            "status_code": 200,
            "text": (
                "<html><head><title>Drain Cleaning Services | Rival</title>"
                '<meta name="description" content="Emergency drain cleaning and jetting.">'
                "</head><body><h1>Drain Cleaning Experts</h1>"
                "<h2>Hydro Jetting</h2><h2>Blocked Drain Repair</h2>"
                "</body></html>"
            ),
        }

    monkeypatch.setattr(competitive_context, "discover_site_urls", _fake_discover)
    monkeypatch.setattr(competitive_context, "fetch_url", _fake_fetch)

    snaps = await competitive_context.fetch_competitor_site_snapshots(
        [{"name": "Rival", "url": "https://rival.example"}],
        max_competitors=1,
    )
    page = snaps[0]["pages"][0]
    assert page["title"] == "Drain Cleaning Services | Rival"
    assert page["h1s"] == ["Drain Cleaning Experts"]
    assert "Hydro Jetting" in page["h2s"]
    assert page["meta_description"] == "Emergency drain cleaning and jetting."
