---
name: firecrawl
description: >
  Firecrawl for Radius OP — rendered scrape/map when plain httpx is blocked
  (WAF/bot challenge), and for design/layout hints on publish preview.
  Prefer the in-app integration (FIRECRAWL_API_KEY + app.integrations.firecrawl).
---

# Firecrawl (Radius OP)

Use Firecrawl when live HTML is incomplete or blocked, or when design/layout
context is needed beyond plain text extraction.

## Product integration (Path B — already wired)

| Piece | Location |
|-------|----------|
| Config | `FIRECRAWL_API_KEY` in `.env` → `settings.firecrawl_api_key` |
| Client | `backend/app/integrations/firecrawl.py` → `scrape_page`, `layout_hints` |
| Fallback fetch | `web_fetch.fetch_page_html` — httpx first, then Firecrawl on bot challenge / thin HTML |
| SEO audit pages | `providers.run_seo_audit` uses `fetch_page_html` |
| Publish preview | `publish_preview` / `publishing` scrape a reference hub for layout hints |

Do **not** commit API keys. Keep them only in local `.env` (gitignored).

## When to use Firecrawl vs plain crawl

1. **`fetch_url` / discover_site_urls** — default for URL inventory (fast, cheap).
2. **`fetch_page_html` / `scrape_page`** — when status is bot-challenge (202), empty body, or HTML &lt; ~800 bytes.
3. **`layout_hints`** — Phase 12 preview framing (H1/H2 pattern), not full CSS clone.
4. **Map / crawl endpoints** — optional for denser URL discovery when robots/sitemap are blocked (prefer existing `discover_site_urls` + indexed SERP first).

## Design accuracy expectations

Firecrawl returns **rendered HTML + markdown + metadata**, which is enough for:

- real titles / H1 / body text on WAF sites (e.g. SiteGround)
- publish preview structural hints

It is **not** a pixel-perfect design clone. For visual QA, use screenshots / browser tools separately.

## Agent session tools (optional Path A)

If the human has installed the Firecrawl CLI (`npx -y firecrawl-cli@latest init`):

- `firecrawl scrape <url>` — one page
- `firecrawl map <url>` — URL discovery
- `firecrawl crawl <url>` — bulk extraction
- `firecrawl interact` — clicks/forms when scrape is not enough

For Radius product work, prefer calling `app.integrations.firecrawl` so SSRF guards and metering stay consistent.

## Smoke test

```python
from app.integrations.firecrawl import configured, scrape_page
assert configured()
r = await scrape_page("https://example.com")
assert r.get("available")
```

## Docs

- API: https://docs.firecrawl.dev/api-reference/v2-introduction
- Skills/workflows: https://github.com/firecrawl/skills
