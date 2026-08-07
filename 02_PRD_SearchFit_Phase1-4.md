Product Requirements Document (PRD)

SearchFit Onboarding & Audit Agent — Phase 1–4

Version: 1.0
Date: August 4, 2026
Owner: Traffic Radius — Head of SEO / Director of AI & Automation
Source material: SearchFit Skill Coverage Matrix, TR SEO Agentic Architecture v1.5/v1.6

---

1. Problem Statement

Traffic Radius's SearchFit skill catalog is strong from keyword research through publishing (Phases 5–12 of the 17-phase SEO workflow are Full or near-Full coverage), but the four phases that start every engagement — Business & Project Discovery, Access/Tracking/Data Collection, Website Situation Analysis, and Competitor & Market Analysis — are the weakest-covered part of the entire workflow. Phase 1 and Phase 2 have no skill at all today; Phase 3 and Phase 4 are only partially covered. In practice this means every new client engagement starts with manual questionnaires, spreadsheets, and one-off audits before an agent ever touches the account — the exact bottleneck the rest of SearchFit was built to remove.

2. Product Vision

A single chat-based agentic application that a Traffic Radius team member opens with a new client and walks through, conversationally, until a complete, human-approved Client Digital Profile exists — covering business context, verified tracking, current site health, and the competitive landscape. That profile becomes the single input every later SearchFit skill (Phase 5 onward) reads from, instead of a form nobody revisits after onboarding.

3. Goals & Success Metrics

| Goal | Metric | Target |
|---|---|---|
| Cut onboarding time | Active hours from kickoff to "ready for Phase 5" | < 2 hours of human time (down from multi-day manual process) |
| Improve profile quality | % of Client Digital Profiles with a completeness/readiness score ≥ 90 before Phase 5 begins | 100% (hard gate) |
| Reduce rework | # of Phase 5+ engagements blocked or restarted due to missing/wrong discovery data | Reduce to near zero |
| Close the skill gap | Phases 1–4 status on the coverage matrix | Move from Gap/Gap/Partial/Partial to Full/Full/Full/Full |
| Trust in automation | % of AI-drafted findings approved without edits by the reviewing specialist | Track as a leading indicator, not a target to maximize (edits are expected and healthy) |

4. In-Scope Phases (this build)

1. Business & Project Discovery
2. Access, Tracking & Data Collection
3. Current Website Situation Analysis
4. Competitor & Market Analysis

Explicitly out of scope: Phases 5–17 (keyword research through continuous improvement). This PRD ends at the point where a client's profile is marked ready to hand off to keyword research.

5. Users & Roles

| Role | Primary use in this build |
|---|---|
| Client Success Manager | Runs the Discovery conversation with the client; signs off on the discovery profile (step D4) |
| Technical SEO Specialist | Runs Tracking & Access setup/verification; runs the Website Situation audit; reviews both before approval |
| SEO Strategist | Runs Competitor & Market Analysis; uses discovery + website findings as positioning input |
| SEO QA / Reporting Lead | Final readiness-gate reviewer — checks the aggregate completeness score across all four phases before the profile unlocks Phase 5 |
| Client (external) | Answers the targeted questionnaire (Phase 1, step D2) via a client-facing form; does not access internal chat or other phases |

Role-permission enforcement follows the same "no role can trigger a skill outside its own lane" principle as TR's existing architecture (see Backend Schema doc, `role_permissions` table).

6. Feature Requirements by Phase

6.1 Phase 1 — Business & Project Discovery (New skill: Discovery Agent)

Closes a full Gap in the current skill catalog. Adapts the D1–D5 workflow already defined in TR's architecture v1.6.

| # | Requirement | Acceptance criteria |
|---|---|---|
| 1.1 | Automated pre-research | Given a client name/URL, the agent researches public footprint (website, Google Business Profile, socials, reviews, visible competitors) and drafts a first-pass business model, product list, and positioning summary — before any question is asked of the client |
| 1.2 | Pre-filled client questionnaire | Client answers only what only they know (revenue split, AOV, sales cycle, seasonality, objectives as checkboxes not free text, compliance constraints, other marketing spend); all fields pre-filled from 1.1 where possible |
| 1.3 | Completeness scoring | Assembled profile scored against the weighted-readiness model; any missing/low-confidence field is flagged by name, not left silently blank |
| 1.4 | Human sign-off | Client Success Manager reviews before finalization; the agent explicitly surfaces discrepancies between client claims and independent research (e.g., "premium" positioning claim vs. price-sensitive review sentiment) as insight, not noise |
| 1.5 | Publish to Client Digital Profile | Output writes to the Commercial Scope and Marketing Context sections of the Client Digital Profile — the single record every later phase reads |

6.2 Phase 2 — Access, Tracking & Data Collection (New skill: Tracking & Access Agent)

Closes a full Gap in the current skill catalog.

| # | Requirement | Acceptance criteria |
|---|---|---|
| 2.1 | Guided access request | Agent walks the client/specialist through granting read access to GA4, Search Console, and GTM via OAuth, tracking which access is granted vs. outstanding |
| 2.2 | Tag/tracking validation | Agent verifies GA4 base tag and key conversion events are actually firing (not just configured), and that GTM container versions match what's live on the site |
| 2.3 | Data quality check | Flags obvious tracking problems: duplicate event firing, missing conversion events, bot-inflated traffic, broken cross-domain tracking |
| 2.4 | Tracking health report | Produces a structured pass/fail/warning report per tracking element, with plain-language fix instructions for anything failing |
| 2.5 | Human review | Technical SEO Specialist reviews and either approves the tracking baseline or flags it for client-side remediation before Phase 3 can rely on the data |

