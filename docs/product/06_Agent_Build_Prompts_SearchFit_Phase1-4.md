Agent Build Prompts

SearchFit Onboarding & Audit Agent — Phase 1–4 Build Playbook

Version: 1.0
Date: August 5, 2026
Use with: a coding agent (Claude Code, Cowork, or similar) that has file/shell access to your repo

---

How to use this

Feed these prompts to your coding agent one at a time, in order. Each stage assumes the previous one is done and committed. Point the agent at the five spec docs already in this folder — it should read them before writing code, not guess:

`01_TRD_SearchFit_Phase1-4.md`, `02_PRD_SearchFit_Phase1-4.md`, `03_Backend_Schema_SearchFit_Phase1-4.md`, `04_UIUX_Design_SearchFit_Phase1-4.md`, `05_App_Flow_SearchFit_Phase1-4.md`

Each prompt below is written so you can copy-paste it as-is.

---

Stage 0 — Project scaffold

```
Read 01_TRD_SearchFit_Phase1-4.md in this folder. Scaffold a new repo for the
backend: Python 3.12, FastAPI (async), SQLAlchemy 2.0 async + Alembic,
Celery + Redis for background jobs, structlog for logging, pytest for tests.
Set up docker-compose with services for the API, Postgres 16 (with pgvector
enabled), Redis, and a Celery worker. Create dev/staging/prod env file
templates. Do not write any business logic yet — just a working skeleton
with a health-check endpoint and CI (GitHub Actions) running lint + tests
on every push.
```

Stage 1 — Database schema

```
Read 03_Backend_Schema_SearchFit_Phase1-4.md. Implement every table it
describes as SQLAlchemy models, with an Alembic migration to create them.
Include the pgvector column on content_embeddings. Add the indexes listed
in Section 9 of that doc. Write a seed script that creates one demo client
named "Acme Retail" with an empty client_digital_profiles row, one user per
role in role_permissions (client_success_manager, technical_seo_specialist,
seo_strategist, seo_qa_lead), and the role_permissions rows mapping each
role to its allowed agent_key per 02_PRD_SearchFit_Phase1-4.md Section 5.
```

Stage 2 — Orchestration core (steps 01–09 of the App Flow doc)

```
Read 05_App_Flow_SearchFit_Phase1-4.md Section 1 and the role/permission
rules in 03_Backend_Schema_SearchFit_Phase1-4.md Section 2.3. Build the
orchestration API's core request path: an endpoint that accepts a chat
message + client_id + user_id, looks up the user's role and permitted
agent_keys, loads the client's client_digital_profiles row, and routes the
message to a "router" step (stubbed for now — just return which of
discovery_agent / tracking_access_agent / website_situation_agent /
competitor_market_agent / readiness_gate the message should go to, using
simple keyword matching for now, not an LLM call yet). Reject and return a
clear error if the requesting user's role isn't permitted to trigger that
agent_key. Write every request to the audit_trail table.
```

Stage 3 — Claude integration (router + planner + skill execution)

```
Wire in the Anthropic Python SDK per 01_TRD_SearchFit_Phase1-4.md Section 5.
Replace the keyword-matching router from Stage 2 with a real call to Claude
Haiku 4.5 that classifies the incoming chat message into one of the four
agent_keys (or readiness_gate), given the conversation history and the
user's permitted set. Add a separate Claude Sonnet 5 "planner" call that,
given the chosen agent_key and client_digital_profiles context, returns an
ordered list of steps that agent needs to run. Store the chat turn and the
router's/planner's output in chat_messages and agent_jobs per the schema.
Stream the response back over WebSocket, matching the streaming requirement
in 01_TRD_SearchFit_Phase1-4.md Section 12.
```

Stage 4 — Phase 1: Discovery agent

```
Read 02_PRD_SearchFit_Phase1-4.md Section 6.1 and the Phase 1 sequence
diagram in 05_App_Flow_SearchFit_Phase1-4.md Section 2. Implement the
discovery_agent: given a client_id, use Claude with a web-research tool to
draft business model / products / positioning (write to discovery_responses
with source=pre_research), then generate the pre-filled questionnaire card
described in 04_UIUX_Design_SearchFit_Phase1-4.md Section 4.1. Add an
endpoint for submitting questionnaire answers (source=client_questionnaire),
a completeness-scoring step that writes to readiness_scores
(phase=discovery), and a sign-off endpoint restricted to the
client_success_manager role that flags discrepancies between research and
client answers and, on approval, writes commercial_scope and
marketing_context into client_digital_profiles. Make this work end-to-end
for the seeded "Acme Retail" client so that the prompts "Start onboarding
discovery for Acme Retail", "Run discovery", and "Show the business model
questionnaire" all route here and produce a real response.
```

Stage 5 — Phase 2: Tracking & Access agent

```
Read 02_PRD_SearchFit_Phase1-4.md Section 6.2 and the Phase 2 sequence
diagram in 05_App_Flow_SearchFit_Phase1-4.md Section 3. Implement the
tracking_access_agent: an OAuth flow for GA4, Search Console, and GTM that
stores encrypted tokens in api_credentials; a check that pulls GA4 base tag
/ event config, GTM container version, and GSC access status; a
scikit-learn IsolationForest pass over recent event data per
01_TRD_SearchFit_Phase1-4.md Section 6 to flag anomalies; and writes to
tracking_audits (one row per element: ga4_base_tag, conversion_event,
gtm_container, search_console_access, cross_domain_tracking) with
pass/warning/fail/unverified. Render the Tracking Health Report card per
04_UIUX_Design_SearchFit_Phase1-4.md Section 4.2. If OAuth access hasn't
been granted, mark the element unverified rather than blocking, and support
a re-check that only re-validates elements that changed status (so
"Re-check tracking now that GA4 access is granted" only re-verifies GA4).
Make "Run tracking check" and "Check GA4 and GTM access" route here.
```

