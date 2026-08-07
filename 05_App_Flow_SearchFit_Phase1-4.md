App Flow — Data Flow Across the Application

SearchFit Onboarding & Audit Agent — Phase 1–4

Version: 1.0
Date: August 4, 2026

---

1. Overall Orchestration (scoped from TR's 15-step architecture)

This build uses steps 01–09 of Traffic Radius's existing orchestration pattern, then substitutes a Client Digital Profile write for the publish steps (10–14), since Phases 1–4 never touch a live CMS.

```mermaid
flowchart TD
    A["01 Request arrives (chat / scheduler)"] --> B["02 Role & permission check"]
    B --> C["03 Orchestration API (FastAPI) receives request"]
    C --> D["04 Client Digital Profile pulled"]
    D --> E["05 Router picks agent (Claude Haiku 4.5)\nDiscovery / Tracking / Website / Competitor"]
    E --> F["06 Planner builds step sequence (Claude Sonnet 5)"]
    F --> G["07 Facts gathered: external APIs + crawler + ML\n(retry -> backoff -> fallback provider)"]
    G --> H["08 Agent skill executes (Claude Sonnet 5)"]
    H --> I["09 Findings synthesized into structured card"]
    I --> J{Human checkpoint}
    J -->|Approve| K["Write to client_digital_profiles"]
    J -->|Edit| K
    J -->|Reject| F
    K --> L["Readiness score recalculated"]
    L --> M{All 4 phases approved\nAND score >= threshold?}
    M -->|No| N["Stay in onboarding — surfaced in status panel"]
    M -->|Yes| O["SEO QA Lead reviews readiness gate"]
    O --> P["ready_for_phase5 = true\nHandoff to Phase 5 (out of scope)"]
```

2. Phase 1 — Business & Project Discovery

Adapts the D1–D5 discovery workflow, run once per new client, before any other phase can meaningfully start.

```mermaid
sequenceDiagram
    participant CSM as Client Success Manager
    participant Chat as Chat UI
    participant API as Orchestration API
    participant Claude as Claude (Sonnet 5)
    participant Web as Web research tool
    participant DB as PostgreSQL

    CSM->>Chat: Start onboarding for new client
    Chat->>API: create client + session
    API->>Claude: D1 pre-research request
    Claude->>Web: research site, GBP, socials, reviews, competitors
    Web-->>Claude: raw findings
    Claude-->>API: draft business model, products, positioning
    API->>DB: insert discovery_responses (source=pre_research)
    API-->>Chat: D2 pre-filled questionnaire rendered as card
    CSM->>Chat: client confirms/edits answers
    Chat->>API: submit questionnaire responses
    API->>DB: insert discovery_responses (source=client_questionnaire)
    API->>Claude: D3 completeness scoring
    Claude-->>API: readiness score + missing fields
    API->>DB: insert readiness_scores (phase=discovery)
    API-->>Chat: D4 sign-off card (incl. any discrepancy flags)
    CSM->>Chat: approve / edit
    Chat->>API: sign-off decision
    API->>DB: update discovery_responses.status, client_digital_profiles.commercial_scope / marketing_context
    API->>DB: audit_trail: finding_approved
```

3. Phase 2 — Access, Tracking & Data Collection

```mermaid
sequenceDiagram
    participant Tech as Technical SEO Specialist
    participant Chat as Chat UI
    participant API as Orchestration API
    participant OAuth as GA4 / GSC / GTM (OAuth)
    participant ML as Anomaly classifier (scikit-learn)
    participant DB as PostgreSQL

    Tech->>Chat: request tracking check for client
    Chat->>API: trigger tracking_access_agent
    API-->>Chat: guided OAuth access request (per provider)
    Tech->>OAuth: grant read access
    OAuth-->>API: access token
    API->>DB: insert api_credentials (encrypted)
    API->>OAuth: pull GA4 base tag / event config, GTM container, GSC status
    OAuth-->>API: raw config + recent event data
    API->>ML: run tag-firing validation + anomaly checks
    ML-->>API: pass/warning/fail per element
    API->>DB: insert tracking_audits (one row per element)
    API-->>Chat: Tracking Health Report card
    Tech->>Chat: approve baseline / flag for client remediation
    Chat->>API: review decision
    API->>DB: update tracking_audits.status, client_digital_profiles.tracking_baseline
    API->>DB: audit_trail: finding_approved / flagged
```

If OAuth access is not granted immediately, the flow does not block: the agent marks the relevant element `unverified`, surfaces it in the status panel, and re-checks on a schedule (Celery beat task) rather than stalling Phase 3.

4. Phase 3 — Current Website Situation Analysis

```mermaid
sequenceDiagram
    participant Tech as Technical SEO Specialist
    participant Chat as Chat UI
    participant API as Orchestration API
    participant Crawler as Internal crawler (async job)
    participant Ahrefs as Ahrefs / Moz
    participant MLspam as Spam-risk classifier
    participant Stat as statsmodels / ruptures
    participant DB as PostgreSQL

    Tech->>Chat: request website situation analysis
    Chat->>API: trigger website_situation_agent
    API->>Crawler: enqueue site_crawl job
    API-->>Chat: system notice: crawl started (async)
    Crawler-->>API: crawl results (indexation, redirects, canonicals)
    API->>DB: insert website_audits (audit_type=crawl_technical)
    API->>Ahrefs: pull backlink profile (retry -> fallback if unreachable)
    Ahrefs-->>API: referring domains, authority score, anchor text
    API->>MLspam: score backlink quality
    MLspam-->>API: spam_risk_score per link batch
    API->>DB: insert backlink_snapshots (client-owned)
    API->>DB: insert website_audits (audit_type=backlink_summary)
    API->>DB: read tracking-verified GA4/GSC series (from Phase 2)
    API->>Stat: trend decomposition + change-point detection
    Stat-->>API: detected anomaly dates, correlated with crawl changes
    API->>DB: insert website_audits (audit_type=traffic_anomaly)
    API-->>Chat: unified Audit Summary card (Technical / Authority / Anomalies tabs)
    Tech->>Chat: approve / edit / reject
    Chat->>API: review decision
    API->>DB: update client_digital_profiles.website_situation_summary
    API->>DB: audit_trail: finding_approved
```

5. Phase 4 — Competitor & Market Analysis

```mermaid
sequenceDiagram
    participant Strat as SEO Strategist
    participant Chat as Chat UI
    participant API as Orchestration API
    participant Search as Web/SERP search
    participant GSC as GSC / Ahrefs export
    participant Embed as sentence-transformers + pgvector
    participant DB as PostgreSQL

    Strat->>Chat: request competitor & market analysis
    Chat->>API: trigger competitor_market_agent
    API->>DB: read competitor set carried over from Discovery
    API->>Search: confirm/expand competitors by search visibility
    Search-->>API: candidate competitor list
    API->>DB: insert competitor_profiles
    API->>GSC: pull ranking data (own + competitor keywords)
    GSC-->>API: keyword positions
    API->>DB: insert competitor_rankings (gap_flag computed)
    API->>DB: read competitor backlink data (reuses backlink_snapshots pattern)
    API->>Embed: embed competitor page content, cluster
    Embed-->>API: positioning_cluster labels per competitor
    API->>DB: update competitor_profiles.positioning_cluster
    API-->>Chat: Competitor Landscape card (clusters, keyword gaps, authority comparison)
    Strat->>Chat: confirm competitor set / approve landscape summary
    Chat->>API: review decision
    API->>DB: update client_digital_profiles.competitive_landscape_summary
    API->>DB: audit_trail: finding_approved
```

Competitor research results are cached (Redis, 14-day default window per TRD Section 5) so re-running Phase 4 for the same client shortly after doesn't re-pay for a full scan.

6. Readiness Gate — Cross-Phase Data Flow

```mermaid
flowchart LR
    D["discovery_responses\n(approved)"] --> RS["readiness_scores"]
    T["tracking_audits\n(approved)"] --> RS
    W["website_audits\n(approved)"] --> RS
    C["competitor_profiles/rankings\n(approved)"] --> RS
    RS --> CDP["client_digital_profiles\noverall_readiness_score"]
    CDP --> Gate{"SEO QA Lead review"}
    Gate -->|Approve| Ready["ready_for_phase5 = true"]
    Gate -->|Send back| Back["Flag specific phase for re-review"]
    Ready --> Handoff["Phase 5 Keyword Research\n(out of scope — reads client_digital_profiles)"]
```

7. Error / Fallback Data Flow (applies to every external call in Sections 2–5)

```mermaid
flowchart TD
    Call["Orchestration API calls external provider"] --> OK{Success?}
    OK -->|Yes| Use["Use response, continue pipeline"]
    OK -->|No| Retry["Retry with backoff (bounded attempts)"]
    Retry --> OK2{Success?}
    OK2 -->|Yes| Use
    OK2 -->|No| Fallback["Call secondary provider directly\n(e.g. Moz if Ahrefs fails)"]
    Fallback --> OK3{Success?}
    OK3 -->|Yes| Use
    OK3 -->|No| Mark["Mark element 'unverified'\nSurface plainly in chat + status panel"]
    Mark --> Human["Never retried silently again —\nwaits for human action or scheduled recheck"]
```

8. State Model — Per-Phase Status

Each phase's status field in `client_digital_profiles` moves through the same states, driven entirely by the flows above:

```mermaid
stateDiagram-v2
    [*] --> not_started
    not_started --> in_progress: agent triggered
    in_progress --> pending_signoff: findings synthesized
    pending_signoff --> in_progress: rejected by reviewer
    pending_signoff --> complete: approved/edited by reviewer
    complete --> in_progress: re-run requested (e.g. data refresh)
```

9. Session-Level Data Flow Summary

A single client engagement produces exactly one thread of data, end to end:

```
Client identity (clients)
   -> one chat_sessions row per active engagement
      -> many chat_messages (full conversational record, all 4 agents)
      -> many agent_jobs (async work backing the conversation)
   -> findings written per phase (discovery_responses / tracking_audits / website_audits / competitor_profiles+rankings)
   -> every finding indexed in findings_ledger for reviewer visibility
   -> every action logged in audit_trail (nothing untraceable)
   -> approved findings roll up into client_digital_profiles (the one handoff artifact)
   -> readiness_scores computed per phase, aggregated into overall_readiness_score
   -> SEO QA Lead gate flips ready_for_phase5, unlocking everything downstream (out of this build's scope)
```
