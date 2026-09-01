# Evidence base — what's actually established vs. what's folklore

Read this before making architecture recommendations. Site architecture is one of the most myth-heavy areas in SEO.

Sources are primary where possible (Google Search Central / Crawling Infrastructure docs), and attributed practitioner commentary otherwise.

---

## Established by Google documentation

### URL structure requirements (hard)
Follow IETF STD 66; percent-encode reserved characters; don't use URL **fragments** to change page content; use `=` for key-value pairs and `&` between parameters.
→ https://developers.google.com/search/docs/crawling-indexing/url-structure

### URL structure recommendations (soft)
Descriptive readable words; audience language; **hyphens rather than underscores**; few parameters; **case-sensitive** URLs — normalise to lowercase.

### What actually causes URL problems
Not parameters per se — **combinatorial explosion** from additive filtering, session IDs, infinite calendars, broken relative links.

### Faceted navigation
- Don't need indexed → disallow parameter patterns in robots.txt, or use **URL fragments**
- Do need indexed → `&` separators, fixed filter order, real **404** for empty combinations
- `rel=canonical` / `nofollow` are generally less effective long-term than blocking
→ https://developers.google.com/crawling/docs/faceted-navigation

### Site moves with URL changes
Server-side 301/308; prefer **direct to final destination**; don't mass-redirect to homepage; keep redirects **≥1 year**; change one thing at a time; expect temporary fluctuation; **301s don't cause PageRank loss**.
→ https://developers.google.com/search/docs/crawling-indexing/site-move-with-url-changes

---

## Directly contradicted by Google — do not repeat

### "Keep URLs within three folders"
Google does not count slashes. **What replaces it: click depth** (link distance from homepage).

### "Google doesn't like query parameters / clean URLs rank better"
**False** as a crawling requirement. Clean URLs still help readability, duplication control, analytics.
→ https://developers.google.com/crawling/docs/myths-about-crawling

### "Crawling more = ranking better"
**False.** Don't sell architecture changes on crawl budget → rankings.

---

## Genuinely contested — judgement calls

### Subdomain vs. subfolder
Google: both fine. Prefer subfolders for **operational** reasons (GSC, analytics, linking, one CMS).

### Strict siloing
Avoid isolation rules that block related cross-links. Prefer **hub-and-spoke** with cross-cluster linking where relevant.

### Click-depth thresholds
≤3 clicks is a practitioner convention, not a Google rule. Scale by site size.

---

## Client reframe

> "Folder depth isn't the thing to optimise; Google doesn't count slashes. What we should measure is how many clicks it takes to reach these pages from the homepage."

---

## Shared content / cluster evidence

Pillar/hub/spoke and "topical authority" language: use the canonical shared document —
[`../references/google-helpful-content.md`](../references/google-helpful-content.md)
(Parts B1–B5). Structural mechanics (URL, depth, breadcrumb) stay in this skill;
content-coverage reasoning for clusters points there. Never claim "Google requires
pillar pages."