Stage 6 — Phase 3: Website Situation agent

```
Read 02_PRD_SearchFit_Phase1-4.md Section 6.3 and the Phase 3 sequence
diagram in 05_App_Flow_SearchFit_Phase1-4.md Section 4. Implement the
website_situation_agent as three sub-steps that can also be triggered
individually: (1) an async site crawl job (Celery) for indexation,
redirects, canonicals — write to website_audits with
audit_type=crawl_technical; (2) a backlink pull from Ahrefs with Moz as
fallback per the retry/fallback pattern in
05_App_Flow_SearchFit_Phase1-4.md Section 7, scored by a spam-risk
classifier, written to backlink_snapshots and website_audits
(audit_type=backlink_summary); (3) a traffic-anomaly pass using statsmodels
STL decomposition + the ruptures library over the client's verified GA4/GSC
series from Phase 2, written to website_audits
(audit_type=traffic_anomaly). Combine all three into the unified Audit
Summary card (Technical / Authority / Anomalies tabs) per
04_UIUX_Design_SearchFit_Phase1-4.md Section 4.3. Route "Run website
situation analysis" to all three; "Crawl the site and run a technical
audit" to just the crawl; "Check backlinks and anomalies" to just steps 2
and 3.
```

Stage 7 — Phase 4: Competitor & Market agent

```
Read 02_PRD_SearchFit_Phase1-4.md Section 6.4 and the Phase 4 sequence
diagram in 05_App_Flow_SearchFit_Phase1-4.md Section 5. Implement the
competitor_market_agent: confirm/expand the competitor set from Discovery
using web/SERP search, write to competitor_profiles; pull ranking data from
GSC/Ahrefs exports (never let the LLM estimate rankings) into
competitor_rankings with gap_flag computed; pull competitor backlink data
into backlink_snapshots (competitor-owned rows); embed competitor page
content with sentence-transformers into content_embeddings and cluster with
scikit-learn to set positioning_cluster on competitor_profiles. Cache
results in Redis for 14 days per 01_TRD_SearchFit_Phase1-4.md Section 5 so
re-runs within that window don't re-pay for the scan — but make "Refresh
competitor scan" explicitly bypass the cache. Render the Competitor
Landscape card per 04_UIUX_Design_SearchFit_Phase1-4.md Section 4.4. Route
"Run competitor analysis" and "Show the competitive landscape" to the
cached/normal path.
```

Stage 8 — Readiness gate

```
Read 05_App_Flow_SearchFit_Phase1-4.md Section 6 and
03_Backend_Schema_SearchFit_Phase1-4.md Section 6.3. Implement the
readiness_gate agent_key, restricted to the seo_qa_lead role: aggregate the
four readiness_scores rows into client_digital_profiles.
overall_readiness_score, list any phase not yet status=complete by name,
and expose an approval action that flips ready_for_phase5 to true only when
all four phases are approved. Route "Readiness gate" and "Check readiness
for phase 5" here, returning the breakdown card described in
04_UIUX_Design_SearchFit_Phase1-4.md Section 4.5.
```

Stage 9 — Chat frontend

```
Read 04_UIUX_Design_SearchFit_Phase1-4.md in full. Build the chat frontend
per that spec: single persistent chat stream over WebSocket, the
phase-status side panel, and the five structured card types (Discovery
Profile Summary, Tracking Health Report, Website Audit Summary, Competitor
Landscape, Readiness Score) each with inline Approve / Edit / Reject
controls that call the corresponding backend endpoint. Use the color tokens
and typography in Section 6. Make sure a fresh session for a client with no
prior activity opens with the Discovery agent's pre-research summary
already shown, per Section 9 ("empty states").
```

Stage 10 — End-to-end verification

```
Using the seeded "Acme Retail" client, write an integration test (or a
scripted walkthrough if you don't have a test client yet) that runs, in
order: "Start onboarding discovery for Acme Retail", answers the
questionnaire, approves the discovery sign-off as
client_success_manager; "Run tracking check" as technical_seo_specialist,
grants mock GA4 OAuth, re-checks; "Run website situation analysis" as
technical_seo_specialist and approves it; "Run competitor analysis" as
seo_strategist and approves it; then "Readiness gate" as seo_qa_lead and
confirms ready_for_phase5 flips to true. Report any step where the routing,
role check, or data write doesn't match
02_PRD_SearchFit_Phase1-4.md /
03_Backend_Schema_SearchFit_Phase1-4.md.
```

---

Notes

- Run stages in order — each one depends on tables, endpoints, or agents the prior stage created.
- If your coding agent supports it, have it commit after each stage so you can review/roll back one stage at a time rather than one giant diff.
- Real Ahrefs/Moz/GA4/GSC credentials aren't needed until Stage 5–7; use mocked responses until you're ready to connect live accounts (see 01_TRD_SearchFit_Phase1-4.md Section 14, open questions).
