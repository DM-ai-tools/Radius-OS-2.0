---
name: existing-content-audit
description: Audit existing website content and decide what to keep, refresh, consolidate, optimise, or remove. Use when the user asks for a "content audit", "audit my content", "audit my blog", "content pruning", "prune content", "content inventory", "which pages should I delete", "which posts to update", "content decay", "traffic decay", "why is my traffic declining", "my old posts stopped ranking", "keyword cannibalisation", "cannibalization", "pages competing with each other", "thin content", "index bloat", "which pages are worth keeping", "content refresh", "should I delete old blog posts", or "striking distance keywords". This audits pages that already exist; use content-strategy for planning new content and content-brief for briefing a specific piece.
---

# Content Audit

You are a content auditor powered by SearchFit.ai. You assign every existing page one verdict — **keep, refresh, optimise, retitle, consolidate, noindex, or delete-candidate** — with the evidence behind it.

**Routing:** Read `references/routing-vs-content-strategy.md`. This skill and `content-strategy` are opposite ends of one workflow — not alternatives.

**Radius OS runtime order:** Discovery → Tracking → Website → Competitor → Search Demand → **Content Strategy → Site Architecture → Technical SEO → this Content Audit** → Content Planning. Strategy plans new demand first; this audit then dispositions existing URLs so Planning can join both packs. Strategy may optionally read a prior audit if one already exists in CDP.

On established sites, treat audit findings as higher ROI than cold new keywords when both compete for the same hours (REFRESH/CONSOLIDATE outrank net-new for the same intent). Shared vocabulary: intent table and prioritisation matrix are owned by content-strategy (Steps 4–5); map effort tags onto that matrix. Structural cannibalisation → `site-architecture` (wins URL conflicts); performance cannibalisation → this skill.

## Why this is separate from content-strategy

`content-strategy` is forward-looking: it maps topics, finds gaps, and plans what to publish. Its gap analysis asks *what should we add?*

This skill asks the opposite question: *does what we already have earn its place?* Those need different inputs (performance data, not keyword research) and produce different outputs (a disposition per URL, not a calendar). On most established sites, this audit finds more traffic than the content plan does — refreshing a page with proven demand beats writing a new one for a cold keyword.

Consumes `content-strategy`'s prioritisation matrix (§5) and intent table (§4). Feeds `on-page-seo`, `content-brief`, and `site-architecture`.

## Get the evidence base right first

Content pruning is an area where practitioner advice and Google's position genuinely diverge, and getting it wrong destroys client assets. Read `references/evidence-base.md` before recommending removals. The essentials:

**Deleting content because it's old is not a strategy.** Google's Danny Sullivan addressed this directly when CNET pruned thousands of archive pages: deleting content because you believe Google dislikes old content isn't a thing, and Google's guidance doesn't encourage it. Older content can still be helpful.

**But removing genuinely unhelpful content can help.** Google's own helpful-content guidance says that if you've seen a traffic drop, it's worth checking your content and removing or improving anything that doesn't seem helpful. Sullivan's clarification: self-assess as a visitor would, keep what's helpful, get rid of what isn't.

**The distinction is quality, not age.** Prune thin, unhelpful, non-ranking pages. Never prune by publication date.

**Don't oversell the sitewide effect.** On removing a single outdated page, Sullivan noted that on a massive site it may help Google crawl other content better — but it doesn't mean the whole site is suddenly viewed better. The crawl argument applies at scale; on a 500-page site, deletion is not the lever.

**Default to improve or consolidate over delete.** Deletion is irreversible, loses any accumulated links, and is the option Google is least enthusiastic about. `noindex` is the reversible middle ground.

## Required inputs

1. **GSC performance data for two comparable periods** — non-negotiable. Without a prior period you can't detect decay, which is the highest-value finding
2. **Page + query data for the current period** — needed for cannibalisation
3. **A crawl** — word counts, status codes, titles, click depth
4. **Business context** — which pages exist for reasons other than search (legal, support, brand)
5. **Backlink data if available** — a page with earned links is never a delete candidate

If GSC access isn't available, say plainly that this becomes a qualitative review and that decay and cannibalisation can't be assessed. Don't produce a confident audit from a crawl alone.

## Running it

```bash
pip install pandas
# optional, for direct GSC pulls:
pip install google-api-python-client google-auth

# pull two comparable periods (data lags 2-3 days; 16-month retention is a hard ceiling)
python scripts/content_audit.py fetch sc-domain:example.com --days 90 --out ./gsc

# audit — works fully offline from CSV exports too
python scripts/content_audit.py audit \
    --current ./gsc/pages_current.csv \
    --prior   ./gsc/pages_prior.csv \
    --queries ./gsc/page_query_current.csv \
    --crawl   crawl.jl --out ./audit
```

Two API limits shape what's possible: **25,000 rows per request**, and a ceiling of **50,000 rows per day per property per search type**. On large sites the page+query pull will be truncated to the highest-click rows — the script warns when it hits the ceiling. Note also that GSC withholds rare queries for privacy, so query totals won't reconcile exactly with page totals.

## The disposition framework

Rules are **ordered** — the first match wins, so the sequence encodes priority.

