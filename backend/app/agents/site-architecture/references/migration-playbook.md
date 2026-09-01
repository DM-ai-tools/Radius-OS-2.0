# Migration / restructure playbook

Non-negotiables:

- **Server-side 301/308**, one hop to final destination; never chain when avoidable
- **Never mass-redirect old URLs to the homepage** (soft-404 risk)
- **Change one thing at a time** — structure, CMS, redesign are separate releases
- **Keep redirects ≥ 1 year**, ideally indefinitely
- **Large sites move in sections**; small/mid sites can move all at once
- Expect ranking fluctuation for weeks; provision extra crawl capacity
- Search Console **Change of Address** for domain moves (not needed for HTTP→HTTPS)
- Self-referencing canonicals + updated hreflang on new URLs; strip staging `noindex`
- Define a **rollback trigger**; validate with crawls pre-launch and at 24h / 7d / 30d

## High-risk — require human sign-off
- Touching >20% of indexed URLs
- Domain or protocol change
- Restructure of any top-10 revenue page

## Validation checkpoints
1. Pre-launch crawl of staging vs redirect map completeness
2. 24h: 404 spike, redirect chains, canonical loops
3. 7d: index coverage, money-page rankings
4. 30d: redirect hit counts, remaining soft-404s
