"""Public robots.txt + sitemap — marketing landing only."""

from __future__ import annotations

from app.config import clear_settings_cache, get_settings
from app.services.public_seo import build_robots_txt, build_sitemap_xml, public_site_origin


def test_public_site_origin_strips_path(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://radius.example.com/app")
    clear_settings_cache()
    assert public_site_origin() == "https://radius.example.com"
    clear_settings_cache()


def test_robots_txt_disallows_private_surfaces():
    text = build_robots_txt()
    assert "User-agent: *" in text
    assert "Disallow: /app" in text
    assert "Disallow: /clients" in text
    assert "Disallow: /api/" in text
    assert "Disallow: /media/" in text
    assert "Sitemap:" in text
    assert "sitemap.xml" in text
    # Must not ban the landing root.
    assert "Disallow: /\n" not in text and "Disallow: /$" not in text


def test_sitemap_xml_contains_only_public_home():
    xml = build_sitemap_xml()
    origin = public_site_origin()
    assert 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"' in xml
    assert f"<loc>{origin}/</loc>" in xml
    assert "/app" not in xml
    assert "/clients" not in xml
    assert xml.count("<url>") == 1


async def test_robots_and_sitemap_http_endpoints(api_client, monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://seo.example.com")
    clear_settings_cache()
    get_settings()

    robots = await api_client.get("/robots.txt")
    assert robots.status_code == 200
    assert "text/plain" in robots.headers.get("content-type", "")
    body = robots.text
    assert "Disallow: /app" in body
    assert "Sitemap: https://seo.example.com/sitemap.xml" in body

    sm = await api_client.get("/sitemap.xml")
    assert sm.status_code == 200
    assert "xml" in sm.headers.get("content-type", "")
    assert "<loc>https://seo.example.com/</loc>" in sm.text
    assert "/clients/" not in sm.text

    clear_settings_cache()
