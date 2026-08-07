Backend Schema

SearchFit Onboarding & Audit Agent — Phase 1–4

Version: 1.0
Date: August 4, 2026
Database: PostgreSQL 16 (+ pgvector extension)
ORM: SQLAlchemy 2.0 (async) / Alembic migrations

---

1. Schema Overview

The schema is organized into six groups:

1. Identity & access — `users`, `roles`, `role_permissions`
2. Client core — `clients`, `client_digital_profiles`
3. Conversation layer — `chat_sessions`, `chat_messages`, `agent_jobs`
4. Phase 1–4 findings — `discovery_responses`, `tracking_audits`, `website_audits`, `backlink_snapshots`, `competitor_profiles`, `competitor_rankings`
5. Governance — `findings_ledger`, `audit_trail`
6. Integration & ML support — `api_credentials`, `content_embeddings`, `readiness_scores`

Every findings-producing table follows the same pattern: an agent writes a row with `status = 'pending'`, a human reviewer transitions it to `approved` / `rejected` / `edited`, and only `approved`/`edited` rows are aggregated into the `client_digital_profiles` row that Phase 5+ reads.

2. Identity & Access

2.1 `roles`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| name | text, unique | e.g. `client_success_manager`, `technical_seo_specialist`, `seo_strategist`, `seo_qa_lead` |
| description | text | |
| created_at | timestamptz | |

2.2 `users`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| email | text, unique | |
| full_name | text | |
| role_id | uuid, FK -> roles.id | one primary role per user for this build |
| is_active | boolean, default true | |
| created_at | timestamptz | |
| last_login_at | timestamptz, nullable | |

2.3 `role_permissions`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| role_id | uuid, FK -> roles.id | |
| agent_key | text | one of `discovery_agent`, `tracking_access_agent`, `website_situation_agent`, `competitor_market_agent`, `readiness_gate` |
| can_trigger | boolean | can this role start the agent |
| can_approve | boolean | can this role approve/reject its findings |
| unique (role_id, agent_key) | constraint | |

Enforced on every orchestration API call server-side (PRD Section 5, TRD Section 8).

3. Client Core

3.1 `clients`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| legal_name | text | |
| display_name | text | |
| primary_url | text | |
| industry | text, nullable | |
| tier | text | `A` / `B` / `C` — carried from TR's existing client-tier model |
| created_at | timestamptz | |
| status | text | `onboarding`, `active`, `paused`, `churned` |

3.2 `client_digital_profiles`

The single record every SearchFit skill from Phase 5 onward reads. One row per client; updated in place as each phase completes.

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id, unique | one profile per client |
| commercial_scope | jsonb | business model, revenue split, AOV, sales cycle, seasonality — from Discovery (1.1/1.2) |
| marketing_context | jsonb | objectives, compliance constraints, other marketing spend — from Discovery |
| tracking_baseline | jsonb | GA4/GSC/GTM verification status and last-verified timestamp — from Phase 2 |
| website_situation_summary | jsonb | technical health, authority position, unresolved anomalies — from Phase 3 |
| competitive_landscape_summary | jsonb | competitor set, positioning clusters, keyword/backlink gaps — from Phase 4 |
| discovery_status | text | `not_started`, `in_progress`, `pending_signoff`, `complete` |
| tracking_status | text | same enum pattern |
| website_status | text | same enum pattern |
| competitor_status | text | same enum pattern |
| overall_readiness_score | numeric(5,2), nullable | weighted composite, see `readiness_scores` |
| ready_for_phase5 | boolean, default false | flips true only after readiness gate approval |
| updated_at | timestamptz | |

4. Conversation Layer

4.1 `chat_sessions`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| user_id | uuid, FK -> users.id | who opened the session |
| active_agent_key | text, nullable | which of the four agents is currently engaged |
| started_at | timestamptz | |
| ended_at | timestamptz, nullable | |

