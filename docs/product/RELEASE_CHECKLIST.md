# Release checklist — Phases 1–12

Frozen 30 September 2026 against the agreed release (discovery through WordPress publish). Phases 13–17 stay out of scope.

| Stage | What ships | Where it lives | Status |
| --- | --- | --- | --- |
| 1 Discovery | Public-site draft, then human sign-off. A fetch or JSON failure returns an empty “needs review” profile. The crawler does not follow a guess onto another company’s domain. | `discovery.py`, `llm.unavailable_pre_research`, `web_fetch` same-domain variants | Shipped |
| 2 Tracking | GA4, Search Console, and Tag Manager. Production has no demo grant. Expired access without a refresh token is not “granted” until reconnect. | `oauth.py`, `tracking.py` | Shipped |
| 3 Website | Crawl, technical issues, and a backlink pull. A homepage estimate is labelled and is not stored as a Domain Rating. | `website.py`, `providers.pull_backlinks` | Shipped |
| 4 Competitors | Real business sites, scored, with directories and dead hosts dropped. Reports show the website next to the score. | `providers.discover_competitors`, `tier_competitors.py` | Shipped |
| Readiness | Phases 1–4 must be approved before keyword research. | `readiness.py` | Shipped |
| 5 Keywords | Volume comes from the provider response, including the Google Ads shape. No invented volume. Homepage authority estimates are not used as Domain Rating. | `dataforseo.search_volume`, `search_demand.py` | Shipped |
| 6 URL map and calendar | Priorities become URLs and a calendar. | `content_strategy.py`, `site_architecture.py` | Shipped |
| 7 Technical SEO | Indexation and crawl issues with severity. | `technical_seo.py` | Shipped |
| 8 Content audit | Keep / refresh / leave inventory. | `content_audit` services | Shipped |
| 9 Planning | Pillar, pillar blogs, sub-pillar, sub-pillar blogs. Existing pages that need work stay on the roadmap. Blogs need trend evidence. | `content_planning.py` | Shipped |
| 10 Draft and preview | One brief and draft, designed HTML download. | `content_production.py` | Shipped |
| 11 On-page | Title, headings, meta, internal links. Competitor brand names are not anchors. | `on_page_seo.py` | Shipped |
| 12 Publishing | Connected WordPress account. Default status is draft. Live publish stays off unless `WORDPRESS_ALLOW_LIVE_PUBLISH` is true. Policy blocks stay unpublished. | `publishing.py`, `wordpress.py` | Shipped |
| Approval | Empty runs and failed validation cannot be approved. The drafter cannot approve their own finding. | `review.py` | Shipped |
| Background jobs | Crawl, backlink pull, competitor scan, anomaly check, and tracking recheck run as real tasks. A stalled job is retried, then abandoned. | `background_jobs.py`, `tasks/jobs.py` | Shipped |

## Access checked on this machine (30 September 2026)

| Check | Result |
| --- | --- |
| Environment | `development` on this workstation. This is not a separate staging host. |
| Anthropic (configured model provider) | Key present. Model list returned HTTP 200. |
| OpenRouter | Key present, not the free tier. About $6.19 of $20 credits remaining. |
| DataForSEO | Live API (not sandbox). Account call returned status 20000. Balance about $13.53. |
| Ahrefs, SEMrush, Moz | No API key configured. Authority falls back to a labelled homepage estimate. Referring domains stay empty. |
| PageSpeed | No API key configured. |
| WordPress | `WORDPRESS_DEFAULT_STATUS=draft`. `WORDPRESS_ALLOW_LIVE_PUBLISH=false`. There is no global test site in config; a client connection is required before a draft can be written. |

Automated suite on 30 September 2026: CI lint (`ruff` F401, F811, F821, F841, E711, E712, E722) passed, and `pytest` passed 1925 tests.

## Still open — not part of this freeze

- Phases 13–17 (local SEO, outreach, CRO, recurring reports, continuous improvement).
- Ahrefs and SEMrush plans cannot be exercised until keys are configured.
- The nine adversarial domains from the validation ledger were never supplied, so those fixtures are not signed off.
- A named staging deployment and a WordPress test site are not configured here.
