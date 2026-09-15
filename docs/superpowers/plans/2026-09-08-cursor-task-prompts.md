# Cursor Task Prompts — Radius OS Action Items

Companion to `2026-09-08-client-review-action-items-plan.md`. Each section below is a **self-contained prompt** you can paste directly into Cursor (Composer/Agent mode, with the repo open) to have it implement one item. They're ordered to match the suggested sequencing from the plan — do them one at a time, review the diff, run the app/tests, then move to the next. Don't paste all seven at once; Cursor does better with one bounded task per session, especially since several of these touch the same files.

Before starting, point Cursor at the whole repo (not just one file) so it can trace callers — most of these functions are consumed by an API router and a frontend card, and Cursor needs to update all three layers, not just the service function.

---

## Task 1 — Site structure: real parent → child → grandchild hierarchy

```
Context: This is a FastAPI + React SEO tool. Site structure is built in
backend/app/services/site_architecture.py::build_blueprint(). It already
has a page_type_model (home/hub/spoke/service/comparison/blog/utility) and
constructs a target_url_tree with url/parent/depth/page_type fields.

Problem: client-declared services (from the CDD) are built flat — every
service is a sibling under /services/{service}/ at the same depth, with
the same parent. Meanwhile backend/app/services/service_prioritization.py
::ia_nodes_from_competitor_tree() DOES build a real two-level tree
(/services/{svc}/ -> /services/{svc}/{sub}/) for sub-services sourced from
competitors, and that gets merged into target_url_tree — but only for the
competitor-sourced path, and both levels share page_type "service" (no
distinct sub-service type). Blog posts are always flat under /blog/{post}/
with no parent link back to a specific service/sub-service page.

Task:
1. In site_architecture.py, unify the CDD-declared service catalog and the
   competitor-derived sub-service tree so EVERY service (however sourced)
   can have children nested under it, using the URL pattern
   /{service-slug}/{subservice-slug}/ (not /services/{service}/ — match
   whatever URL convention the rest of target_url_tree already uses for
   top-level pages; check how the "home" node's children are keyed).
2. Add a distinct page_type value for sub-services (e.g. "subservice") so
   parent service pages and child sub-service pages are no longer
   identical in type — update the page_type_model enum/constant and
   everywhere it's validated (search for other exhaustive matches on
   page_type across the backend, e.g. in content_planning.py).
3. In backend/app/services/content_planning.py, find the roadmap builder
   (build_roadmap) and its content-type/page-type decision logic
   (_planning_content_type / _page_type). Currently blog rows default to
   parent "/blog/". Add logic so a blog post that supports a specific
   service or sub-service gets `parent` set to that service/sub-service's
   URL instead — use topical relevance to the service (keyword overlap,
   or an explicit "supports_service_id" field if one already exists
   upstream in the topic/keyword data; check search_demand.py for
   whatever ties a keyword to a service).
4. Also in that same routing logic: when a keyword is being assigned a
   page type, if it matches a client's existing service or sub-service
   (by name/topic, low search volume, commercial intent), assign it
   page_type "service" or "subservice" and parent it under that service,
   NOT "blog". Only fall through to blog when there's no service match.
5. Update any Pydantic schemas in backend/app/schemas/ that describe the
   blueprint/tree response so the new page_type value and parent linkage
   are reflected in the API contract.

Acceptance criteria: for a seeded client with an existing service (e.g.
"Meta Ads") and a competitor-discovered sub-service (e.g. "Instagram
Ads"), the resulting target_url_tree contains a 3-level chain:
/meta-ads/ (page_type=service, parent=root) ->
/meta-ads/instagram-ads/ (page_type=subservice, parent=/meta-ads/) ->
at least one blog post (page_type=blog, parent=/meta-ads/instagram-ads/).
A low-volume commercial keyword like "social media marketing" resolves to
a service/subservice page, not a blog post, in the content_planning
roadmap. Run the existing test suite (pytest -q in backend/) and fix any
tests that assert the old flat structure — update their expectations to
the new nested structure rather than deleting them.
```

---

## Task 2 — Service auto-suggestion: surface brand-new competitor services