4.2 `chat_messages`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| session_id | uuid, FK -> chat_sessions.id | |
| role | text | `user`, `agent`, `system`, `tool_result` |
| content | text | rendered message text |
| structured_payload | jsonb, nullable | inline "card" data (tracking report, audit table, competitor matrix) rendered by the UI — see UI/UX doc |
| agent_key | text, nullable | which agent produced this message, if role = agent |
| created_at | timestamptz | |

4.3 `agent_jobs`

Backs long-running async work (crawls, competitor scans) dispatched to Celery, so the chat can show live status instead of blocking.

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| session_id | uuid, FK -> chat_sessions.id | |
| agent_key | text | |
| job_type | text | e.g. `site_crawl`, `backlink_pull`, `competitor_scan`, `anomaly_detection` |
| status | text | `queued`, `running`, `succeeded`, `failed`, `retrying` |
| attempt_count | int, default 0 | |
| provider_used | text, nullable | which external provider handled it (primary vs. fallback) |
| result_ref | jsonb, nullable | pointer to the resulting findings row(s) |
| error_detail | text, nullable | |
| created_at | timestamptz | |
| completed_at | timestamptz, nullable | |

5. Phase 1–4 Findings Tables

5.1 `discovery_responses` (Phase 1)

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| source | text | `pre_research` (D1) or `client_questionnaire` (D2) |
| field_key | text | e.g. `revenue_split`, `average_order_value`, `sales_cycle`, `seasonality`, `objectives`, `compliance_constraints` |
| field_value | jsonb | |
| confidence | numeric(4,2), nullable | set on pre-research rows; null on client-provided rows |
| discrepancy_flag | boolean, default false | true when client answer conflicts with independent research (D4) |
| status | text | `pending`, `approved`, `edited`, `rejected` |
| reviewed_by | uuid, FK -> users.id, nullable | |
| created_at | timestamptz | |

5.2 `tracking_audits` (Phase 2)

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| element | text | `ga4_base_tag`, `conversion_event`, `gtm_container`, `search_console_access`, `cross_domain_tracking` |
| check_result | text | `pass`, `warning`, `fail`, `unverified` |
| detail | jsonb | raw check output, plain-language fix instructions |
| detected_at | timestamptz | |
| status | text | `pending`, `approved`, `flagged_for_client` |
| reviewed_by | uuid, FK -> users.id, nullable | |

5.3 `website_audits` (Phase 3)

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| audit_type | text | `crawl_technical`, `traffic_anomaly`, `backlink_summary` |
| summary | jsonb | headline findings |
| severity | text | `info`, `warning`, `critical` |
| linked_job_id | uuid, FK -> agent_jobs.id, nullable | |
| status | text | `pending`, `approved`, `edited`, `rejected` |
| reviewed_by | uuid, FK -> users.id, nullable | |
| created_at | timestamptz | |

5.4 `backlink_snapshots` (Phase 3, feeds Phase 4 comparison)

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id, nullable | null when the snapshot belongs to a competitor instead |
| competitor_profile_id | uuid, FK -> competitor_profiles.id, nullable | mutually exclusive with client_id |
| provider | text | `ahrefs`, `moz` |
| referring_domains | int | |
| authority_score | numeric(5,2) | provider's domain authority metric |
| spam_risk_score | numeric(4,2), nullable | from the ML classifier (TRD Section 6) |
| top_anchor_text | jsonb | |
| pulled_at | timestamptz | |

5.5 `competitor_profiles` (Phase 4)

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| name | text | |
| url | text | |
| source | text | `discovery_carryover`, `search_visibility`, `manual` |
| positioning_cluster | text, nullable | label assigned by the clustering step |
| confirmed | boolean, default false | confirmed as a true strategic peer vs. auto-suggested |
| created_at | timestamptz | |

5.6 `competitor_rankings` (Phase 4)

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| competitor_profile_id | uuid, FK -> competitor_profiles.id | |
| keyword | text | |
| position | int | |
| search_volume | int, nullable | |
| client_position | int, nullable | client's own ranking for the same keyword, if any |
| gap_flag | boolean | true when this is a keyword the client doesn't rank for |
| source | text | `gsc_export`, `ahrefs` |
| pulled_at | timestamptz | |