6.3 Phase 3 — Current Website Situation Analysis (Extends existing "SEO Audit" skill)

Currently Partial: crawl-based technical analysis exists; backlink/authority review and traffic-drop diagnosis do not.

| # | Requirement | Acceptance criteria |
|---|---|---|
| 3.1 | Site crawl (existing capability, retained) | Full crawl for indexation, redirects, canonicals, basic technical health |
| 3.2 | Backlink & authority review (new) | Pulls backlink profile from Ahrefs/Moz, scores link quality/spam risk, surfaces top referring domains and anchor text distribution |
| 3.3 | Traffic-drop / anomaly diagnosis (new) | Using verified GA4/GSC data from Phase 2, detects significant trend breaks or drops, attempts to correlate them with crawl-detected site changes (e.g., a redirect chain introduced around the same date) |
| 3.4 | Unified situation report | Combines 3.1–3.3 into one report: technical health, authority position, and any unresolved traffic anomalies |
| 3.5 | Human review | Technical SEO Specialist approves or annotates before it's written to the profile |

6.4 Phase 4 — Competitor & Market Analysis (Extends existing "Competitor Analyzer" skill)

Currently Partial: keyword gap analysis exists; the skill cannot measure rankings itself (needs GSC/Ahrefs exports) and competitor backlinks/PR are not covered.

| # | Requirement | Acceptance criteria |
|---|---|---|
| 4.1 | Competitor identification | Confirms/expands the competitor set surfaced in Discovery (1.1) using search visibility and category overlap |
| 4.2 | Keyword gap analysis (existing capability, retained) | Identifies keyword opportunities competitors rank for and the client doesn't |
| 4.3 | Ranking data ingestion (new) | Pulls actual ranking positions from GSC/Ahrefs exports rather than relying on the LLM to estimate them |
| 4.4 | Competitor backlink/authority comparison (new) | Compares competitor backlink profiles against the client's (from 3.2) to identify authority gaps |
| 4.5 | Positioning/content clustering (new) | Groups competitors by actual content strategy (via embedding similarity, not just domain guesswork) to identify which competitors are true strategic peers |
| 4.6 | Market analysis report | Combines 4.1–4.5 into a structured competitive-landscape report |
| 4.7 | Human review | SEO Strategist approves before it's written to the profile |

7. Cross-Cutting Requirements

- Confidence-based staging: every finding from all four agents carries a confidence score; high-confidence findings are staged for review, medium/low-confidence findings are explicitly flagged for closer review — nothing is ever auto-applied regardless of score.
- Readiness gate: the SEO QA/Reporting Lead reviews an aggregate completeness score (weighted across all four phases) before a client profile is marked "ready for Phase 5." A profile cannot silently sit half-complete.
- Full audit trail: every chat turn, external API call, and human decision is logged against the client and the requester, matching TR's "everything is traceable" principle.
- Chat-first: all four agents are used conversationally, in one chat interface, not as four separate tools a user has to open individually — see UI/UX Design doc.

8. Out of Scope (explicit)

- Any content generation, drafting, or publishing (Phases 5–12).
- Any live CMS write access.
- Local SEO / Google Business Profile management (Phase 13).
- Link-building outreach execution (Phase 14) — Phase 4 analyzes competitor authority but does not initiate outreach.
- CRO/UX testing (Phase 15).
- Recurring/scheduled reporting cadence (Phase 16) beyond the one-time Phase 1–4 findings reports.

9. Assumptions & Dependencies

- Client is willing to grant OAuth read access to GA4, Search Console, and GTM (Phase 2 depends on this; the agent must degrade gracefully — flagging "unverified" rather than blocking — if access isn't granted immediately).
- Traffic Radius holds an active Ahrefs or Moz API subscription with sufficient quota for backlink and ranking pulls.
- The existing SearchFit "SEO Audit" and "Competitor Analyzer" skills' current logic is reusable/extendable rather than needing a full rewrite (confirmed feasible per TRD Section 4).

10. Milestones (Phase 1–4 MVP)

| Milestone | Scope |
|---|---|
| M1 | Discovery Agent live (Phase 1 complete, end-to-end with client questionnaire) |
| M2 | Tracking & Access Agent live (Phase 2 complete) |
| M3 | Website Situation Agent extended (Phase 3 complete, backlink + anomaly detection added) |
| M4 | Competitor & Market Agent extended (Phase 4 complete, ranking ingestion + backlink comparison + clustering added) |
| M5 | Readiness gate + full Client Digital Profile handoff wired end-to-end; first live client run through all four phases in one chat session |

11. Risks

| Risk | Mitigation |
|---|---|
| Client delays OAuth access grants, stalling Phase 2 | Agent proceeds with available data, marks tracking baseline "unverified," and re-checks on a schedule rather than blocking the whole pipeline |
| Ahrefs/Moz rate limits or cost overrun on backlink pulls | Cache competitor and backlink results per client for a configurable window (default 14 days); rely on TRD's provider fallback pattern |
| LLM misjudges business context in automated pre-research (1.1) | Every discovery finding is explicitly a draft until the Client Success Manager sign-off (step D4); discrepancies are surfaced, not silently resolved |
| Anomaly/ML models produce false positives on traffic drops | ML outputs are advisory pre-triage only; a human specialist always reviews before anything is written to the profile |