```
Context: backend/app/services/service_prioritization.py already does most
of what's needed: build_client_service_catalog() builds the client's own
service list from the CDD; discover_competitor_service_trees() crawls
competitor sites and extracts service/sub-service paths; 
merge_competitor_subservices() adds competitor sub-services into the
client's tree; default_selection() implements the select/deselect +
"mark as primary" data structure already used by
frontend/src/components/cards/ServicePrioritizationCard.tsx.

Problem: merge_competitor_subservices() only attaches a competitor
sub-service under a client service whose NAME ALREADY MATCHES (see the
by_name lookup inside that function). It has no path for proposing a
brand-new TOP-LEVEL service the client doesn't offer at all, even when
multiple competitors do. There's an existing but unused/incomplete
`adopt_competitor_service_ids` field referenced in confirm_prioritization
that suggests this was planned.

Task:
1. In service_prioritization.py, add a new function (or extend
   merge_competitor_subservices) that, after matching sub-services to
   existing client services by name, takes any REMAINING top-level
   competitor services with no name match and turns each into a
   candidate top-level service node: source="competitor",
   selected=False, priority_tier=None (unselected until the user opts
   in) — same shape as existing service nodes so the frontend doesn't
   need a new data contract, just a new state to render.
2. Add a simple frequency/consensus threshold so this isn't noisy: only
   surface a candidate new service if it appears across at least 2
   competitors (or whatever threshold makes sense given how many
   competitors are typically tracked per client — check Phase 4
   competitor.py for how many competitors are typically stored).
3. Wire adopt_competitor_service_ids through in confirm_prioritization()
   so that when the user selects one of these candidate new services,
   it gets promoted into the client's own service catalog (same
   treatment as a CDD-declared service) rather than staying tagged
   "competitor" forever.
4. In frontend/src/components/cards/ServicePrioritizationCard.tsx, check
   whether the card already renders a visual distinction between "existing
   service, competitor added a sub-service under it" vs. "brand-new
   service the client doesn't currently offer." If not, add a badge/label
   (e.g. "Suggested — not currently on your site") for the new-service
   case so users don't confuse it with their own declared services.
5. Confirm backend/app/api/clients.py's get_service_prioritization /
   save_service_prioritization endpoints pass this new candidate-service
   data through without dropping it (check the response schema).

Acceptance criteria: for a client with thin service coverage (fewer
services declared than 2+ of their tracked competitors), the service
prioritization view shows at least one unselected, clearly-labeled
candidate service that isn't in the client's own CDD. Selecting it and
saving promotes it into the client's active service catalog and it then
appears correctly in the site_architecture blueprint (Task 1's tree).
```

---

## Task 3 — Content calendar & publishing flow: re-validate only

```
Context: the content calendar/publishing flow (site structure -> content
calendar -> select title -> draft -> review/request changes -> preview ->
publish) was confirmed as working correctly in a client review and does
NOT need to be rebuilt.

Task: after Task 1 (site structure hierarchy) and Task 4 (content types)
are both merged, do a regression pass: run through the full flow end to
end for a seeded client (Acme Retail Co. per the README) and confirm nothing
broke — specifically that the content calendar correctly reflects the new
3-tier hierarchy from Task 1 (service/subservice/blog parent-child links
show up correctly in whatever calendar/table view consumes
target_url_tree) and that the new "tool" content type from Task 4 doesn't
crash the drafting step in create_content.py if a page of that type isn't
fully wired yet (fail gracefully with a clear "not yet supported" message
instead of a 500).

No new features here — this is a verification task, report back anything
that broke rather than silently fixing scope beyond the regression.
```

---

## Task 4 — Add "tool" as a content type (and confirm guide/service page work)

```
Context: backend/app/services/content_planning.py::PAGE_TYPES already
includes home, hub, spoke, pillar, cluster, article, blog, service,
product, location, commercial, supporting, utility, landing, faq,
comparison. backend/app/services/create_content.py::page_playbook()
already has full drafting playbooks (voice/structure/length/outline) for
service, landing, comparison, guide, hub, spoke, location, product, and
article — each branching in _section_prose/_opening_prose/_cta_prose.
frontend/src/components/cards/ContentPlanningCard.tsx::contentTypeLabel()
already renders "Service page"/"Landing page"/"FAQ"/"Guide"/"Comparison"/
"Listicle" chips.

Problem: "tool" does not exist anywhere in this stack — not in PAGE_TYPES,
not in page_playbook, not in the frontend labels. Everything else in this
list (including "guide" and "service page") already works end-to-end.

Before writing code: a "tool" page is structurally different from the
others in this list — it likely means an interactive
calculator/quiz/estimator embed, not a static prose page. Do NOT assume a
prose-only implementation. Ask me (via a comment or a question back) to
confirm what a "tool" page should contain before generating content for
it, if that's not already obvious from other docs in docs/product/ or
docs/architecture/ (check there first for any existing tool-page spec).

Task (once "tool" scope is confirmed):
1. Add "tool" to PAGE_TYPES in content_planning.py.
2. Add a page_playbook entry for "tool" in create_content.py with its own
   outline/voice logic (this will differ meaningfully from prose-only
   types — likely needs a placeholder/spec for embedding an interactive
   component rather than full prose generation).
3. Add the "Tool" label/chip in ContentPlanningCard.tsx's
   contentTypeLabel(), following the existing pattern.
4. Do NOT touch service/landing/guide/comparison — they already work.
   Instead, write a short note back confirming they work as expected by
   testing each one through the actual content-drafting flow (not just
   reading the code), since the client review suggested these might not
   be visible/discoverable even though the code supports them — check if
   this is a UI-surfacing issue (e.g. these options aren't shown in
   whatever page-title-selection step precedes drafting) rather than a
   missing-feature issue.

Acceptance criteria: a "tool" page type can be created from the content
calendar without erroring, even if its content generation is a scoped-down
first version (e.g. a structured spec/outline rather than full prose,
if that's what we agree it should be). Service/landing/guide/comparison
page types are confirmed reachable and working through the actual UI flow,
not just present in the code.
```

