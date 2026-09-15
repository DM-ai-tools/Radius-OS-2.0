# Radius OS — Implementation Plan for Client Review Action Items

**Source:** Client review discussion (Kushal, Karthik) — Sept 2026
**Codebase reviewed:** `C:\Users\Kushal\Radius OP` (current working tree, uncommitted)
**Prepared:** Sept 8, 2026

## Important context before scoping any of this

`git status` on the repo shows a large uncommitted working tree — 109 modified files and roughly 30 new, untracked files, including `backend/app/services/service_prioritization.py`, `live_site_scan.py`, `technical_seo_ia.py`, `content_enhancement.py`, and `frontend/src/components/cards/ServicePrioritizationCard.tsx`. In other words, **a meaningful chunk of items 1 and 2 below is already built in this working copy** — it just hasn't been committed, demoed fully, or wired end-to-end. Before assigning new engineering work, it's worth a 15-minute walkthrough with whoever wrote this diff to confirm what's finished, what's half-wired, and what should simply be committed rather than re-built. The plan below reflects what the code actually does today, not what the meeting assumed was missing.

---

## 1. Site structure hierarchy (parent → child → grandchild)

**Ask:** service category pages (parent) → sub-service pages (child) → supporting blog content (grandchild), with service-level keywords routed to service pages instead of blog posts.

**Current state:**
- `backend/app/services/site_architecture.py::build_blueprint()` already models a `page_type_model` (Home / Hub-Pillar / Spoke / Service / Comparison / Blog / Utility) and constructs a `target_url_tree` with `url / parent / depth / page_type`. Hub→Spoke nesting already works this way for topic clusters (`/{hub}/{spoke}/`).
- Client-declared services from the CDD are currently built **flat** — every service sits as a sibling under `/services/{service}/` at depth 2, all sharing the same parent. No `/meta-ads/instagram-ads/` style nesting.
- `service_prioritization.py::ia_nodes_from_competitor_tree()` *does* build a genuine two-level tree (`/services/{svc}/` → `/services/{svc}/{sub}/`) for sub-services discovered from competitors, and this gets merged into the blueprint — but only for the competitor-sourced path, and it labels both levels `page_type: "service"` (no distinct sub-service type).
- Blog posts are always flat under `/blog/{post}/` with no `parent` link back to a specific service or sub-service page — so there's currently no way to say "this blog post supports Instagram Ads specifically."

**What needs to change:**
1. Unify the CDD-declared service catalog and the competitor-derived sub-service tree into one nesting scheme, so every service — however it was sourced — can carry children.
2. Add a distinct `page_type: "subservice"` (or similar) so parent and child aren't visually/logically identical.
3. Extend `content_planning.py`'s roadmap builder (`build_roadmap`) so a blog row can be assigned a `parent` pointing at the specific service/sub-service URL it supports, rather than defaulting to `/blog/`.
4. Fix keyword→page-type routing so service-level keywords (low-volume, high commercial intent — e.g. "social media marketing," 50/mo) resolve to a service or sub-service page, not a blog post. This is a `content_planning.py::_planning_content_type()` / `_page_type()` decision-logic change; the `PAGE_TYPES` enum already has the right categories, so this is a routing-logic fix, not a data-model change.

**Owner note:** this is the highest-leverage item since the frontend blueprint view, the content calendar, and the auto-suggestion feature (item 2) all read from this same tree — get the hierarchy right first, everything downstream inherits it.

---

## 2. Service auto-suggestion from competitor/industry data

**Ask:** don't just pull services from the client's own site/CDD — scrape competitor and industry data to suggest services the client hasn't listed, especially useful for new businesses with thin service pages. Keep the existing select/deselect (mark primary) UI pattern.

**Current state — most of this already exists:**
- `service_prioritization.py::build_client_service_catalog()` builds the client's own service list from CDD fields.
- `discover_competitor_service_trees()` crawls each Phase-4 competitor's site and extracts service/sub-service URL paths via regex matching (`/services|solutions|products|what-we-do/...`).
- `merge_competitor_subservices()` adds competitor-discovered sub-services into the tree, tagged `source: "competitor"`, `selected: False` by default.
- `default_selection()` implements the exact select/deselect + "mark as primary" data structure already validated in the demo (`selected`, `priority_tier: primary|secondary`, `selected_service_ids`, `primary_service_ids`).
- `ServicePrioritizationCard.tsx` already renders the checkbox UI and posts the selection back.
- Wired end-to-end through `backend/app/api/clients.py` (`get_service_prioritization` / `save_service_prioritization`) into the blueprint builder.

