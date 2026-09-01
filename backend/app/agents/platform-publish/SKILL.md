---
name: platform-publish
description: Fetch brand design, render a dry-run publish preview, and write to WordPress (draft by default) with IndexNow/GSC recrawl planning. Use for publish, publishing, indexation, IndexNow, recrawl, go live, push to WordPress, phase 12.
---

# Platform Publish & Indexation (Phase 12)

You produce the publish package for approved pages: the client's real design, an exact
dry-run preview, the CMS write, and the post-publish verification plan.

This is the only phase that can change a client's live site. Treat every write as
consequential and irreversible from here.

## Pipeline context

Runs after Phase 11 (On-Page SEO). Reads from locked shared memory:

- **On-Page SEO** (Phase 11) — `pages` with approved title / meta / H1 / H2s / JSON-LD,
  plus `internal_links`. This is what gets published; do not re-optimise it here.
- **Content Production** (Phase 10) — `briefs` for outline, FAQ, and content goal.
- **Site Architecture** (Phase 6b) — `rollout_plan`, and the hub/service page used as the
  live reference for the preview.

## Three modes — never guess which one

| Mode | Trigger | What happens |
|---|---|---|
| `preview` | anything not explicitly a write ("run phase 12", "publish checklist") | Brand fetch + preview render. **No CMS write.** |
| `draft` | "save to WordPress", "create draft", "push draft" | Writes as WordPress draft, then verifies |
| `publish` | "publish it live", "go live", "make it live" | Requests live status, then verifies |

**Ambiguity resolves to `preview`.** A CMS write must be asked for explicitly; it is never
inferred from a vague instruction. Live publish additionally requires the deployment to
set `WORDPRESS_ALLOW_LIVE_PUBLISH` — when it is off, the write is downgraded to draft and
the downgrade is reported. Never describe a downgraded write as a live publish.

## Process

### Step 1: Fetch the design
Brandfetch supplies logo, colour palette, and typeface from the client's domain.
Firecrawl scrapes one live reference page (a hub or service page from the IA tree) for
heading pattern and section rhythm.

Neither is required. When either is unavailable the preview renders neutral styling and
**says so on its face** — a preview that silently looks generic misleads the reviewer into
approving a design they have not actually seen.

### Step 2: Build the exact payload (v1.9 step 12)
Render the dry-run preview — "the exact payload that would be sent, with no live CMS
writes". The preview and the publisher build from the same `cms_payload`, so what the
reviewer approves is what the CMS receives.

Body content is assembled **only** from approved Phase 10/11 material: H1, brief intro,
outline headings, FAQ. Do not write article copy here. Outlined-but-unwritten sections
render as visible placeholders and set `has_written_copy: false` — shipping an outline as
if it were a finished page is worse than shipping nothing.

### Step 3: Platform capability check (v1.9 guardrail)
Before any write, verify the CMS answers and the user has the rights being asked for.
An unreachable CMS or a user without `publish_posts` is an **external blocker** reported
up front — not a failure discovered halfway through a batch.

### Step 4: Write to the CMS
- Match on slug first so re-runs **update** rather than duplicate.
- Application Passwords only; never an account password. Credentials never appear in
  output, logs, or the card.

### Step 5: Verify — never a silent republish (v1.9 step 14)
Re-read every written post from the CMS and compare stored title and status against what
was sent. Any mismatch, failed write, or post that cannot be re-read is surfaced with
`action_required` for a human. Do not retry silently.

IndexNow and GSC recrawl are **planned, never submitted** from this build: the payload and
the recrawl queue are output for a human to action. IndexNow requires a key file hosted on
the client's domain before the first submission — that is a prerequisite, not a step you
can perform.

## Guardrails

- Draft is the default. Live publish requires an explicit request *and* deployment opt-in.
- Nothing here overrides the human gate: v1.9 requires approval before publish, and final
  publish authority is lead-only (Senior SEO Strategist / Content & On-Page Lead). QA
  triages the queue but cannot trigger the write.
- Never publish a URL absent from the approved On-Page package.
- Never claim a write succeeded without a verification read backing it.
- Report partial success honestly: "3 succeeded, 2 failed" beats a green summary.

## Evidence base

**Documented behaviour** (follow these):
- Google may rewrite `<title>` links and meta descriptions when it judges another string
  better matches the query — an approved title is an input, not a guarantee.
  <https://developers.google.com/search/docs/appearance/title-link>
- Meta descriptions are **not** a ranking factor; they influence snippet text and
  therefore click-through.
  <https://developers.google.com/search/docs/appearance/snippet>
- Structured data must describe content **visible on the page**; marking up absent content
  violates the guidelines and risks a manual action.
  <https://developers.google.com/search/docs/appearance/structured-data/sd-policies>
- IndexNow requires a key file hosted at the domain root before submissions are accepted.
  <https://www.indexnow.org/documentation>
- Submitting a URL for recrawl does not guarantee indexing or a timeframe.
  <https://developers.google.com/search/docs/crawling-indexing/ask-google-to-recrawl>

**Heuristic / house standard** (reasonable defaults, not Google rules):
- Batch size of 8 pages per run, to keep a review reasonable and limit blast radius.
- Publishing as draft first, so a human sees the rendered page in its real theme before
  it is public.

**Do not claim**:
- That publishing or pinging IndexNow causes ranking movement.
- That a rich result will appear — Google never guarantees rich result display even for
  valid structured data.

## Output contract

```json
{
  "mode_requested": "preview|draft|publish",
  "mode_effective": "preview|draft|publish",
  "design": {"brand_available": true, "primary_color": "", "logo_url": "", "reference_url": ""},
  "previews": [{"url": "", "slug": "", "cms_payload": {}, "preview_html": "", "has_written_copy": false}],
  "publish_queue": [{"url": "", "slug": "", "status": "", "post_id": 0, "link": "", "downgraded": false}],
  "verification": [{"slug": "", "verified": true, "stored_status": "", "action_required": null}],
  "capability_blockers": [{"blocker": "", "detail": "", "resolution": ""}],
  "indexnow_preview": {"host": "", "urlList": []},
  "gsc_recrawl": [],
  "qa_checklist": []
}
```

## Triggers

- "publish", "publishing", "indexation", "IndexNow", "recrawl", "phase 12"
- "preview the page", "show me the preview", "push to WordPress", "go live"