---

## Task 5 — Memory retention

```
Context: this repo currently has NO client-memory retention/flush
mechanism. Do not assume one exists and "extend" it — this needs a
design decision before implementation. What currently exists that is
NOT the same thing: config.py::competitor_cache_days=14 (a competitor
data cache TTL, unrelated); OAuth refresh-token expiry in api/oauth.py
(timedelta(days=30), token lifetime not client data);
services/memory_packs.py (compacts/slims phase payloads for LLM context
windows — this is about token budget, not deletion/expiry). Client data
(chat sessions, messages, agent jobs, ClientDigitalProfile) lives in
Postgres via models/client.py and models/conversation.py, with
created_at/updated_at timestamps but no expiry column. The only Celery
beat job today (tasks/celery_app.py) is an hourly tracking recheck —
there is no purge task anywhere.

Task: do NOT write a purge job yet. First, stop and ask me to confirm
two design decisions in writing before generating any code:
  (a) What "memory" means for retention purposes — raw chat
      messages/ChatMessage rows, phase payloads/ClientDigitalProfile, or
      both? These have different reuse value and sensitivity.
  (b) Is the goal time-based expiry (delete/archive rows older than N
      days, N possibly configurable) or a "persistent onboarding
      session" (a flag that exempts onboarding-phase data from expiry
      entirely, so routine chat history ages out but onboarding
      progress/CDP never does)? These lead to different implementations
      and are not interchangeable.

Once I've answered, implement accordingly:
- If time-based: add a retention window setting to backend/app/config.py
  (default 30 days, but make it a settable value, not a hardcoded
  constant, mirroring how competitor_cache_days is already exposed as
  a config field), then add a new Celery beat task alongside
  recheck-unverified-tracking in tasks/celery_app.py + tasks/jobs.py that
  purges or archives (confirm which — hard delete vs. soft
  archive/anonymize) rows older than the window, scoped to whichever
  tables we agreed on in (a).
- If persistent-onboarding-session: add an `is_onboarding` or similar
  boolean/flag column via an Alembic migration on the relevant model(s)
  in models/client.py or models/conversation.py, and make sure the
  purge logic (still needed for non-onboarding data) skips rows where
  that flag is set.
- Either way: write an Alembic migration (backend/alembic/) for any
  schema changes, and add a test in backend/tests/ that seeds old and
  new rows and confirms the purge/exemption logic behaves correctly.

Acceptance criteria: nothing is implemented until (a) and (b) above are
answered. After that, the chosen mechanism is covered by a migration (if
schema changed), a scheduled Celery task (if time-based), and a passing
test that proves rows past the retention window are actually removed/
archived while exempted rows are not.
```

---

## Task 6 — Site blueprint UI: differentiate content types visually

