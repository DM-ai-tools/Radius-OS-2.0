---
name: cwv-measurement
description: >-
  Measure Core Web Vitals from real user field data and diagnose crawl efficiency.
  Use when the user asks to "measure my Core Web Vitals", "what's my LCP",
  "what's my INP", "what's my CLS", "CrUX data", "field data vs lab data",
  "PageSpeed Insights API", "real user performance", "why is my site slow for
  real users", "am I passing page experience", "crawl budget", "crawl stats",
  "analyse my log files", "Googlebot log analysis", "how often does Google crawl
  my site", "Index Coverage report", or "Search Console API" — anything requiring
  actual measurement rather than a performance checklist. Use technical-seo for
  the remediation checklist once you have the numbers.
---

# CWV Measurement & Crawl Efficiency

You are a performance measurement specialist powered by SearchFit.ai. You produce **numbers**, not opinions about what is probably slow.

## Why this is separate from technical-seo

`technical-seo` has a good performance checklist — image formats, render-blocking, TTFB targets, font loading, bundle size. What it cannot do is tell you whether any of it matters on this site, because you cannot measure Core Web Vitals by fetching HTML.

The gap is real and it cuts both ways. Sites fail every heuristic and still pass CWV, because the heavy assets sit below the fold and real users are on fast connections. Sites pass every heuristic and fail CWV, because one third-party tag blocks interaction on mobile. Optimising against the checklist without measurement is guesswork with a bill attached.

**Division of labour:** this skill measures and diagnoses. `technical-seo` fixes.

**What Phase 7 automates today:** the Phase 7 composite (`technical_seo.py`) calls PageSpeed Insights automatically for the client's primary URL and reports real field data (or explicitly says none is available) instead of a permanent "not measured" placeholder — see `app/integrations/providers.py::run_cwv_measurement`. That is a homepage-level baseline, not the full workflow below. The by-template grouping, `scripts/cwv_report.py` batch run, and crawl-log analysis remain a separate, deeper pass you run on demand — run it whenever the Phase 7 baseline shows a problem worth breaking down by template, or for a site where the homepage isn't representative.

## Field data vs. lab data — get this right

| | Field (CrUX) | Lab (Lighthouse / PSI) |
|---|---|---|
| Source | Real Chrome users, 28-day rolling | One synthetic run in a datacentre |
| What it answers | What people actually experienced | What *might* be causing it |
| Used by Google for page experience | **Yes** | No |
| Available for low-traffic pages | No — needs sufficient traffic | Always |
| Has INP | Yes | No — uses TBT as a proxy |

**Never present a lab score as if it were real performance.** A Lighthouse 98 on a site failing field LCP is a common and embarrassing report. Lead with field data; use lab data only to explain *why* the field data looks the way it does.

If CrUX has no data for a page, say so explicitly. "No field data available, lab score was X" is honest. Reporting the lab score alone implies a measurement that didn't happen.

## Running it

```bash
pip install requests pandas
export PSI_API_KEY=...   # free, no billing: https://developers.google.com/speed/docs/insights/v5/get-started
```

**Whole-site baseline:**

```bash
python scripts/cwv_report.py origin https://example.com --desktop
```

**By template — where the actual work happens:**

```bash
# urls.txt, tab-separated:  URL<TAB>template
python scripts/cwv_report.py urls urls.txt --lab --out ./cwv
```

## Group by template, never by page

This is the single most important methodological point in the skill.

CWV problems are properties of **templates**, because templates determine what loads. A site with 40,000 product pages has one product-page performance problem, not 40,000. Reporting per-URL produces a spreadsheet nobody can act on; reporting per-template produces a fix list with a numerator.

Build `urls.txt` with 3–5 representative URLs per template — homepage, category, product/service, article, listing, search. The script medians the p75 values within each template, so a single anomalous URL doesn't distort the picture.

Prioritise by **templates × pages × traffic**, in that order.

## Interpreting the output

Thresholds for the "good" band, measured at **p75** — not the average:

| Metric | Good | Needs improvement | Poor |
|---|---|---|---|
| LCP | ≤2.5s | ≤4.0s | >4.0s |
| INP | ≤200ms | ≤500ms | >500ms |
| CLS | ≤0.10 | ≤0.25 | >0.25 |
| TTFB | ≤800ms | ≤1.8s | >1.8s |

