---
name: website-situation-agent
description: Run Phase 3 Current Website Situation Analysis for a Radius OS client — technical crawl audit, backlink/authority review, and traffic-drop anomaly diagnosis, combined into one unified situation report published to the Client Digital Profile. Use when the user says "run website situation analysis", "crawl the site and run a technical audit", "check backlinks and anomalies", or wants a current health check on a client's website.
---

# Website Situation Agent — Phase 3: Current Website Situation Analysis

## Purpose

Extends Radius OS's existing "SEO Audit" skill, which today only covers crawl-based technical analysis. This skill closes the two gaps flagged in the coverage matrix — backlink/authority review and traffic-drop diagnosis — and combines all three into one situation report. It depends on Phase 2's verified tracking baseline: traffic-anomaly detection must use tracking data that's been marked `pass` or `warning`, never `unverified` or `fail` data, or the anomaly finding itself can't be trusted.

This skill is read-only against the client's site and data sources. It never modifies anything live.

## When to use this skill

Trigger on any of:
- "Run website situation analysis" → run all three sub-steps (crawl, backlink, anomaly)
- "Crawl the site and run a technical audit" → run only the crawl sub-step
- "Check backlinks and anomalies" → run only the backlink and anomaly sub-steps (skip crawl if a recent crawl already exists)
- Phase 2 (Tracking & Access) has just completed for a client and Phase 3 hasn't started

Before running the anomaly sub-step, check `client_digital_profiles.tracking_baseline`. If `tracking_status` is not `complete`, still run the crawl and backlink sub-steps, but skip anomaly detection and state plainly that it requires a verified tracking baseline first — don't run it against unverified data and present the result as reliable.

## Role requirements

- Any permitted role may trigger this skill.
- Only `technical_seo_specialist` may approve, edit, or reject the situation report. If another role reaches the review step, present it read-only.

## Workflow

### 1. Technical crawl (existing capability, retained)

1. Enqueue an async site crawl job — this can take minutes on larger sites, so report progress in the chat rather than leaving the user waiting on a blocking call.
2. Check indexation status, redirect chains, canonical tag correctness, and basic technical health (broken links, status codes, duplicate content signals).
3. Write findings to `website_audits` with `audit_type = crawl_technical`, `severity` set per finding (`info` / `warning` / `critical`).
4. Record the crawl date — the anomaly sub-step (Section 3) needs this to correlate site changes with traffic shifts.

### 2. Backlink & authority review (new)

1. Pull the client's backlink profile from the primary provider (Ahrefs); on failure, retry with backoff, then call the secondary provider (Moz) directly — never surface a raw provider error to the user.
2. Record referring domain count, authority score, and top anchor-text distribution.
3. Run each backlink batch through the spam-risk classifier to pre-triage which links a human should look at first — this is advisory pre-triage only, not an automatic disavow or exclusion.
4. Write the raw snapshot to `backlink_snapshots` (client-owned row) and a summarized finding to `website_audits` with `audit_type = backlink_summary`.

### 3. Traffic-drop / anomaly diagnosis (new)

1. Confirm the tracking baseline is usable (see role/trigger note above). If not, skip this step and say why.
2. Pull the client's GA4/GSC time series and run trend decomposition plus change-point detection to identify statistically significant breaks or drops — not routine day-to-day noise.
3. For each detected anomaly, check whether its date correlates with a site change surfaced in the crawl (Section 1) — e.g., a redirect chain introduced or a canonical change around the same date. State the correlation as a hypothesis to verify, not a confirmed cause.
4. Write findings to `website_audits` with `audit_type = traffic_anomaly`, including the detected date range and any correlated crawl finding.
5. If no anomalies are found, say so explicitly — an empty anomaly list is a real (positive) finding, not something to omit from the report.

### 4. Unified situation report

Combine all `website_audits` rows for this client's current audit run into one report with three sections: Technical, Authority, Anomalies. Do not let one section dominate — a critical anomaly and a minor technical warning should both be visible, not buried under whichever ran first.

### 5. Human review

1. Present Approve / Edit / Reject to `technical_seo_specialist`.
2. On **Approve** or **Edit**: write the combined summary into `client_digital_profiles.website_situation_summary`, set `website_status = complete`, log `finding_approved` (or `finding_edited`) to `audit_trail`.
3. On **Reject**: return to the relevant sub-step for re-run with the reviewer's note; `website_status` stays `in_progress`.
4. Recompute `overall_readiness_score` after the outcome.

## Output format

Render the unified Audit Summary card with three tabs — Technical / Authority / Anomalies — per the UI/UX spec:
- **Technical tab:** crawl findings, severity badges.
- **Authority tab:** referring domains, authority score, anchor-text breakdown, spam-risk pre-triage flagged links.
- **Anomalies tab:** trend sparkline, detected change-point date(s), any correlated crawl finding, or an explicit "no anomalies detected" state.

If only a subset of sub-steps ran (e.g., "crawl the site and run a technical audit" only), render only that tab as populated and mark the others "not run this session" rather than showing them empty or omitting them silently.

## Guardrails

- Never run anomaly detection against an unverified or failing tracking baseline — skip it and say why instead of producing an unreliable finding.
- Never present a spam-risk or backlink-quality score as a final judgment — it's pre-triage for the human reviewer, never an automatic disavow or removal action.
- Never state a causal link between a crawl change and a traffic anomaly as fact — correlation only, flagged for the reviewer to confirm.
- Never silently drop a provider failure — if Ahrefs fails and Moz also fails, say backlink data is unavailable this run rather than omitting the Authority tab without explanation.
- Nothing writes to `client_digital_profiles` outside of an explicit Technical SEO Specialist approval or edit.

## Example interaction

```
User: Run website situation analysis

Agent: Starting site crawl for acme-retail.com — this will take a few
minutes. I'll pull backlink data and check for traffic anomalies in
parallel.
[system notice: crawl in progress, 340 pages found...]

Crawl complete: 2 critical issues (redirect chain on /category/sale,
missing canonical on 14 product pages), 6 warnings.

Backlink review: 1,240 referring domains, authority score 42/100. 18
links flagged for spam-risk review — mostly low-quality directory
listings from 2019.

Checking for traffic anomalies using verified tracking data...
Detected a 22% organic traffic drop starting March 3 — this lines up
with the canonical issue on the 14 product pages found in the crawl.
Worth confirming that's the cause before prioritizing the fix.

[renders unified Audit Summary card, three tabs]

User: [Technical SEO Specialist approves with a note on the redirect
chain priority]

Agent: Website situation report approved and published to Acme Retail's
Client Digital Profile. Website phase is now complete.
```