6. Governance

6.1 `findings_ledger`

A cross-phase index of every reviewable item produced, so the readiness gate (Phase QA lead) has one place to see everything pending across all four agents, mirroring the "suggestion ledger" concept in TR's broader architecture.

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| agent_key | text | |
| source_table | text | which findings table this references (`discovery_responses`, `tracking_audits`, `website_audits`, `competitor_profiles`, etc.) |
| source_id | uuid | row id in that table |
| confidence | text | `high`, `medium`, `low` |
| status | text | `pending`, `approved`, `edited`, `rejected` |
| created_at | timestamptz | |
| resolved_at | timestamptz, nullable | |

6.2 `audit_trail`

Append-only log. Every chat turn, external API call, and human decision.

| Column | Type | Notes |
|---|---|---|
| id | bigserial, PK | |
| client_id | uuid, FK -> clients.id | |
| actor_type | text | `user`, `agent`, `system` |
| actor_id | uuid, nullable | user_id if actor_type = user |
| event_type | text | `chat_message`, `tool_call`, `job_started`, `job_completed`, `finding_approved`, `finding_rejected`, `readiness_gate_passed`, etc. |
| event_detail | jsonb | |
| occurred_at | timestamptz | |

6.3 `readiness_scores`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| phase | text | `discovery`, `tracking`, `website`, `competitor` |
| score | numeric(5,2) | weighted completeness score for that phase |
| missing_fields | jsonb | named list of what's still missing/low-confidence |
| computed_at | timestamptz | |

7. Integration & ML Support

7.1 `api_credentials`

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| client_id | uuid, FK -> clients.id | |
| provider | text | `ga4`, `search_console`, `gtm` |
| encrypted_token | bytea | encrypted at rest, decrypted only server-side at call time |
| scope | text | granted OAuth scopes |
| expires_at | timestamptz, nullable | |
| granted_at | timestamptz | |
| revoked_at | timestamptz, nullable | |

7.2 `content_embeddings`

Backs the Phase 4 positioning/content clustering (TRD Section 6).

| Column | Type | Notes |
|---|---|---|
| id | uuid, PK | |
| owner_type | text | `client` or `competitor_profile` |
| owner_id | uuid | |
| source_url | text | |
| embedding | vector(768) | pgvector column, dimension matches the sentence-transformers model used |
| created_at | timestamptz | |

8. Key Relationships (summary)

```
clients 1---1 client_digital_profiles
clients 1---N chat_sessions 1---N chat_messages
clients 1---N discovery_responses
clients 1---N tracking_audits
clients 1---N website_audits
clients 1---N competitor_profiles 1---N competitor_rankings
clients 1---N backlink_snapshots (client-owned side)
competitor_profiles 1---N backlink_snapshots (competitor-owned side)
clients 1---N findings_ledger
clients 1---N audit_trail
clients 1---N readiness_scores
clients 1---N api_credentials
roles 1---N users
roles 1---N role_permissions
```

9. Indexing Notes

- `findings_ledger(client_id, status)` — powers the readiness-gate review queue.
- `chat_messages(session_id, created_at)` — chat history pagination.
- `agent_jobs(status, agent_key)` — worker polling / dashboard.
- `competitor_rankings(competitor_profile_id, gap_flag)` — fast keyword-gap lookups.
- `content_embeddings` — HNSW or IVFFlat index via pgvector for similarity search.
- `audit_trail(client_id, occurred_at)` — chronological audit export.

10. Migration Strategy

- Alembic, one migration per logical change; no manual DDL against staging/prod (TRD Section 13).
- Findings tables are additive-only in normal operation (new rows on re-audit, not overwritten) so historical findings remain queryable; `client_digital_profiles` is the only table updated in place, and only from approved/edited findings.