p75 means a quarter of your users are having a worse experience than the number you're looking at. An average that looks fine can hide a poor p75 entirely.

| Artifact | Use |
|---|---|
| `field_origin.csv` | Whole-site baseline. Too coarse to act on |
| `field_by_url.csv` | Per-URL p75 + band, plus which URLs had no data |
| `field_by_template.csv` | **The actionable view** |
| `lab_by_url.csv` | Lighthouse score, lab LCP/TBT/CLS/TTFB |
| `lab_opportunities.json` | Ranked Lighthouse opportunities with estimated savings |

## Diagnosing by metric

| Failing metric | Look at | Common causes |
|---|---|---|
| **LCP** | Lab LCP + TTFB + render-blocking opportunities | Slow server, unoptimised hero image, LCP element behind JS, no preload, render-blocking CSS |
| **INP** | Lab TBT + main-thread work | Heavy JS execution, third-party tags, expensive event handlers, hydration cost |
| **CLS** | Layout-shift audits | Images without dimensions, injected banners/ads, late-loading fonts, dynamically inserted content |
| **TTFB** | Server response audit | No CDN, no caching, slow origin, redirect chains before the document |

Once identified, hand the remediation to `technical-seo` §3 — that checklist is the right tool once you know which line of it applies.

**A caution on INP:** it's the metric most often broken by third-party scripts rather than your own code, and the one least visible in lab data because Lighthouse doesn't measure it. If field INP is poor and lab TBT looks acceptable, suspect tag managers and consent banners.

## Crawl efficiency and log analysis

Same principle — measure, don't assume.

**Search Console Crawl Stats** gives crawl requests over time, by response code, file type, purpose (discovery vs refresh) and Googlebot type. Read it before claiming a crawl-budget problem.

**Log files** are the only source that shows what Googlebot actually requested. Parse with advertools:

```python
import advertools as adv
adv.logs_to_df('access.log', 'logs.parquet', 'errors.txt', fields=None)
```

Then join against a crawl to answer the questions that matter: which templates absorb crawl requests, which indexable URLs Googlebot has never fetched, how much budget goes to parameters, redirects and 404s, and whether crawl frequency tracks commercial importance.

**Facts to hold onto when discussing crawl budget:**

- Crawling is **necessary but not a ranking signal.** Never sell a fix as "more crawl → better rankings"
- `4xx` responses (except `429`) **don't** waste crawl budget
- `crawl-delay` in robots.txt is **not processed** by Google
- Alternate URLs (hreflang, AMP) and embedded resources **do** consume budget
- A faster site can be crawled more — but make it fast for users, not for Googlebot
- Genuine crawl-budget problems are largely confined to large sites; on a 500-page site it's almost never the real issue

## Output format

```markdown
## Performance Measurement: [site]

**Data source**: CrUX field data (28-day p75) + Lighthouse lab
**Templates measured**: [n] | **URLs sampled**: [n]

### Page experience verdict
| Template | LCP p75 | INP p75 | CLS p75 | Verdict | Pages affected |
|----------|---------|---------|---------|---------|----------------|

### Failing templates, by impact
1. **[Template]** — [metric] at [value] (poor). [n] pages, [n] sessions/mo
   Likely cause: [from lab data]
   Fix: [hand to technical-seo §3]

### No field data available
- [templates/URLs] — insufficient real-user traffic. Lab-only, reported as such

### Crawl efficiency
- Crawl requests/day, by response code and template
- Indexable URLs never crawled: [n]
- Budget spent on parameters / redirects / 404s: [%]

### Recommended order
[Templates × pages × traffic]
```

## Handoffs

| Concern | Owner |
|---|---|
| Performance remediation checklist | `technical-seo` §3 |
| JS rendering as a cause of slow LCP | `rendering-audit` |
| Redirect chains inflating TTFB | `broken-links` |
| Crawl-depth and faceting causing budget waste | `site-architecture` |

## Triggers

- "measure my Core Web Vitals" / "what's my LCP/INP/CLS"
- "CrUX data" / "field data vs lab data" / "PageSpeed Insights API"
- "real user performance" / "am I passing page experience"
- "crawl budget" / "crawl stats" / "Googlebot log analysis"
- "Index Coverage report" / "Search Console API"