```
Context: frontend/src/components/cards/SiteArchitectureCard.tsx renders
the URL tree and roadmap rows. Every page type currently shows as plain
text inside a generic chip (className="cs-chip") with no color-coding or
icon. Elsewhere in the same file/codebase, StatusChip already has a
working colors-map pattern for status/priority values — reuse that
pattern rather than inventing a new one.

Task:
1. In SiteArchitectureCard.tsx, add a page_type -> {color, icon/label}
   lookup object, following the same structure as StatusChip's existing
   colors map (same file, look for the `colors` object used by
   StatusChip).
2. Apply it to the page-type chip in the URL tree view and in the
   roadmap table rows (the two locations currently rendering
   `<span className="cs-chip">{n.type}</span>` and the equivalent in the
   roadmap table).
3. Before picking colors, read frontend/CLASSY_UI_SPEC.md for the
   existing palette/token conventions and use those tokens/variables
   rather than introducing new hex values — this needs to stay
   consistent with the rest of the UI, not introduce an off-brand
   ad-hoc palette.
4. Make sure the new "subservice" page_type from Task 1 and the "tool"
   page_type from Task 4 both get entries in this lookup — don't ship
   this with gaps for the two newest types.
5. Visually group service -> subservice -> blog as a nested/indented
   tree in the URL tree view if it isn't already rendered with visual
   nesting (check current indentation logic against the `depth` field
   already present on each node).

Acceptance criteria: opening the site blueprint for a client with the
Task 1 hierarchy in place shows service, subservice, and blog nodes in
visually distinct colors/icons, correctly indented by depth, using only
colors/tokens already defined in CLASSY_UI_SPEC.md.
```

---

## Task 7 — Close the RBAC enforcement gap

```
Context: RBAC is already fully modeled and partially enforced — this is
NOT a build-from-scratch task, it's closing gaps in an existing pattern.
models/identity.py has Role, User (role_id FK), and RolePermission (per
agent_key, with can_trigger/can_approve columns). deps.py::
require_permission() is the central enforcement function — it looks up
RolePermission for (user.role_id, agent_key) and checks can_trigger/
can_approve, with role "head_of_department" hardcoded to bypass all
checks. services/role_skills.py has SKILL_ROLE_OWNERS mapping roles to
skill ownership and required_role_for().

Problem: require_permission() is only actually called in
api/findings.py (discovery/tracking/competitor triggers, and approval
gates via services/review.py) and api/integrations.py (publishing).
Every other agent-phase router — search_demand, content_strategy,
site_architecture, content_planning, content_production, technical_seo,
on_page_seo — has no permission check at all. Most importantly,
api/chat.py, the main conversational endpoint that drives phase
progression through the chat interface, has ZERO require_permission
calls — so a restricted-role user might be able to trigger or approve a
phase through chat even though the equivalent dedicated REST endpoint
correctly blocks them.

Task:
1. Audit every router under backend/app/api/ and list which ones call
   require_permission() today vs. which agent-phase-triggering or
   approval endpoints don't. Use api/findings.py's existing calls as the
   reference pattern (note the agent_key + need_trigger/need_approve
   argument shape it already uses).
2. Add the missing require_permission() calls to every agent-phase
   router identified as ungated (search_demand, content_strategy,
   site_architecture, content_planning, content_production,
   technical_seo, on_page_seo, and any others found in the audit),
   matching each endpoint's agent_key to the correct RolePermission
   lookup, using need_trigger=True for actions that start/run a phase
   and need_approve=True for anything that approves/advances a phase
   gate (same distinction findings.py already makes).
3. For api/chat.py: since this is a shared conversational entry point
   rather than one-endpoint-per-action, add a single permission check
   inside the message-handling logic, keyed off whichever agent_key the
   incoming message is about to trigger or approve, called before that
   action executes — not a blanket check at the top of the endpoint
   (a user should still be able to chat/ask questions; only phase
   trigger/approve actions need gating).
4. Do not change the head_of_department bypass behavior — that's
   intentional per the existing design.

Acceptance criteria: write or extend backend/tests/ to spin up at least
three test users — head_of_department (should pass everywhere),
a role with can_trigger=True/can_approve=False on a specific agent_key
(should be able to trigger that phase's REST endpoint AND via chat, but
blocked from approving it via either path), and a role with no
permissions on that agent_key at all (blocked from both trigger and
approve, via both the REST endpoint and chat). Run this test matrix
against every agent phase, not just the ones already gated before this
task, and confirm chat and REST enforcement agree with each other for
every phase.
```

---

## Notes on using these with Cursor

- Paste one task block (the text inside the triple-backtick fence) into Cursor's Composer/Agent chat as a single message. Keep the repo open so Cursor can index it and follow the file references.
- Tasks 5 and 4 both have an explicit "stop and ask before coding" step — don't skip past those prompts to force code out of Cursor; the whole point is that those two are underspecified and coding first would mean re-doing it.
- After each task, review the diff yourself before accepting — several of these touch shared files (`content_planning.py`, `site_architecture.py`) across multiple tasks, so merge conflicts between tasks done out of order are likely if you run them in parallel sessions instead of sequentially.
- Run `pytest -q` in `backend/` after every backend task and `npm run build` (or the project's lint/typecheck script) in `frontend/` after every frontend task before moving to the next prompt.
