Technical Requirements Document (TRD)

SearchFit Onboarding & Audit Agent — Phase 1–4 Build

Stack: Python · FastAPI · PostgreSQL · ML Frameworks

Version: 1.0
Date: August 4, 2026
Owner: Traffic Radius — Director of AI & Automation
Source material: SearchFit Skill Coverage Matrix (17-phase SEO workflow), TR SEO Agentic Architecture v1.5/v1.6

---

1. Purpose & Scope

This TRD defines the technical build for a chat-based agentic application that automates the first four phases of Traffic Radius's 17-phase SEO workflow:

1. Business & Project Discovery
2. Access, Tracking & Data Collection
3. Current Website Situation Analysis
4. Competitor & Market Analysis

These four phases sit upstream of keyword research (Phase 5) and everything after it. In the source coverage matrix, Phase 1 and Phase 2 are marked "Gap" (no existing SearchFit skill) and Phases 3–4 are marked "Partial" (existing skills cover part of the job). This build closes those gaps by shipping four new or extended agent skills — Discovery Agent, Tracking & Access Agent, Website Situation Agent, and Competitor & Market Agent — running inside one conversational interface, on top of the orchestration pattern already defined in TR's agentic architecture (role check → orchestration API → client profile → plan → gather facts → execute skill → synthesize report → human review).

Everything from Phase 5 onward (keyword research, content production, publishing, local SEO, digital PR, CRO, reporting) is explicitly out of scope for this build. The system produces one artifact that later phases will consume: a completed, human-approved Client Digital Profile plus four structured findings reports.

2. Goals

- Replace manual client intake, tracking audits, site audits, and competitor scans with a single conversational agent that a Client Success Manager, Technical SEO Specialist, or SEO Strategist can run start to finish.
- Cut discovery-to-ready-for-strategy time from days (manual questionnaires + spreadsheets) to under an hour of active work, most of it review rather than data entry.
- Guarantee nothing downstream (Phase 5+) starts on an incomplete or unverified profile — enforced by a completeness/readiness score, not a checklist someone can skip.
- Keep a person accountable for every finding that leaves the system: nothing in the Client Digital Profile is marked final without human sign-off.

3. Non-Goals (explicit exclusions)

- No content generation, briefing, or publishing (Phases 9–12).
- No CMS write access of any kind — this build is read-only against the client's website and analytics; it never modifies a live site.
- No local SEO / GBP management (Phase 13).
- No link-building or outreach execution (Phase 14).
- No CRO/UX testing (Phase 15).
- No recurring reporting cadence (Phase 16) — that is a separate, later build on top of the same data model.

4. System Architecture Overview

The application follows the orchestration pattern from TR's SEO Agentic Architecture, scoped down to a single, non-publishing pipeline:

```
Chat UI (WebSocket/SSE)
   -> Orchestration API (FastAPI)
      -> Auth & Role/Permission Check
      -> Client Profile Load (PostgreSQL)
      -> Router Model (Claude Haiku 4.5) -> picks Discovery / Tracking / Website / Competitor agent
      -> Planner Model (Claude Sonnet 5) -> builds the step sequence for that agent
      -> Tool/Fact Gathering Layer -> external APIs, crawler, ML services, with retry + fallback
      -> Skill Execution (Claude Sonnet 5) -> runs the selected agent's instructions
      -> Report Synthesis (Claude Sonnet 5) -> unified findings report, text-first
      -> Human Review Checkpoint -> approve / edit / reject
      -> Client Digital Profile Update (PostgreSQL) -> feeds Phase 5+
```

Unlike the full 15-step architecture, there is no publish step (steps 12–14 of the source architecture do not apply — there is nothing to push to a CMS in Phases 1–4). The pipeline ends at "Client Digital Profile Update," which is the single handoff artifact to later phases.

4.1 Why an orchestration layer instead of calling Claude directly from the frontend

- Every external call (GA4, GSC, Ahrefs, crawler) needs a consistent retry/backoff/fallback policy that must not live in the browser.
- Role-based skill access (Section 8) must be enforced server-side, not trusted to the client.
- Cost control (routing cheap vs. expensive models, caching competitor research) requires a server-side decision point.

5. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12 | Ecosystem fit for both API and ML/data work; one language across the stack |
| API framework | FastAPI (async) | Native async for streaming chat responses and concurrent external API calls; automatic OpenAPI schema |
| ASGI server | Uvicorn (behind Gunicorn in prod) | Standard FastAPI deployment |
| Database | PostgreSQL 16 | Relational integrity for client/role/profile data; JSONB for flexible discovery answers; pgvector extension for embeddings |
| ORM / migrations | SQLAlchemy 2.0 (async) + Alembic | Typed models, versioned schema migrations |
| Task queue | Celery + Redis (or RQ for a lighter footprint) | Long-running jobs (site crawls, competitor scans) must not block the chat response |
| Chat transport | WebSocket (primary) with SSE fallback | Streaming agent responses token-by-token; WebSocket also carries structured "card" events (see UI/UX doc) |
| LLM provider | Anthropic Claude via official Python SDK | Claude Haiku 4.5 for routing/classification, Claude Sonnet 5 for planning, skill execution, and report synthesis — matching the model split in TR's existing architecture |
| Agent orchestration | Custom orchestrator (FastAPI services) — not a third-party workflow canvas | Matches TR's stated principle: "no standing dependency on an external workflow tool"; full control over retry/fallback logic |
| ML frameworks | scikit-learn, statsmodels (or Prophet), sentence-transformers, pandas, numpy | See Section 6 |
| Vector store | pgvector (inside PostgreSQL) | Competitor/content similarity without a separate vector DB to operate |
| Caching | Redis | Competitor research cache window (per source architecture: "Reuse, don't repay"); rate-limit counters |
| Secrets | Environment-injected via secrets manager (e.g., AWS Secrets Manager / GCP Secret Manager) | Provider keys and client-connected credentials (GA4/GSC OAuth tokens) never touch the codebase or client |
| Observability | structlog (structured logging), OpenTelemetry traces, Sentry for error tracking | Every job traceable end to end, per TR's "everything is traceable" principle |
| Containerization | Docker, docker-compose for local dev | Parity between dev/staging/prod |
| CI/CD | GitHub Actions -> build, test, migrate, deploy | Gate merges on test suite (Section 11) |

6. ML Frameworks — Where and Why

ML is used in three of the four in-scope phases; Discovery (Phase 1) is primarily LLM + structured-form driven and does not require a dedicated ML model.

| Phase | ML use case | Approach |
|---|---|---|
| Phase 2 — Access & Tracking | Tracking-health scoring (is GA4/GTM/conversion tracking actually firing correctly, and is data quality good enough to trust) | Rule-based checks (tag presence, event schema validation) combined with a lightweight anomaly classifier (scikit-learn `IsolationForest`) flagging suspicious traffic/event patterns (bot traffic, broken event firing, duplicate hits) |
| Phase 3 — Website Situation Analysis | Traffic-drop / anomaly diagnosis from GA4 and GSC time series | `statsmodels` (STL decomposition) or `Prophet` for trend/seasonality baseline, with a change-point detection pass (`ruptures` library) to flag when and where a drop started, cross-referenced against crawl-detected site changes |
| Phase 3 — Backlink/authority review | Authority scoring, spam-link flagging | Feature-based classifier (scikit-learn `GradientBoostingClassifier`) trained on link attributes (anchor text diversity, referring domain authority, TLD, link velocity) pulled from Ahrefs/Moz data, to pre-triage which backlinks a human should look at first |
| Phase 4 — Competitor & Market Analysis | Competitor/content similarity, positioning clustering | `sentence-transformers` embeddings of competitor page content stored in `pgvector`, clustered with scikit-learn `KMeans` or `HDBSCAN` to group competitors by actual content strategy rather than just domain |

All ML outputs are advisory — they pre-triage and prioritize what a human specialist reviews; per the "confidence decides the shelf, not the gate" principle carried over from TR's architecture, nothing an ML model flags is written to the Client Digital Profile without the human review checkpoint (Section 9).

7. External Integrations

| Integration | Used in phase | Method | Fallback |
|---|---|---|---|
| Google Analytics 4 (GA4) Data API | 2, 3 | OAuth2 (client-granted), server-side token refresh | None (primary source of truth) — if unreachable, retry with backoff, then mark tracking-health check as "unverified," never silently skip |
| Google Search Console API | 2, 3, 4 | OAuth2 | None — same retry/backoff policy |
| Google Tag Manager API | 2 | OAuth2 (read-only container inspection) | Manual container export upload if API access isn't granted |
| Ahrefs API (or Moz API) | 3, 4 | API key, server-side only | Secondary provider (Moz if Ahrefs fails, or vice versa) called directly by the orchestration API, per TR's existing fallback pattern |
| Site crawler (internal, e.g., built on `httpx` + `parsel`/`scrapy`) | 3 | Self-hosted async crawler service | N/A — internal service, retried on transient failures |
| Competitor discovery (web search) | 4 | Anthropic web search tool or SERP API | Secondary SERP provider on failure |

