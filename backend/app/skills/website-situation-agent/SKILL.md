---
name: website-situation-agent
description: Phase 3 Website Situation — technical crawl, backlink/authority, traffic-anomaly diagnosis into one unified report.
---

# Website Situation Agent — Phase 3

## Purpose

Combine crawl technical audit, backlink/authority review, and traffic-drop anomaly diagnosis. Read-only against the live site. Anomaly detection requires Phase 2 tracking baseline complete (pass/warning data only).

## Triggers

- "Run website situation analysis" → all three sub-steps
- "Crawl the site and run a technical audit" → crawl only
- "Check backlinks and anomalies" → backlink + anomaly (skip crawl if recent exists)

## Roles

- Any permitted role may trigger.
- Only `technical_seo_specialist` may Approve / Edit / Reject.

## Workflow

1. **Technical crawl** — progress notices; write `website_audits` audit_type=crawl_technical.
2. **Backlink & authority** — Ahrefs → Moz fallback; spam-risk pre-triage; `backlink_snapshots` + audit_type=backlink_summary.
3. **Traffic anomaly** — skip if tracking_status ≠ complete; change-point + crawl correlation as hypothesis only; audit_type=traffic_anomaly. Explicit "no anomalies" is a valid finding.
4. **Unified report** — three tabs: Technical / Authority / Anomalies. Unused tabs = "not run this session".
5. **Review** — Approve/Edit → website_situation_summary + website_status=complete; Reject → in_progress.

## Guardrails

- Never run anomaly against unverified/failing baseline.
- Never present spam-risk as final judgment.
- Never claim causal crawl→traffic link as fact.
- Never silently omit provider failures.