**The actual gap:** `merge_competitor_subservices()` only attaches a competitor's sub-service under a client service with a **matching name**. It has no path for proposing a brand-new top-level service the client doesn't offer at all but competitors do — which is precisely the "thin service coverage" case Kushal raised for new businesses. There's an `adopt_competitor_service_ids` field already referenced in `confirm_prioritization`, suggesting this was planned but not finished.

**What needs to change:**
1. Add a matching path in `merge_competitor_subservices()` (or a sibling function) that surfaces competitor top-level services with **no name match** in the client's catalog as new, unselected candidate services — not just sub-services under existing ones.
2. Decide on a threshold/heuristic for surfacing these (e.g. appears across 2+ competitors, or industry-standard service categories) so the suggestion list doesn't become noisy.
3. Confirm the frontend card already handles a "new service, not currently offered" card state distinctly from "existing service, competitor added a sub-service" — if not, a small UI addition to `ServicePrioritizationCard.tsx`.

---

## 3. Content calendar & publishing flow

**Ask:** confirmed as solid in the meeting — site structure → content calendar → select title → draft → review/request changes → preview → publish.

**Action:** no rework needed here per the discussion. The one dependency is that this flow needs to consume the corrected hierarchy from item 1 and the expanded content types from item 4 — so sequence this after those two land, and re-validate the flow end-to-end once they do (this is covered in the verification step at the end of this plan).

---

## 4. Content type expansion (blog, service page, guide, tool)

**Ask:** the tool previously only generated blogs; it needs to support service pages, guides, and tools, working top-down from site structure before drilling into individual pages.

**Current state — more built than the meeting assumed:**
- `content_planning.py::PAGE_TYPES` already lists home, hub, spoke, pillar, cluster, article, blog, service, product, location, commercial, supporting, utility, landing, faq, comparison.
- `create_content.py::page_playbook()` has full drafting playbooks (voice, structure, length, outline) for service, landing, comparison, guide, hub, spoke, location, product, and article pages — each with distinct generation logic.
- The frontend (`ContentPlanningCard.tsx::contentTypeLabel()`) already renders "Service page," "Landing page," "FAQ," "Guide," "Comparison," "Listicle" chips, not just "Blog."

**Genuine gap:** `"tool"` does not exist anywhere — not in `PAGE_TYPES`, not in `page_playbook`, not in the frontend labels. That's the one net-new content type.

**What needs to change:**
1. Add `"tool"` to `PAGE_TYPES` and define what a "tool" page means for this product (e.g., an interactive calculator/quiz embed vs. a static page describing a tool) — this needs a quick product decision before engineering, since it's structurally different from the other content types (likely needs embed/interactive-component support, not just prose).
2. Add a `page_playbook` entry and prose-generation branch for it in `create_content.py`.
3. Add the frontend label/chip.
4. Separately: since "guide" and "service page" already work end-to-end in code, re-demo those specifically to the client — the gap may be visibility/discoverability in the current flow rather than missing functionality. Worth clarifying with them before spending engineering time re-building something that exists.

---

## 5. Memory retention (30-day flush)

**Ask:** evaluate extending the 30-day client memory retention window, possibly via dynamic config or a persistent onboarding-session store.

**Current state:** there is no 30-day retention/flush mechanism anywhere in the codebase today. What exists and could be confused for it:
- `config.py::competitor_cache_days = 14` — a competitor-data cache TTL, unrelated to client memory.
- OAuth refresh-token expiry (`api/oauth.py`, `timedelta(days=30)`) — token lifetime, not client data.
- `services/memory_packs.py` — this compacts/slims phase payloads to fit LLM context windows; it does not expire or delete anything.
- Client state (chat sessions, messages, agent jobs, digital profile) lives in Postgres with `created_at`/`updated_at` timestamps but no expiry column, and the only Celery beat job is an hourly tracking recheck — there's no purge task.

**Read on the "30 days" reference:** either it's a policy the team has in mind but hasn't implemented yet, or it's describing behavior in a different environment/config not present in this codebase. Worth a quick confirmation with whoever raised it before building — this may be a net-new feature request rather than a bug in existing behavior.