All provider keys and any client-granted OAuth tokens are stored server-side only, encrypted at rest (Section 10). No credential is ever passed to or stored in the browser.

8. Role-Based Access (carried over from TR's architecture, scoped to Phases 1–4 roles)

| Role | Can trigger | Cannot do |
|---|---|---|
| Client Success Manager | Discovery Agent (Phase 1) — sign-off step (D4) | Cannot approve tracking/website/competitor findings |
| Technical SEO Specialist | Tracking & Access Agent (Phase 2), Website Situation Agent (Phase 3) | Cannot sign off on Discovery |
| SEO Strategist | Competitor & Market Agent (Phase 4), can view all four phases' outputs | Escalation lead for cross-domain conflicts between phases |
| SEO QA / Reporting Lead | Final completeness-score gate before a profile is marked "ready for Phase 5" | Cannot edit findings content, only approve/reject the gate |

Enforced server-side at the orchestration API, identical in principle to Step 02 of TR's existing architecture ("no role can trigger a skill outside its own lane").

9. Human-in-the-Loop Checkpoints

Two mandatory checkpoints, matching TR's existing "a person is always in the loop" principle:

1. Per-phase sign-off — each of the four agents produces a draft finding; the accountable role (Section 8) reviews, edits, or rejects it before it's written to the Client Digital Profile.
2. Readiness gate — before the profile is marked "ready for Phase 5," the SEO QA/Reporting Lead reviews the aggregate completeness/confidence score (Section 6 of the source architecture's weighted-readiness model, reused here) and any discrepancies the system has flagged (e.g., client claims vs. independent research, per Discovery step D4).

Nothing auto-applies. Every finding is stored as "pending" until approved, regardless of confidence score — identical rule to the source architecture's suggestion ledger.

10. Security Requirements

- All external provider keys and client OAuth tokens: server-side only, encrypted at rest, rotated on a defined schedule.
- Transport: TLS everywhere (browser <-> API, API <-> external providers).
- AuthN: JWT-based session auth for internal TR users; OAuth2 for client-side data-source connections (GA4/GSC/GTM).
- AuthZ: role/permission table enforced on every orchestration API call (Section 8), not just at the UI layer.
- Audit logging: every chat turn, tool call, external API call, and human decision logged with actor, timestamp, and client ID (Section 15 of source architecture: "everything is traceable").
- PII handling: client business data (revenue splits, contact info) classified and access-scoped; no client data used to train or fine-tune any model.

11. Testing Strategy

| Layer | Approach |
|---|---|
| Unit | pytest for orchestration logic, ML scoring functions, schema validation |
| Integration | Mocked external provider responses (GA4/GSC/Ahrefs) to test retry/fallback paths without hitting live rate limits |
| Contract | Recorded fixtures (VCR-style) for each external API to catch upstream schema drift |
| Agent behavior (eval) | Golden-transcript evals for each of the four agents — fixed input scenarios with expected structured output, run on every model or prompt change |
| Load | Simulated concurrent chat sessions to validate WebSocket/Celery throughput before client rollout |
| Security | Dependency scanning (pip-audit), secret-scanning in CI, periodic access-control review |

12. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Chat first-token latency | < 2s for routing response, < 5s for first token of a reasoning response |
| Long-running jobs (crawl, competitor scan) | Run async via Celery, progress streamed to chat as status updates, not a blocking wait |
| Availability | 99.5% for the orchestration API during business hours |
| Cost control | Router model (Haiku) used for all classification/routing; Sonnet reserved for planning, skill execution, and synthesis only; competitor research cached per client for a configurable window (default 14 days) |
| Data retention | Client Digital Profile and audit trail retained indefinitely (compliance record); raw crawl/API payloads retained 90 days, then pruned |

13. Deployment & Environments

- Environments: `dev`, `staging`, `prod` — identical container images promoted between them.
- Database migrations gated in CI; no manual schema changes in staging/prod.
- Feature flags for enabling each of the four agents independently, so Phase 2 (Tracking) can ship after Phase 1 (Discovery) without a full redeploy.

14. Open Questions / Dependencies for Engineering Kickoff

- Confirm Ahrefs vs. Moz as primary backlink data provider (cost and coverage tradeoff).
- Confirm whether GTM read access will be OAuth-based or manual-export-based for the initial client cohort.
- Confirm hosting target (AWS/GCP) to finalize secrets-manager and container orchestration choice.