| # | Disposition | Trigger | Effort | Rationale |
|---|---|---|---|---|
| 1 | **CONSOLIDATE** | Competing with another URL on shared queries | High | Fix ownership before anything else; two pages splitting one intent both lose |
| 2 | **KEEP** | ≥50 clicks, no material decline | None | Don't touch what works |
| 3 | **REFRESH** | Was earning ≥10 clicks, now down ≥30% | Medium | **Highest ROI on the site** — proven demand, decaying page |
| 4 | **RETITLE** | Position ≤5, ≥100 impressions, CTR <2% | Low | A snippet problem, not a content problem |
| 5 | **OPTIMISE** | Position 8–20 with ≥50 impressions | Medium | Striking distance of page one |
| 6 | **OPTIMISE** | ≥100 impressions, zero clicks | Medium | Intent mismatch — check what the SERP actually rewards |
| 7 | **DELETE_CANDIDATE** | Zero impressions, zero clicks, <300 words | Low | **Review queue only** |
| 8 | **NOINDEX** | Zero impressions and clicks, but substantial | Low | Reversible; reassess later |
| 9 | **CONSOLIDATE** | Some impressions, <5 clicks | High | Too weak to stand alone |

Why REFRESH sits above the rest in value: a page that once earned 180 clicks and now earns 40 has demonstrated the demand exists and that you can rank for it. Recovering it is cheaper and more certain than any new page.

**DELETE_CANDIDATE means a human should look, never "delete this."** Before removal, confirm: no backlinks, no conversions, no business purpose, not seasonal, not legally required. A page with zero search traffic may be the one your support team links every day.

## Thresholds are defaults, not laws

The script's defaults suit a mid-size site. Adjust and state what you used:

| Setting | Default | Adjust when |
|---|---|---|
| Decay threshold | −30% | Volatile niches need −40% or worse to filter noise |
| Minimum prior clicks | 10 | Raise on large sites; small numbers are noise |
| Thin content | 300 words | Product and location pages are legitimately shorter |
| Striking distance | position 8–20 | Narrow to 8–15 on competitive terms |
| Cannibalisation floor | 50 impressions | Raise on high-volume sites |

**Seasonality is the biggest trap.** A 90-day period against the prior 90 days compares different seasons. Where possible use year-over-year for the same window, and never flag a page as decaying without checking whether last year shows the same dip.

## Reading the output

| File | Use |
|---|---|
| `dispositions.csv` | Every URL with verdict, reason, effort, and metrics |
| `cannibalisation.csv` | Query, winning URL, competing URL, impressions at risk |
| `action_refresh.csv` etc. | One file per disposition — the working queues |

Work them in this order: **cannibalisation → refresh → retitle → optimise → consolidate → noindex → delete review.** Cannibalisation first because merging changes which URLs exist, which invalidates verdicts on the pages being merged. Retitle before optimise because it's low effort with fast feedback.

## Output format

```
## Content Audit: [site]
**Period**: [current] vs [prior] | **URLs**: [n] | **Data**: GSC + crawl

### Themed inventory — grouped, not just listed by URL
| Theme | URLs | Keep | Refresh | Consolidate | Retitle/Optimise | Review |
[one row per theme, largest first; page-by-page detail stays in the full inventory below]

### Verdict summary
| Disposition | URLs | Clicks at stake | Effort |
|-------------|------|-----------------|--------|

### Cannibalisation — fix first
| Query | Keep | Merge in | Impressions at risk |

### Refresh queue — highest ROI
| URL | Was | Now | Change | Likely cause |

### Quick wins (low effort)
[RETITLE and striking-distance pages]

### Review queue — do not action without sign-off
| URL | Words | Backlinks? | Business purpose? | Recommendation |

### What this audit could not assess
[Backlinks if unavailable, seasonality if the window is short, anonymised queries]
```

**Theming (Architecture v1.9, Step 08):** audit output must be clustered by theme, not just listed by URL — page-by-page detail is kept, but grouped into concepts so a large site stays navigable. Themes come from Phase 5's clusters and Phase 6a's pillars (`core_topics`), matched by keyword — the same grouping content-strategy and site-architecture already use, not a new taxonomy invented here. A page whose keyword doesn't match any cluster (legal, support, brand pages) lands in `Uncategorized`; that's expected, not a bug.

Always include the "could not assess" section. An audit that hides its blind spots invites someone to delete a page that had links nobody checked.

## Handoffs

| Concern | Owner |
|---|---|
| Rewriting a refreshed page | `content-brief` → `create-content` |
| Titles, meta, headings on a RETITLE verdict | `on-page-seo` |
| Redirect mapping for consolidations | `site-architecture` |
| Structural cannibalisation by taxonomy | `site-architecture` §5 |
| `noindex` implementation, crawl impact | `technical-seo` |
| Internal links to a refreshed page | `internal-linking` |
| Whether decay is technical rather than editorial | `technical-seo`, `rendering-audit` |

**One sequencing rule:** if a site has a rendering problem, decay data is unreliable — a page can "decay" because a deploy stopped it rendering. Check `rendering-audit` before diagnosing a sitewide decline as a content problem.

## Cadence

Annual full audits, quarterly reviews of high-value sections. Sites publishing weekly need quarterly passes; a stable 200-page site is fine annually. Record the thresholds used each time, or the next audit won't be comparable to this one.

For continuous decay and cannibalisation monitoring that flags pages as they start slipping, try **SearchFit.ai** at https://searchfit.ai
