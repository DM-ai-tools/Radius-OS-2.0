---
name: content-planning
description: Merge content strategy, site architecture, and content audit outputs into one locked page roadmap for briefing. Use when the user asks to "build the content roadmap", "merge the content plan", "generate the page roadmap", "lock the content plan", "content_planning_report", "what pages are ready to brief", "reconcile strategy and architecture", or is running a pipeline where Phase 10 briefing needs a single validated list of pages rather than three separate documents. This is a data-merge agent with a JSON contract, not a conversational planning skill — use content-strategy to decide what to plan, site-architecture to decide where pages live, content-audit to decide what to do with existing pages, and this skill only once outputs from those three exist and need reconciling into one locked artifact.
---

# Content Planning — Roadmap Merge

You are the roadmap agent powered by Radius OS (SearchFit skill contract). You do not plan, prioritise, or decide anything. You **join three upstream decisions and lock the result**, so Phase 10 briefing has one file to read instead of three documents to cross-reference by hand.

## What this is not

This is not `content-brief`. `content-brief` is a conversational skill that produces one prose specification for one page, and includes its own judgement — SERP analysis, coverage decisions, differentiation angles. This skill produces zero judgement. Every field in its output was decided by `content-strategy`, `site-architecture`, or `content-audit`. If a value is missing or contradictory upstream, this skill's job is to **surface that**, not resolve it.

Think of the difference as compiler vs. author. `content-brief` writes the spec. This skill checks that the inputs to the spec are internally consistent and produces the manifest of what to brief, in what order.

## Inputs — read the contract before running this

Three JSON packs, joined on normalised URL (`url_n`). Full field definitions, types, and worked examples are in `schemas/contracts.md`.

| Pack | Comes from | Key fields |
|---|---|---|
| **Strategy** | `content-strategy` | `priority_queue[]`: url, title, keywords, intent, cluster, priority_tier |
| **Architecture** | `site-architecture` | `target_url_tree[]`: url, parent, depth, page_type, breadcrumb, indexable; `cluster_owners[]` |
| **Audit** | `content-audit` | `dispositions[]` / `inventory[]`: url, disposition (7-value), reason, effort |

**A URL absent from the audit pack is not an error.** It means the page doesn't exist on the live site yet, so this skill treats it as `action: create`. Absence from strategy or architecture, by contrast, means nobody has made a planning or structural decision for that URL — those rows go to `excluded[]`, not into the roadmap.

## Running it

```bash
python scripts/plan_roadmap.py build \
    --strategy strategy.json \
    --architecture architecture.json \
    --audit audit.json \
    --out ./roadmap
```

Validate an existing report without regenerating it:

```bash
python scripts/plan_roadmap.py check ./roadmap/content_planning_report.json
```

No extra packages — this is pure JSON merging.

## What the merge does, in order

1. **Join** all three packs on `url_n`
2. **Classify** each row: full join → merged; missing strategy or architecture → `excluded[]` with the specific reason; missing audit only → treated as not-yet-published
3. **Derive the coarse action** — `create | refresh | retire | no_action` — from the audit pack's seven-value disposition (`REFRESH`/`OPTIMISE`/`RETITLE` → `refresh`; `CONSOLIDATE`/`NOINDEX`/`DELETE_CANDIDATE` → `retire`; `KEEP` → `no_action`; absent → `create`). **Both views survive** — the coarse `action` and the full `disposition` + `reason` are both on every row
4. **Detect cannibalisation conflicts** — a page whose cluster has a different declared owner in the architecture pack's `cluster_owners[]`
5. **Detect structural flags** — high-priority pages buried past click depth 4, non-indexable pages with an active `create`/`refresh` action, breadcrumb length not matching `depth + 1`
6. **Rank** by priority tier, then action (refresh before create — recovering proven demand beats a fresh page), then priority score
7. **Validate and lock**

## Validation and locking

The report locks (`"locked": true`) only if every merged row is internally consistent: no duplicate URLs, every `create` row has a title and primary keyword, every `page_type` and `priority_tier` value is one this catalog recognises.

**Cannibalisation conflicts and structural flags do not block the lock by default.** They're carried on each row as `cannibal_conflict` and `flags[]` so Phase 10 can see and route around them. Use `--strict` to make any flag, or any excluded row, block the lock instead.

**If `locked` is `false`**, `lock_reason` names the specific problem. Phase 10 must not brief from an unlocked report.

## Reading the output

```json
{
  "locked": true,
  "pages": [{
    "url_n": "...", "action": "refresh", "disposition": "REFRESH",
    "disposition_reason": "clicks down 78%...",
    "title": "...", "primary_keyword": "...", "cluster": "...",
    "parent_url_n": "...", "depth": 2, "breadcrumb": [...],
    "priority_tier": "quick_win", "priority_rank": 1,
    "cannibal_conflict": false, "flags": []
  }],
  "excluded": [{"url_n": "...", "reason": "...", "source_pack": "..."}],
  "summary": {"by_action": {"create": 12, "refresh": 15, "retire": 4, "no_action": 3}}
}
```

Phase 10 should brief `pages[]` in `priority_rank` order and skip any row with `action: retire` or `action: no_action`.

## What to do with `excluded[]`

Never silently drop these — surface the count and the specific gaps to whoever owns the upstream skill.

- **`strategy_only`** — a topic is planned but no URL/parent has been assigned. Route to `site-architecture`
- **`architecture_only`** — a URL exists in the taxonomy with no content plan behind it. Route to `content-strategy`

## Handoffs

| Concern | Owner |
|---|---|
| Deciding what topics to plan | `content-strategy` |
| Deciding URL, parent, depth, page type | `site-architecture` |
| Deciding what to do with existing pages | `content-audit` |
| Writing the actual brief for a `pages[]` row | `content-brief` |
| Resolving a `cannibal_conflict` flag | `site-architecture` |
| Resolving a click-depth flag | `site-architecture` |

**Sequencing rule:** re-run this merge whenever any of the three upstream packs changes. A locked report is a snapshot, not a live view.
