---
name: site-page-inventory
description: >
  Crawl a live website and produce a classified page inventory — every URL
  fetched, typed, and measured, reconciled against the XML sitemaps to surface
  orphans, broken links and empty pages. Use for "site map", "page inventory",
  "crawl the site", "list every page", or content/SEO audits needing per-page data.
  In Radius OP this skill is executed primarily via Perplexity (OpenRouter research_model).
---

# Site Page Inventory

Produce a complete, verified inventory of every URL on a site: fetched individually,
classified by page type, measured, and reconciled against the CMS sitemaps.

Deliver two things: a published artifact (grouped, with findings) and a CSV-ready
row list (one row per URL, sortable).

## Radius OP execution

**Primary executor:** Perplexity via OpenRouter (`settings.research_model`, default
`perplexity/sonar-pro`) through `app.services.site_page_inventory` /
`app.integrations.site_research`.

**Live fetch fallbacks (in order):**

1. Plain HTTP (`web_fetch.fetch_url` / `discover_site_urls`)
2. Firecrawl (`fetch_page_html` / `firecrawl.scrape_page`) when bot challenge / thin HTML
3. Perplexity research inventory (this skill) when WAF blocks bulk fetch or coverage is thin

Never solve SiteGround/Cloudflare challenges programmatically. A bot challenge with
`x-robots-tag: noindex` is a finding — tell the user to verify crawler exemption in
Search Console; do not claim Googlebot is blocked.

## Scope check

If the user says "site map" they may mean the XML sitemap contents or the full site
architecture. Build the **superset** — everything in the sitemaps plus everything
linked but unlisted — and say so in one line.

## Step 1 — Establish a working fetch path

Try HTTP → Firecrawl → Perplexity. Stop enriching at the first path that yields
usable page rows with titles.

## Step 2 — Discover every URL

Breadth-first from homepage + XML sitemaps + `site:` / public index evidence.
Normalise paths (trailing slash, same-host only). Separate archive/pagination
(`/author/`, `/category/`, `/tag/`, `/page/N/`) from the main inventory — count them,
note robots-disallowed, keep them out of the primary list.

## Step 3 — Fetch and measure every URL

Per URL capture when evidence allows: status, title + length, meta description +
length, H1 text and count, H2 count, body word count, HTML KB, image count,
canonical, robots, last-modified.

Check byte size, not just status. A `200` at a few hundred bytes with no title/body
is a **broken page serving 200**.

## Step 4 — Verify before reporting

Sites under rapid sequential fetching return inconsistent HTML. For every **negative**
finding (missing meta, missing H1), prefer double-check via Firecrawl / second
Perplexity pass when live fetch is unreliable. Only report what survives. If a metric
cannot be verified, exclude it and say why.

## Step 5 — Classify from the CMS, not the URL

Prefer sitemap membership as ground truth:

- in `post-sitemap.xml` / blog sitemap → blog article
- in `page-sitemap.xml` → page, then sort by title/H1/nav into: core service, service,
  location, proof/case study, company, conversion, legal
- in neither → flag it

Heuristics are fine as a first pass; validate against sitemap membership when known.

## Step 6 — Reconcile

| Condition | Meaning |
|---|---|
| In sitemap, no inbound internal links | **Orphan** |
| Linked, returns 404 | **Broken link** |
| Linked, not in sitemap, indexable | **Missing from sitemap** |
| Linked, not in sitemap, noindex | Intentional exclusion (crawl-budget note) |
| 200 with near-empty body | **Broken page serving 200** |

## Step 7 — Deliver

Artifact: stats; findings by impact; inventory grouped by page type.
CSV-ready rows: URL, page type, title, title length, desc length, H1 count, H2 count,
word count, HTML KB, images, robots, sitemap membership, orphan, last modified, HTTP, issues.

Include a **method and limits** note. Report median word count by page type when available.

## Reporting rules

- Never state a negative finding that survived only one weak fetch.
- Distinguish confirmed mechanisms from confirmed outages.
- Name what you could not measure.
- Persist as `site_sitemap` / `page_inventory` on Phase 3 website situation for downstream reuse.
