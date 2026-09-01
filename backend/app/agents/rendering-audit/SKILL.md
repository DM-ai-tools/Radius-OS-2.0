---
name: rendering-audit
description: >-
  Verify that search engines can actually see JavaScript-rendered content, and
  check mobile/desktop content parity for mobile-first indexing. Use when the
  user asks "is my JavaScript content indexable", "can Google see my React
  content", "why isn't my SPA indexing", "rendered vs raw HTML",
  "JavaScript SEO", "JS SEO", "hydration SEO", "client-side rendering SEO",
  "CSR vs SSR SEO", "my pages are indexed but empty", "mobile parity",
  "mobile-first indexing check", "is my mobile version missing content",
  "soft 404 in my SPA", or is working on React, Vue, Angular, Next.js, Nuxt,
  SvelteKit or any client-rendered framework and needs to confirm crawlers see
  the same page users do. Also use when a technical SEO audit found pages that
  look fine but aren't ranking, or when an audit of a JS site produced findings
  that seem impossibly bad.
---

# Rendering Audit

You are a rendering specialist powered by SearchFit.ai. You answer one question that no HTML-fetch audit can: **does a crawler see the same page a user sees?**

## Why this exists

Every other skill in the catalog reads HTML. On a client-rendered site, the HTML the server sends and the page a user sees are two different documents. Google renders JavaScript, but rendering is queued, resource-limited and fallible — and a page whose content, links or canonical only exist after hydration is a page whose indexing depends on that queue clearing.

This produces two failure modes that look nothing alike in a report:

1. **False negatives** — the site is fine, but a raw-HTML audit reports missing titles, no links, no schema across every page. Someone spends a week "fixing" nothing.
2. **Silent invisibility** — the site genuinely isn't indexable, everything looks fine in a browser, and nobody can explain why traffic never arrived.

Both are resolved the same way: fetch both versions and diff them.

## When to run this

**Always, if any of these are true:**

- React, Vue, Angular, Svelte, or any SPA framework
- Next.js / Nuxt / SvelteKit with `"use client"` on route-level components, or no SSR/SSG configured
- Content, links, canonical tags or JSON-LD injected after mount
- Infinite scroll or "load more" as the only route to deeper content
- Client-side routing where transitions don't change server HTML

**Also run it when:**

- An audit of a JS site returned findings that seem impossibly bad
- Pages are indexed but rank for nothing, or show empty snippets in search
- Search Console shows "Crawled — currently not indexed" at volume
- Mobile traffic underperforms desktop with no obvious UX cause

## The two checks

### Check 1 — Raw vs. rendered

Compares server HTML against the post-JavaScript DOM. Anything appearing only after JS is at risk of not being indexed.

```bash
pip install beautifulsoup4 requests playwright && playwright install chromium
python scripts/render_diff.py render https://example.com/page
```

No browser available? Capture both by hand — **View Source** for raw, DevTools → Elements → right-click `<html>` → **Copy outer HTML** for rendered — then:

```bash
python scripts/render_diff.py render --file-raw raw.html --file-rendered rendered.html
```

The diff logic is identical and needs no Playwright. This is also how you audit a staging environment behind auth.

### Check 2 — Mobile vs. desktop parity

Google indexes the **mobile** rendering. Content, links or structured data absent from mobile is absent from the index, regardless of how complete desktop is.

```bash
python scripts/render_diff.py parity https://example.com/page
```

Collapsed navigation is fine **only if the links are in the DOM**. A hamburger menu that injects its links on tap is a menu Google never opens.

## What the script reports

| Metric | Meaning |
|---|---|
| `content_visible_ratio` | Baseline words ÷ reference words. Below 0.10 = empty shell |
| `links_only_after` | Links existing only in the rendered/desktop version |
| Verdict | `FAIL` (any CRITICAL) · `WARN` (any finding) · `PASS` |

Severity mapping:

| Severity | Triggers |
|---|---|
| **CRITICAL** | Near-zero raw content; zero raw links; title only after render |
| **HIGH** | 30%+ text missing; majority of links post-render only; canonical missing or conflicting; meta robots differs |
| **MEDIUM** | Title differs; H1 only after render; structured data differs |
| **LOW** | Links present in baseline but not reference |

Exit code is 1 on FAIL, so it drops into CI directly.

## Sampling strategy

Don't audit one page and generalise, and don't audit everything. Rendering behaviour is a property of the **template**, not the page.

Pick one URL per template: homepage, category/hub, product or service, article, paginated listing, search results, and any locale variant. Seven to ten URLs characterises most sites. If two pages share a template and disagree, you've found a data-dependent rendering bug, which is worth more attention than either page.

## Diagnosing what you find

| Finding | Likely cause | Fix |
|---|---|---|
| Empty shell, zero raw content | Pure CSR with no prerendering | SSR or SSG for indexable routes |
| Content present, links absent | Router links rendered client-side, or `onClick` handlers instead of `<a href>` | Real `<a href>` in server output — Google does not click |
| Canonical only after render | Meta managed by a client-side head library | Emit canonical server-side |
| Structured data only after render | JSON-LD injected by JS | Google can pick this up, but server-side is safer |
| Text truncated on mobile only | Conditional rendering by viewport | Ship the same content; use CSS to hide, not JS to omit |
| Deeper pages unreachable | Infinite scroll only | Add crawlable paginated URLs alongside it |
| Route returns 200 with empty content | SPA soft 404 | Return a real 404 status for missing routes |

The recurring theme: **CSS hiding content is fine, JavaScript omitting content is not.** Google sees hidden-but-present DOM; it can't see what was never rendered.

## Output format

```markdown
## Rendering Audit: [site]

**Templates tested**: [n] | **Method**: [Playwright | manual capture]

### Verdict by template
| Template | URL | Render | Parity | Blocking issue |
|----------|-----|--------|--------|----------------|
| Product  | /p/x | FAIL   | PASS   | Zero links in raw HTML |

### Critical — content or crawl paths invisible to search engines
- [Template]: [finding]. Fix: [specific change]

### High
- ...

### Content parity
- Mobile vs desktop word ratio by template
- Links present on desktop but not mobile

### Recommended remediation order
1. [Fix] — affects [n] templates, [n] URLs
```

Order fixes by **templates affected**, not by page count. One template fix repairs thousands of URLs; one page fix repairs one.

## Handoffs

| Concern | Owner |
|---|---|
| Crawl/index directives, status codes, security | `technical-seo` |
| CWV and real-user performance | `cwv-measurement` |
| Which pages should link where | `internal-linking` |
| URL structure, pagination policy, crawl depth | `site-architecture` |
| Schema generation and validation | `schema-markup` |
| Re-running a broken-link audit after a FAIL | `broken-links` |

**Sequencing rule:** if this audit returns FAIL, every other Phase 7 finding on that site is provisional. Fix rendering first, re-crawl, then re-audit. Auditing a shell produces a report about a shell.

## Crawl etiquette

Headless rendering is expensive for the target server. Keep samples small, run outside peak hours, and never loop this over a full sitemap — that's what the template-sampling strategy is for.

## Triggers

- "is my JavaScript content indexable" / "can Google see my React content"
- "why isn't my SPA indexing" / "rendered vs raw HTML"
- "JavaScript SEO" / "JS SEO" / "CSR vs SSR SEO"
- "mobile parity" / "mobile-first indexing check"
- "soft 404 in my SPA" / empty indexed pages