**What needs to change (assuming it's net-new):**
1. Decide what "memory" means in retention terms — raw chat messages, phase payloads/CDD, or both — since these have different sensitivity and reuse value.
2. Add a retention-window config value to `config.py` (make it dynamic/per-org if the team wants configurability rather than a global constant).
3. Add a Celery beat task (alongside the existing `recheck-unverified-tracking` job) that purges or archives rows older than the window.
4. If the goal is "persistent onboarding session" rather than time-based expiry, that's a different design — a flag on `ChatSession`/`Client` marking onboarding data as exempt from the retention job, so onboarding progress survives indefinitely while routine chat history still ages out. Worth clarifying which of these two the team actually wants, since they lead to different implementations.

---

## 6. Site blueprint UI rework

**Ask:** the site structure display needs to clearly differentiate content types (service vs. blog vs. TOFU) before the content marketing flow kicks in.

**Current state:** `SiteArchitectureCard.tsx` renders the URL tree and roadmap rows, but every page type shows as plain text in a generic chip with no color-coding or icon — unlike `StatusChip`, which already has an explicit color map for status/priority elsewhere in the same codebase.

**What needs to change:**
1. Add a `page_type → color/icon` lookup (mirroring the existing `StatusChip` colors-map pattern) and apply it to both the tree view and the roadmap table rows.
2. Check `frontend/CLASSY_UI_SPEC.md` for the existing palette/token conventions before introducing new colors, so this stays consistent with the rest of the UI rather than introducing an off-brand scheme.
3. This item is naturally sequenced after item 1 (hierarchy fix) and item 4 (tool content type), since the UI needs the underlying page types to be correct and complete before it can visually distinguish them.

---

## 7. Role-based access control validation

**Ask:** validate the role-permission gates end-to-end across the full workflow.

**Current state:** the RBAC data model and enforcement function are real and already built — `models/identity.py` (`Role`, `User`, `RolePermission` with `can_trigger`/`can_approve` per agent), and `deps.py::require_permission()` as the central check (with `head_of_department` hardcoded to bypass all checks). `services/role_skills.py` maps roles to skill ownership.

**The actual gap:** enforcement is scattered, not centralized. `require_permission()` is only called in `api/findings.py` (discovery/tracking/competitor triggers, and approval gates via `services/review.py`) and `api/integrations.py` (publishing). Every other agent phase router — search_demand, content_strategy, site_architecture, content_planning, content_production, technical_seo, on_page_seo — has no visible permission check. Most importantly, **`api/chat.py`, the main conversational endpoint that actually drives phase progression, has zero `require_permission` calls** — meaning a lower-level role could potentially trigger or approve phases through the chat interface even though the dedicated REST endpoints correctly gate them.

**What needs to change:**
1. This is the most concrete, scoped fix in this whole list: apply `require_permission()` uniformly, either as a FastAPI dependency on every phase-related router, or as a single check inside the chat message-handling loop keyed off whichever `agent_key` the message is about to trigger.
2. Audit the ~9 ungated agent phase routers against the ~4 that are gated today and close the gap systematically rather than one at a time, since the pattern to replicate already exists in `findings.py`.
3. Once closed, this is the item most suited to a straightforward end-to-end validation pass (see verification step below) — spin up test users at each role level and confirm gates hold across every phase, not just the ones demoed.

---

## Suggested sequencing

1. **Site structure hierarchy fix** (item 1) — foundational; content calendar, service suggestions, and the blueprint UI all depend on getting this right.
2. **RBAC enforcement gap** (item 7) — self-contained, doesn't block or get blocked by anything else, and is a well-understood fix (replicate an existing pattern). Good to parallelize with item 1.
3. **Service auto-suggestion gap** (item 2) — builds directly on item 1's unified hierarchy.
4. **Content type expansion** (item 4) — needs a quick product decision on what "tool" means before engineering starts.
5. **Site blueprint UI rework** (item 6) — sequenced after 1 and 4 so it has correct, complete page types to render.
6. **Memory retention** (item 5) — needs a scoping conversation first (what "memory" means, time-based vs. persistent-session), since it's genuinely unbuilt and the requirements aren't fully pinned down yet.
7. **Content calendar/publishing flow** (item 3) — no rebuild needed; re-validate end-to-end once items 1 and 4 land.

## Verification

Before calling any of the above done: re-run the full Acme Retail Co. demo walkthrough end-to-end (Discovery → Publishing) after each major item lands, confirm the site blueprint shows correct 3-tier URLs for a real service with sub-services and supporting blog posts, confirm a newly-suggested (non-name-matching) competitor service surfaces correctly and can be selected, and run the role-permission audit with at least three role levels (head of department, a mid-level approver, and a restricted trigger-only role) against every phase — not just the ones currently gated — to confirm no phase is reachable through chat that isn't reachable through its REST endpoint.
