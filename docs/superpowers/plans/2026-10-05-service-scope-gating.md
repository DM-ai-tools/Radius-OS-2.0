# Service Scope Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keyword research and content generation only ever work with the sub-services the client actually offers, chosen explicitly per service, and a violation is caught before generation and after drafting.

**Architecture:** One new pure module, `service_scope.py`, owns the scope data shape, the "is this text about a sub-service we don't offer" detector, and the defaults. The operator's choice is saved as `service_scope` inside the existing `commercial_scope.service_prioritization` JSON (no migration). Existing consumers (`compact_selected_services`, seed expansion, IA nodes) switch from "empty list means no filter" to "a service with a scope entry is strictly limited to its `offered` list". Three new gates call the module: the keyword-validation gate, a pre-generation check in `write_one_page`, and a post-draft check wired into the phase-10 deterministic validation so the existing revision loop picks it up.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / pytest (`asyncio_mode = auto`); React 18 + TypeScript (Vite).

**Spec:** Design approved in chat on 2026-10-05 (audit + proposal). Decisions confirmed: (1) bring back per-sub-service checkboxes, reversing the "included automatically" behaviour from commit 328e431; (2) competitor-discovered sub-services default to NOT offered; (3) block before generation (keywords/brief), and let the finished-draft check feed the existing revision loop; (4) clients with an already-saved selection stay "unscoped" (old behaviour + a warning note) until re-confirmed.

## Global Constraints

- Backend tests run from `backend/`: `python -m pytest tests/<file> -v`. Frontend check is `npm run build` from `frontend/` (there is no frontend test runner).
- Do NOT commit, stage, or touch git state. The working tree already holds the user's uncommitted edits in `dataforseo.py`, `providers.py`, `keyword_seeding.py`, `keyword_opportunity.py`, `content_signals/validation.py` and `tests/test_dataforseo_keyword_enrichment.py`. Edit `content_signals/validation.py` additively only (the Edit tool keeps their changes); never touch the other four files.
- No Alembic migration: scope lives inside the existing `service_prioritization` JSON column.
- Scope item shape is `{"id": str, "name": str}` (id = `_norm_key(name)` for website/manual sub-services). Entry shape is `{"name": str, "offered": [item], "excluded": [item], "decided_at": str | None}`, keyed by normalised service id.
- Semantics: a service **with** a scope entry is strictly limited to `offered` (empty = no sub-service pages). A service **without** an entry is unscoped and keeps today's flat-`selected_subservice_ids` behaviour.
- Rejection reason string is exactly `sub_service_not_offered`; classifier version string is exactly `service_scope_v1`.
- After every task run the full related suites: `tests/test_service_prioritization.py tests/test_service_prioritization_api.py tests/test_content_planning.py tests/test_content_signals_context.py tests/test_content_business_validation.py tests/test_create_content.py`. Where an old test encoded the leak (a competitor sub-service auto-selected), update the expectation and say so in the task report; do not weaken the new behaviour.

## File Structure

| File | Responsibility |
|---|---|
| `backend/app/services/service_scope.py` (create) | Scope shape, normalisation, `sub_allowed`, `excluded_hits`, keyword partition, content-context signal, defaults, `finalize_scope` |
| `backend/tests/test_service_scope.py` (create) | Unit tests for the module |
| `backend/tests/test_service_scope_gates.py` (create) | Tests for the prioritization wiring, keyword gate, content gate, draft check, phase check |
| `backend/app/services/service_prioritization.py` (modify) | Use `sub_allowed`; competitor subs default unselected; defaults and `confirm_prioritization` carry `service_scope` |
| `backend/app/services/site_architecture.py` (modify) | Pass `service_scope` into `ia_nodes_from_competitor_tree` (2 call sites) |
| `backend/app/api/clients.py` (modify) | `ServicePrioritizationUpdate.service_scope` + save path |
| `backend/app/services/keyword_validation_gate.py` (modify) | Drop keywords about non-offered sub-services |
| `backend/app/agents/search_demand.py` (modify) | Pass prioritization to the gate |
| `backend/app/services/content_signals/context.py` (modify) | `service_id` param, `service_scope` key, prompt block |
| `backend/app/services/content_signals/validation.py` (modify, additive) | Prompt rule line + `sub_service_scope` check |
| `backend/app/agents/content_production.py` (modify) | Pass `service_id` into the context builder |
| `backend/app/services/create_content.py` (modify) | Pre-generation scope refusal in `write_one_page` |
| `backend/app/services/phase_validation/deterministic.py`, `criteria.py` (modify) | `production_sub_service_scope` check |
| `frontend/src/components/cards/ServicePrioritizationCard.tsx` (modify) | Per-service sub-service panel, review gate, `service_scope` payload |

---

### Task 1: The `service_scope` module

**Files:**
- Create: `backend/app/services/service_scope.py`
- Test: `backend/tests/test_service_scope.py`

**Interfaces:**
- Consumes: from `app.services.service_prioritization`: `_distinctive_tokens(text) -> set[str]`, `_expand_platform_tokens(tokens) -> set[str]`, `_norm_key(value) -> str`, `_loose_key(value) -> str`, `_selection_keys(*values) -> set[str]`. (`service_prioritization` must import this module only lazily, inside functions, to avoid a circular import.)
- Produces (all in `app.services.service_scope`):
  - `REJECT_REASON = "sub_service_not_offered"`, `CLASSIFIER_VERSION = "service_scope_v1"`
  - `normalize_scope(raw) -> dict[str, dict]`
  - `scope_of(prioritization) -> dict[str, dict]`
  - `resolve_scope(prioritization, ref) -> tuple[str, dict] | None`
  - `sub_allowed(prioritization, service_id, sub_id, sub_name="") -> bool`
  - `excluded_hits(text, entry) -> list[str]`
  - `partition_scope_rejects(rows, prioritization) -> tuple[list[dict], list[dict]]`
  - `scope_signal(prioritization, ref) -> dict | None`
  - `scope_violations(texts, signal) -> list[dict]`
  - `suggest_exclusions(service_name, offered, already=()) -> list[dict]`
  - `build_default_scope(catalog, selected_service_ids) -> dict[str, dict]`
  - `finalize_scope(payload) -> None` (mutates)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_service_scope.py`:

```python
"""Per-service scope: which sub-services the client actually offers."""

from app.services.service_scope import (
    CLASSIFIER_VERSION,
    REJECT_REASON,
    build_default_scope,
    excluded_hits,
    finalize_scope,
    normalize_scope,
    partition_scope_rejects,
    resolve_scope,
    scope_signal,
    scope_violations,
    sub_allowed,
    suggest_exclusions,
)

SMM = {
    "name": "Social Media Marketing",
    "offered": [{"id": "meta ads", "name": "Meta Ads"}, "Instagram Ads"],
    "excluded": ["TikTok Ads", "LinkedIn Ads", "Pinterest Ads", "Twitter/X Ads"],
}


def _prio(**extra):
    return {
        "confirmed_at": "x",
        "selected_service_ids": ["social media marketing"],
        "selected_subservice_ids": [],
        "service_scope": {"social media marketing": SMM},
        **extra,
    }


def test_normalize_accepts_plain_strings_and_dicts_and_dedupes():
    scope = normalize_scope(
        {"Social Media Marketing": {**SMM, "offered": SMM["offered"] + ["meta ads"]}}
    )
    entry = scope["social media marketing"]
    assert [o["name"] for o in entry["offered"]] == ["Meta Ads", "Instagram Ads"]
    assert entry["offered"][1]["id"] == "instagram ads"
    assert [e["name"] for e in entry["excluded"]][0] == "TikTok Ads"


def test_offered_wins_when_a_name_is_in_both_lists():
    scope = normalize_scope(
        {"smm": {"name": "SMM", "offered": ["Meta Ads"], "excluded": ["meta ads", "TikTok Ads"]}}
    )
    assert [e["name"] for e in scope["smm"]["excluded"]] == ["TikTok Ads"]


def test_normalize_tolerates_garbage():
    assert normalize_scope(None) == {}
    assert normalize_scope({"a": "nope"}) == {}
    assert normalize_scope({"a": {"offered": [None, 3, {}]}})["a"]["offered"] == []


def test_resolve_scope_by_service_slug_name_or_member_sub_service():
    prio = _prio()
    assert resolve_scope(prio, "social-media-marketing")[0] == "social media marketing"
    assert resolve_scope(prio, "Social Media Marketing")[0] == "social media marketing"
    assert resolve_scope(prio, "tiktok ads")[0] == "social media marketing"
    assert resolve_scope(prio, "meta ads")[0] == "social media marketing"
    assert resolve_scope(prio, "seo") is None


def test_sub_allowed_scoped_service_is_strict():
    prio = _prio()
    assert sub_allowed(prio, "social media marketing", "meta ads", "Meta Ads")
    assert sub_allowed(prio, "social media marketing", "instagram ads", "Instagram Ads")
    assert not sub_allowed(prio, "social media marketing", "tiktok-ads", "TikTok Ads")
    # a sub-service nobody mentioned is not offered either
    assert not sub_allowed(prio, "social media marketing", "snapchat ads", "Snapchat Ads")


def test_sub_allowed_scoped_with_empty_offered_means_none():
    prio = {"service_scope": {"seo": {"name": "SEO", "offered": [], "excluded": []}}}
    assert not sub_allowed(prio, "seo", "local seo", "Local SEO")


def test_sub_allowed_unscoped_keeps_legacy_flat_list_behaviour():
    legacy_empty = {"selected_subservice_ids": []}
    assert sub_allowed(legacy_empty, "seo", "local seo", "Local SEO")
    legacy_set = {"selected_subservice_ids": ["local seo"]}
    assert sub_allowed(legacy_set, "seo", "local seo", "Local SEO")
    assert not sub_allowed(legacy_set, "seo", "link building", "Link Building")


def test_excluded_hits_catches_the_unoffered_platform():
    entry = normalize_scope({"s": SMM})["s"]
    assert excluded_hits("tiktok ads agency melbourne", entry) == ["TikTok Ads"]
    assert excluded_hits("How to run LinkedIn campaigns", entry) == ["LinkedIn Ads"]
    assert excluded_hits("twitter ad costs", entry) == ["Twitter/X Ads"]


def test_excluded_hits_ignores_offered_and_unrelated_text():
    entry = normalize_scope({"s": SMM})["s"]
    assert excluded_hits("meta ads management melbourne", entry) == []
    assert excluded_hits("instagram ads for restaurants", entry) == []
    assert excluded_hits("social media marketing agency", entry) == []


def test_alias_family_covers_facebook_when_meta_is_offered():
    entry = normalize_scope(
        {"s": {"name": "SMM", "offered": ["Meta Ads"], "excluded": ["Facebook Ads", "TikTok Ads"]}}
    )["s"]
    assert excluded_hits("facebook ads melbourne", entry) == []
    assert excluded_hits("tiktok ads", entry) == ["TikTok Ads"]


def test_multi_token_exclusion_needs_every_token():
    entry = {"name": "Email", "offered": ["Email Marketing"], "excluded": ["SMS Automation"]}
    assert excluded_hits("sms automation tools", entry) == ["SMS Automation"]
    assert excluded_hits("marketing automation tools", entry) == []


def test_generic_only_exclusion_matches_as_a_phrase():
    entry = {"name": "X", "offered": ["Meta Ads"], "excluded": ["Social Ads"]}
    assert excluded_hits("social ads agency", entry) == ["Social Ads"]
    assert excluded_hits("social media agency", entry) == []


def test_partition_scope_rejects_drops_only_scoped_hits():
    rows = [
        {"keyword": "tiktok ads melbourne", "target": "meta ads"},
        {"keyword": "meta ads melbourne", "target": "meta ads"},
        {"keyword": "tiktok marketing", "target": "seo"},  # no scope for seo → untouched
        {"keyword": "linkedin ads cost", "target": "Social Media Marketing"},
    ]
    kept, dropped = partition_scope_rejects(rows, _prio())
    assert [r["keyword"] for r in kept] == ["meta ads melbourne", "tiktok marketing"]
    assert [r["keyword"] for r in dropped] == ["tiktok ads melbourne", "linkedin ads cost"]
    assert dropped[0]["relevance_drop_reason"] == REJECT_REASON
    assert dropped[0]["classifier_version"] == CLASSIFIER_VERSION
    assert dropped[0]["excluded_sub_service"] == "TikTok Ads"
    assert dropped[0]["originating_service"] == "meta ads"


def test_partition_without_scope_is_a_noop():
    rows = [{"keyword": "tiktok ads", "target": "meta ads"}]
    kept, dropped = partition_scope_rejects(rows, {"selected_service_ids": ["meta ads"]})
    assert kept == rows and dropped == []


def test_scope_signal_scoped_unscoped_and_unknown():
    prio = _prio(
        service_catalog=[
            {"id": "seo", "name": "SEO", "subservices": [{"id": "local seo", "name": "Local SEO"}]},
            {"id": "pr", "name": "PR", "subservices": []},
        ]
    )
    scoped = scope_signal(prio, "social-media-marketing")
    assert scoped["scoped"] is True
    assert scoped["offered"] == ["Meta Ads", "Instagram Ads"]
    assert "TikTok Ads" in scoped["excluded"]
    unscoped = scope_signal(prio, "seo")
    assert unscoped["scoped"] is False and unscoped["offered"] == []
    assert scope_signal(prio, "pr") is None  # no sub-services → nothing to say
    assert scope_signal(prio, "unknown thing") is None


def test_scope_violations_reports_text_and_sub_service():
    sig = scope_signal(_prio(), "social media marketing")
    found = scope_violations(["meta ads tips", "TikTok Ads for retailers"], sig)
    assert found == [{"text": "TikTok Ads for retailers", "sub_service": "TikTok Ads"}]
    assert scope_violations(["tiktok ads"], {"scoped": False, "excluded": [], "offered": []}) == []
    assert scope_violations(["tiktok ads"], None) == []


def test_suggest_exclusions_social_family_skips_covered_and_existing():
    names = [s["name"] for s in suggest_exclusions("Social Media Marketing", ["Meta Ads"], ["LinkedIn Ads"])]
    assert "TikTok Ads" in names and "Pinterest Ads" in names
    assert "Facebook Ads" not in names and "Instagram Ads" not in names  # covered by Meta
    assert "LinkedIn Ads" not in names  # already excluded


def test_suggest_exclusions_unknown_family_is_empty():
    assert suggest_exclusions("Plumbing Repairs", ["Drain Cleaning"]) == []


def test_suggest_exclusions_paid_search_never_excludes_the_parent_itself():
    names = [s["name"] for s in suggest_exclusions("Google Ads/PPC Management", ["Shopping Ads"])]
    assert "Google Ads" not in names
    assert "Bing Ads" in names


def test_build_default_scope_offers_site_and_manual_but_not_competitor_subs():
    catalog = [
        {
            "id": "social media marketing",
            "name": "Social Media Marketing",
            "subservices": [
                {"id": "meta ads", "name": "Meta Ads", "source": "website"},
                {"id": "tiktok-ads", "name": "TikTok Ads", "source": "competitor"},
                {"id": "reels", "name": "Reels", "source": "manual"},
            ],
        },
        {"id": "seo", "name": "SEO", "subservices": []},
    ]
    scope = build_default_scope(catalog, ["social media marketing"])
    entry = scope["social media marketing"]
    assert [o["name"] for o in entry["offered"]] == ["Meta Ads", "Reels"]
    excluded = [e["name"] for e in entry["excluded"]]
    assert excluded[0] == "TikTok Ads" and "LinkedIn Ads" in excluded
    assert "seo" not in scope  # not selected → no entry


def test_finalize_scope_stamps_filters_and_derives_flat_ids():
    payload = {
        "selected_service_ids": ["social media marketing", "seo"],
        "selected_subservice_ids": ["meta ads", "tiktok-ads", "local seo"],
        "service_catalog": [
            {
                "id": "social media marketing",
                "name": "Social Media Marketing",
                "subservices": [
                    {"id": "meta ads", "name": "Meta Ads"},
                    {"id": "tiktok-ads", "name": "TikTok Ads"},
                ],
            },
            {"id": "seo", "name": "SEO", "subservices": [{"id": "local seo", "name": "Local SEO"}]},
        ],
        "service_scope": {
            "social media marketing": {
                "name": "Social Media Marketing",
                "offered": ["Meta Ads"],
                "excluded": [{"id": "tiktok-ads", "name": "TikTok Ads"}],
            },
            "not selected": {"name": "Nope", "offered": ["X"], "excluded": []},
        },
    }
    finalize_scope(payload)
    assert set(payload["service_scope"]) == {"social media marketing"}
    assert payload["service_scope"]["social media marketing"]["decided_at"]
    # tiktok is governed by the scope and dropped; unscoped seo's legacy id survives
    assert payload["selected_subservice_ids"] == ["local seo", "meta ads"]


def test_finalize_scope_with_no_scope_leaves_legacy_ids_alone():
    payload = {"selected_service_ids": ["seo"], "selected_subservice_ids": ["local seo"]}
    finalize_scope(payload)
    assert payload["service_scope"] == {}
    assert payload["selected_subservice_ids"] == ["local seo"]
```

- [ ] **Step 2: Run to verify it fails**

Run (from `backend/`): `python -m pytest tests/test_service_scope.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'app.services.service_scope'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/service_scope.py`:

```python
"""Per-service scope: which sub-services the client actually offers.

The service catalog is discovered from the client's own site *and* from competitor
sites, so it routinely lists sub-services the client does not sell (a competitor's
TikTok Ads page under our Social Media Marketing). ``service_scope`` is the
operator's explicit answer, saved on ``commercial_scope.service_prioritization``:

    "service_scope": {
        "social media marketing": {
            "name": "Social Media Marketing",
            "offered":  [{"id": "meta ads", "name": "Meta Ads"}],
            "excluded": [{"id": "tiktok ads", "name": "TikTok Ads"}],
            "decided_at": "2026-10-05T00:00:00+00:00"
        }
    }

A service WITH an entry is strictly limited to ``offered`` (empty means no
sub-service pages). A service WITHOUT an entry is *unscoped* — a legacy save — and
keeps the old flat ``selected_subservice_ids`` behaviour.

Everything that decides "is this text about a sub-service we do not offer" lives
here so keyword research, planning and content generation cannot drift apart.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.services.service_prioritization import (
    _distinctive_tokens,
    _expand_platform_tokens,
    _loose_key,
    _norm_key,
    _selection_keys,
)

REJECT_REASON = "sub_service_not_offered"
CLASSIFIER_VERSION = "service_scope_v1"

# Siblings a parent category is commonly associated with. These only *suggest*
# exclusions in the selection UI; what is enforced is the saved ``excluded`` list.
COMMON_SIBLINGS: dict[str, tuple[str, ...]] = {
    "social": (
        "Facebook Ads",
        "Instagram Ads",
        "TikTok Ads",
        "LinkedIn Ads",
        "Pinterest Ads",
        "Snapchat Ads",
        "Twitter/X Ads",
        "Reddit Ads",
        "YouTube Ads",
    ),
    "paid_search": (
        "Google Ads",
        "Microsoft Ads",
        "Bing Ads",
        "YouTube Ads",
        "Display Ads",
        "Shopping Ads",
    ),
}


def _item(raw: Any) -> dict[str, str] | None:
    if isinstance(raw, str):
        name = " ".join(raw.split()).strip()
        return {"id": _norm_key(name), "name": name} if name else None
    if isinstance(raw, dict):
        name = " ".join(str(raw.get("name") or raw.get("id") or "").split()).strip()
        if not name:
            return None
        return {"id": _norm_key(str(raw.get("id") or name)), "name": name}
    return None


def _items(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in raw or []:
        item = _item(value)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            out.append(item)
    return out


def _item_keys(item: dict[str, str]) -> set[str]:
    return _selection_keys(item["id"], item["name"])


def normalize_scope(raw: Any) -> dict[str, dict[str, Any]]:
    """Canonical scope map. Accepts hand-edited JSON (plain strings or dicts)."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for key, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        sid = _norm_key(str(key))
        if not sid:
            continue
        offered = _items(entry.get("offered"))
        offered_keys = {k for o in offered for k in _item_keys(o)}
        # An explicit "offered" beats a stray duplicate in "excluded".
        excluded = [e for e in _items(entry.get("excluded")) if not (_item_keys(e) & offered_keys)]
        out[sid] = {
            "name": str(entry.get("name") or key).strip(),
            "offered": offered,
            "excluded": excluded,
            "decided_at": entry.get("decided_at"),
        }
    return out


def scope_of(prioritization: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return normalize_scope((prioritization or {}).get("service_scope"))


def resolve_scope(
    prioritization: dict[str, Any] | None, ref: Any
) -> tuple[str, dict[str, Any]] | None:
    """Find the scope entry a service OR one of its sub-services belongs to."""
    keys = _selection_keys(str(ref or ""))
    if not keys:
        return None
    scope = scope_of(prioritization)
    for sid, entry in scope.items():
        if keys & _selection_keys(sid, entry["name"]):
            return sid, entry
    for sid, entry in scope.items():
        for item in (*entry["offered"], *entry["excluded"]):
            if keys & _item_keys(item):
                return sid, entry
    return None


def sub_allowed(
    prioritization: dict[str, Any] | None,
    service_id: Any,
    sub_id: Any,
    sub_name: str = "",
) -> bool:
    """May this sub-service be used under this service?"""
    found = resolve_scope(prioritization, service_id)
    keys = _selection_keys(str(sub_id or ""), sub_name)
    if found is None:
        # Unscoped (legacy): an empty flat list has always meant "no filter".
        flat = {_norm_key(str(x)) for x in (prioritization or {}).get("selected_subservice_ids") or []}
        flat.discard("")
        return not flat or _norm_key(str(sub_id or "")) in flat
    return any(keys & _item_keys(o) for o in found[1]["offered"])


def _family(entry: dict[str, Any]) -> set[str]:
    """Distinctive tokens the service already covers, platform aliases expanded."""
    tokens: set[str] = set(_distinctive_tokens(str(entry.get("name") or "")))
    for item in entry.get("offered") or []:
        name = item["name"] if isinstance(item, dict) else str(item)
        tokens |= _distinctive_tokens(name)
    return _expand_platform_tokens(tokens)


def _text_tokens(text: str) -> set[str]:
    return {t for t in _loose_key(text).split() if t}


def excluded_hits(text: str, entry: dict[str, Any]) -> list[str]:
    """Names of excluded sub-services the text is about.

    Matching is by distinctive tokens, so "tiktok ads" and "tiktok marketing" both
    hit "TikTok Ads", while the shared head "ads" never does. An exclusion the
    service already covers through a platform alias (Facebook when Meta Ads is
    offered) is skipped — the alias family is treated as one offering.
    """
    tokens = _text_tokens(text)
    if not tokens:
        return []
    family = _family(entry)
    padded = f" {_loose_key(text)} "
    hits: list[str] = []
    for raw in entry.get("excluded") or []:
        name = raw["name"] if isinstance(raw, dict) else str(raw)
        need = _distinctive_tokens(name)
        if not need:  # all-generic name ("Social Ads"): match the whole phrase
            phrase = _loose_key(name)
            if phrase and f" {phrase} " in padded:
                hits.append(name)
            continue
        if need <= family:
            continue
        if need <= tokens:
            hits.append(name)
    return hits


def partition_scope_rejects(
    rows: list[dict[str, Any]], prioritization: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split keyword rows into (kept, dropped) by the originating service's scope."""
    if not scope_of(prioritization):
        return list(rows), []
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            kept.append(row)
            continue
        origin = str(row.get("target") or row.get("seed") or "")
        found = resolve_scope(prioritization, origin)
        hits = excluded_hits(str(row.get("keyword") or ""), found[1]) if found else []
        if hits:
            dropped.append(
                {
                    **row,
                    "relevance_drop_reason": REJECT_REASON,
                    "classifier_version": CLASSIFIER_VERSION,
                    "excluded_sub_service": hits[0],
                    "originating_service": origin,
                }
            )
        else:
            kept.append(row)
    return kept, dropped


def scope_signal(prioritization: dict[str, Any] | None, ref: Any) -> dict[str, Any] | None:
    """The scope a content run must honour, or None when there is nothing to say."""
    found = resolve_scope(prioritization, ref)
    if found:
        sid, entry = found
        return {
            "service": entry["name"],
            "service_id": sid,
            "scoped": True,
            "offered": [o["name"] for o in entry["offered"]],
            "excluded": [e["name"] for e in entry["excluded"]],
        }
    keys = _selection_keys(str(ref or ""))
    for svc in (prioritization or {}).get("service_catalog") or []:
        if not isinstance(svc, dict) or not (svc.get("subservices") or []):
            continue
        if keys & _selection_keys(str(svc.get("id") or ""), str(svc.get("name") or "")):
            return {
                "service": str(svc.get("name") or svc.get("id")),
                "service_id": _norm_key(str(svc.get("id") or svc.get("name") or "")),
                "scoped": False,
                "offered": [],
                "excluded": [],
            }
    return None


def scope_violations(texts: Iterable[str], signal: dict[str, Any] | None) -> list[dict[str, str]]:
    """Every (text, excluded sub-service) pair. Empty when unscoped."""
    if not signal or not signal.get("scoped"):
        return []
    entry = {
        "name": signal.get("service") or "",
        "offered": list(signal.get("offered") or []),
        "excluded": list(signal.get("excluded") or []),
    }
    found: list[dict[str, str]] = []
    for text in texts:
        for name in excluded_hits(str(text or ""), entry):
            found.append({"text": str(text), "sub_service": name})
    return found


def _parent_family(service_name: str) -> str | None:
    n = _norm_key(service_name)
    words = set(_loose_key(service_name).split())
    if "social" in words:
        return "social"
    if words & {"ppc", "sem"} or "paid search" in n or "google ads" in n:
        return "paid_search"
    return None


def suggest_exclusions(
    service_name: str, offered: Iterable[Any], already: Iterable[Any] = ()
) -> list[dict[str, str]]:
    """Commonly associated siblings the client does not offer."""
    family_key = _parent_family(service_name)
    if not family_key:
        return []
    entry = {"name": service_name, "offered": _items(offered)}
    covered = _family(entry)
    skip = {k for item in _items(already) for k in _item_keys(item)}
    out: list[dict[str, str]] = []
    for sibling in COMMON_SIBLINGS[family_key]:
        item = _item(sibling)
        assert item is not None
        if _item_keys(item) & skip:
            continue
        if _distinctive_tokens(sibling) <= covered:
            continue
        out.append(item)
    return out


def build_default_scope(
    catalog: list[dict[str, Any]], selected_service_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Proposed scope for each selected service, for the operator to confirm.

    Sub-services found on the client's own site (or typed in by the operator) are
    offered. Sub-services seen only on competitor sites are NOT.
    """
    selected = {_norm_key(str(x)) for x in selected_service_ids}
    out: dict[str, dict[str, Any]] = {}
    for svc in catalog:
        if not isinstance(svc, dict):
            continue
        sid = _norm_key(str(svc.get("id") or svc.get("name") or ""))
        if sid not in selected:
            continue
        subs = [s for s in svc.get("subservices") or [] if isinstance(s, dict)]
        offered = _items(s for s in subs if str(s.get("source") or "").lower() != "competitor")
        excluded = _items(s for s in subs if str(s.get("source") or "").lower() == "competitor")
        name = str(svc.get("name") or sid)
        excluded += suggest_exclusions(name, offered, excluded)
        out[sid] = {"name": name, "offered": offered, "excluded": excluded, "decided_at": None}
    return out


def finalize_scope(payload: dict[str, Any]) -> None:
    """Normalise the saved scope and keep the flat legacy id list in step with it."""
    scope = normalize_scope(payload.get("service_scope"))
    if not scope:
        payload["service_scope"] = {}
        return
    selected = {_norm_key(str(x)) for x in payload.get("selected_service_ids") or []}
    now = datetime.now(timezone.utc).isoformat()
    scope = {
        sid: {**entry, "decided_at": entry.get("decided_at") or now}
        for sid, entry in scope.items()
        if sid in selected
    }
    governed: set[str] = set()
    for svc in payload.get("service_catalog") or []:
        if not isinstance(svc, dict):
            continue
        if _norm_key(str(svc.get("id") or svc.get("name") or "")) not in scope:
            continue
        for sub in svc.get("subservices") or []:
            if isinstance(sub, dict):
                governed.add(_norm_key(str(sub.get("id") or sub.get("name") or "")))
    offered_ids = [o["id"] for entry in scope.values() for o in entry["offered"]]
    legacy = [
        str(x)
        for x in payload.get("selected_subservice_ids") or []
        if _norm_key(str(x)) not in governed and _norm_key(str(x)) not in set(offered_ids)
    ]
    payload["selected_subservice_ids"] = list(dict.fromkeys([*legacy, *offered_ids]))
    payload["service_scope"] = scope
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_service_scope.py -v`
Expected: all PASS. If `test_resolve_scope_by_service_slug_name_or_member_sub_service` fails on the slug, check that `_selection_keys` includes the loose key (it does: `social-media-marketing` → `social media marketing`).

---

### Task 2: Wire the scope into `service_prioritization.py` and site architecture

**Files:**
- Modify: `backend/app/services/service_prioritization.py` (`compact_selected_services` ~L166-268, `apply_service_selection` ~L955-966, `expand_vertical_subservice_seeds` ~L977-1017, `ia_nodes_from_competitor_tree` ~L1077-1173, `default_selection` ~L778-813, `build_prioritization_pack` ~L859-870, `confirm_prioritization` ~L1282-end)
- Modify: `backend/app/services/site_architecture.py` (the two `ia_nodes_from_competitor_tree(` calls, ~L601 and ~L958)
- Test: `backend/tests/test_service_scope_gates.py`

**Interfaces:**
- Consumes: Task 1 (`sub_allowed`, `resolve_scope`, `build_default_scope`, `finalize_scope`).
- Produces: `ia_nodes_from_competitor_tree(..., service_scope: dict | None = None)`; `default_selection(catalog)` result gains `"service_scope"`; `build_prioritization_pack(...)["existing_selection"]` may contain `"service_scope"`; `confirm_prioritization(payload)` returns a payload whose `service_scope` is normalised and stamped.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_service_scope_gates.py`:

```python
"""The scope is enforced everywhere the selection used to be advisory."""

from app.services.service_prioritization import (
    apply_service_selection,
    compact_selected_services,
    confirm_prioritization,
    default_selection,
    expand_vertical_subservice_seeds,
    ia_nodes_from_competitor_tree,
)

CATALOG = [
    {
        "id": "social media marketing",
        "name": "Social Media Marketing",
        "source": "website",
        "subservices": [
            {"id": "meta ads", "name": "Meta Ads", "source": "website", "path": "/smm/meta-ads"},
            {"id": "instagram ads", "name": "Instagram Ads", "source": "website"},
            {"id": "tiktok-ads", "name": "TikTok Ads", "source": "competitor"},
            {"id": "pinterest-ads", "name": "Pinterest Ads", "source": "competitor"},
        ],
    }
]

SCOPE = {
    "social media marketing": {
        "name": "Social Media Marketing",
        "offered": ["Meta Ads", "Instagram Ads"],
        "excluded": ["TikTok Ads", "Pinterest Ads"],
    }
}


def _prio(**extra):
    return {
        "confirmed_at": "x",
        "selected_service_ids": ["social media marketing"],
        # the old UI sent EVERY sub-service, competitor ones included
        "selected_subservice_ids": ["meta ads", "instagram ads", "tiktok-ads", "pinterest-ads"],
        "service_catalog": CATALOG,
        "service_scope": SCOPE,
        **extra,
    }


def test_default_selection_does_not_select_competitor_subservices():
    catalog = [dict(CATALOG[0], subservices=[dict(s) for s in CATALOG[0]["subservices"]])]
    result = default_selection(catalog)
    assert result["selected_subservice_ids"] == ["meta ads", "instagram ads"]
    assert [s["selected"] for s in catalog[0]["subservices"]] == [True, True, False, False]
    entry = result["service_scope"]["social media marketing"]
    assert [o["name"] for o in entry["offered"]] == ["Meta Ads", "Instagram Ads"]
    assert "TikTok Ads" in [e["name"] for e in entry["excluded"]]


def test_compact_selected_services_only_lists_offered_subservices():
    out = compact_selected_services(_prio(), service_catalog=CATALOG)
    assert [s["name"] for s in out[0]["subservices"]] == ["Meta Ads", "Instagram Ads"]


def test_compact_selected_services_empty_offered_means_no_subservices():
    prio = _prio(
        service_scope={"social media marketing": {"name": "SMM", "offered": [], "excluded": []}}
    )
    out = compact_selected_services(prio, service_catalog=CATALOG)
    assert out[0]["subservices"] == []


def test_compact_selected_services_unscoped_keeps_legacy_behaviour():
    prio = _prio()
    prio.pop("service_scope")
    out = compact_selected_services(prio, service_catalog=CATALOG)
    assert len(out[0]["subservices"]) == 4  # legacy flat list lists all four


def test_expand_vertical_seeds_skips_non_offered_subservices():
    roots, targets = expand_vertical_subservice_seeds(
        [], {}, prioritization=_prio(), service_catalog=CATALOG
    )
    assert sorted(roots) == ["Instagram Ads", "Meta Ads"]
    assert "tiktok ads" not in targets


def test_expand_vertical_seeds_scoped_empty_offered_adds_nothing():
    prio = _prio(service_scope={"social media marketing": {"name": "SMM", "offered": [], "excluded": []}})
    roots, _ = expand_vertical_subservice_seeds([], {}, prioritization=prio, service_catalog=CATALOG)
    assert roots == []


def test_apply_service_selection_drops_non_offered_subservice_seed():
    targets = {
        "meta ads": {"target": "Meta Ads", "target_type": "sub_service", "parent_segment": "social-media-marketing"},
        "tiktok ads": {"target": "TikTok Ads", "target_type": "sub_service", "parent_segment": "social-media-marketing"},
    }
    _services, kept = apply_service_selection(["Social Media Marketing"], targets, _prio())
    assert list(kept) == ["meta ads"]


def test_ia_nodes_exclude_non_offered_subservices():
    tree = {"name": "catalog", "services": CATALOG}
    nodes = ia_nodes_from_competitor_tree(
        tree,
        domain="example.com",
        selected_service_ids=["social media marketing"],
        selected_subservice_ids=["meta ads", "instagram ads", "tiktok-ads", "pinterest-ads"],
        service_scope=SCOPE,
    )
    subs = sorted(n["title"] for n in nodes if n["page_type"] == "subservice")
    assert subs == ["Instagram Ads", "Meta Ads"]


def test_confirm_prioritization_normalises_scope_and_derives_flat_ids():
    out = confirm_prioritization(
        {
            "selected_service_ids": ["social media marketing"],
            "selected_subservice_ids": ["meta ads", "instagram ads", "tiktok-ads", "pinterest-ads"],
            "service_catalog": CATALOG,
            "service_scope": SCOPE,
        }
    )
    assert out["selected_subservice_ids"] == ["meta ads", "instagram ads"]
    entry = out["service_scope"]["social media marketing"]
    assert entry["decided_at"]
    assert [e["id"] for e in entry["excluded"]] == ["tiktok ads", "pinterest ads"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_service_scope_gates.py -v`
Expected: FAIL (`TypeError: ia_nodes_from_competitor_tree() got an unexpected keyword argument 'service_scope'`, and assertion failures in the others).

- [ ] **Step 3: Implement**

All edits are in `backend/app/services/service_prioritization.py` unless stated. Import `service_scope` lazily inside each function (the new module imports this one at top level).

**3a. `compact_selected_services`.** Delete the `sub_filter` lines (L187-188) and replace the two uses.

Replace:
```python
    sub_filter = {_norm_key(str(x)) for x in (prio.get("selected_subservice_ids") or [])}
    sub_filter.discard("")
    catalog = [
```
with:
```python
    from app.services.service_scope import sub_allowed

    catalog = [
```
Replace (catalog loop):
```python
            sub_id = _norm_key(str(sub.get("id") or sub.get("name") or ""))
            if not sub_id or (sub_filter and sub_id not in sub_filter):
                continue
            subs.append({"id": sub_id, "name": str(sub.get("name") or sub_id)})
```
with:
```python
            sub_id = _norm_key(str(sub.get("id") or sub.get("name") or ""))
            if not sub_id or not sub_allowed(prio, sid, sub_id, str(sub.get("name") or "")):
                continue
            subs.append({"id": sub_id, "name": str(sub.get("name") or sub_id)})
```
Replace (IA node loop):
```python
        sub_id = _norm_key(str(node.get("subservice_id") or ""))
        if not sub_id or (sub_filter and sub_id not in sub_filter):
            continue
```
with:
```python
        sub_id = _norm_key(str(node.get("subservice_id") or ""))
        if not sub_id or not sub_allowed(prio, sid, sub_id, str(node.get("title") or "")):
            continue
```

**3b. `default_selection`.** Replace the non-candidate sub loop:
```python
        service_ids.append(svc["id"])
        for sub in svc.get("subservices") or []:
            sub["selected"] = True
            sub_ids.append(sub["id"])
        active_index += 1
    return {
        "selected_service_ids": service_ids,
        "selected_subservice_ids": sub_ids,
        "primary_service_ids": [s["id"] for s in active[:n_primary]],
    }
```
with:
```python
        service_ids.append(svc["id"])
        for sub in svc.get("subservices") or []:
            # Sub-services seen only on competitor sites are not something the
            # client offers; the operator opts them in, never the default.
            offered = str(sub.get("source") or "").lower() != "competitor"
            sub["selected"] = offered
            if offered:
                sub_ids.append(sub["id"])
        active_index += 1
    from app.services.service_scope import build_default_scope

    return {
        "selected_service_ids": service_ids,
        "selected_subservice_ids": sub_ids,
        "primary_service_ids": [s["id"] for s in active[:n_primary]],
        "service_scope": build_default_scope(catalog, service_ids),
    }
```

**3c. `build_prioritization_pack`.** In the `existing_selection` key tuple add `"service_scope",` after `"selected_subservice_ids",`.

**3d. `apply_service_selection`.** Replace:
```python
            if _selection_keys(target) & sel_subs or (not sel_subs and parent_match):
                filtered_targets[key] = meta
            continue
```
with:
```python
            from app.services.service_scope import resolve_scope, sub_allowed

            found = (resolve_scope(prioritization, parent_seg) if parent_seg else None) or (
                resolve_scope(prioritization, target)
            )
            if found is not None:
                if sub_allowed(prioritization, found[0], target, target):
                    filtered_targets[key] = meta
            elif _selection_keys(target) & sel_subs or (not sel_subs and parent_match):
                filtered_targets[key] = meta
            continue
```

**3e. `expand_vertical_subservice_seeds`.** Delete the line `sel_subs = {_norm_key(x) for x in (prioritization.get("selected_subservice_ids") or [])}` and add `from app.services.service_scope import sub_allowed` right after the `if not catalog or not prioritization.get("confirmed_at"): return ...` guard. Replace:
```python
            if sel_subs and sub_id not in sel_subs:
                continue
```
with:
```python
            if not sub_allowed(prioritization, sid, sub_id, str(sub.get("name") or "")):
                continue
```

**3f. `ia_nodes_from_competitor_tree`.** Add parameter `service_scope: dict[str, Any] | None = None,` after `adopt_service_ids`. After the `sel_subs = ...` line add:
```python
    from app.services.service_scope import sub_allowed

    scope_prio = {
        "service_scope": service_scope,
        "selected_subservice_ids": list(selected_subservice_ids or []),
    }
```
Replace:
```python
            if sel_subs and sub_id not in sel_subs:
                continue
```
with:
```python
            if not sub_allowed(scope_prio, sid, sub_id, sub_name):
                continue
```
Then delete the now-unused `sel_subs = {...}` line in that function.

**3g. `confirm_prioritization`.** Immediately after `out["primary_service_ids"] = list(out.get("primary_service_ids") or [])` add:
```python
    from app.services.service_scope import finalize_scope

    finalize_scope(out)
```
(`finalize_scope` runs before the adoption loop; it uses `out["service_catalog"]` as sent by the UI, which is what it needs.)

**3h. `site_architecture.py`.** In both `ia_nodes_from_competitor_tree(` calls, add after the `selected_subservice_ids=list(...)` argument:
```python
            service_scope=prioritization.get("service_scope"),
```
(match the indentation of the surrounding arguments in each call).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_service_scope_gates.py tests/test_service_scope.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing related suites**

Run: `python -m pytest tests/test_service_prioritization.py tests/test_service_prioritization_api.py tests/test_content_planning.py tests/test_top_priority_service.py tests/test_service_competitors.py tests/test_site_map_accuracy.py -v`
Expected: PASS. A failure from a test that expected a competitor sub-service to be auto-selected (`test_service_prioritization.py` around the `default_selection` assertions) is the old leak: update that expectation and report it. Any other failure is a regression — fix the code.

---

### Task 3: API accepts and persists `service_scope`

**Files:**
- Modify: `backend/app/api/clients.py` (`ServicePrioritizationUpdate` L683-693, `save_service_prioritization` L773-787)
- Test: `backend/tests/test_service_prioritization_api.py` (append)

**Interfaces:**
- Consumes: `confirm_prioritization` from Task 2.
- Produces: `PUT /clients/{id}/service-prioritization` accepts `service_scope: dict`; stored under `commercial_scope.service_prioritization.service_scope`.

- [ ] **Step 1: Write the failing test** (append to `backend/tests/test_service_prioritization_api.py`)

```python
async def test_confirm_persists_service_scope_and_derives_flat_ids(db_session, api_client):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "scope-hod@test.com")

    resp = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={
            "selected_service_ids": ["social media marketing"],
            "selected_subservice_ids": ["meta ads", "tiktok-ads"],
            "service_catalog": [
                {
                    "id": "social media marketing",
                    "name": "Social Media Marketing",
                    "subservices": [
                        {"id": "meta ads", "name": "Meta Ads"},
                        {"id": "tiktok-ads", "name": "TikTok Ads"},
                    ],
                }
            ],
            "service_scope": {
                "social media marketing": {
                    "name": "Social Media Marketing",
                    "offered": [{"id": "meta ads", "name": "Meta Ads"}],
                    "excluded": [{"id": "tiktok-ads", "name": "TikTok Ads"}],
                }
            },
            "confirmed": True,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()["service_prioritization"]
    assert saved["selected_subservice_ids"] == ["meta ads"]
    entry = saved["service_scope"]["social media marketing"]
    assert entry["decided_at"]
    assert [e["name"] for e in entry["excluded"]] == ["TikTok Ads"]

    profile = (
        await db_session.execute(
            select(ClientDigitalProfile).where(ClientDigitalProfile.client_id == client.id)
        )
    ).scalar_one()
    await db_session.refresh(profile)
    stored = profile.commercial_scope["service_prioritization"]
    assert stored["service_scope"]["social media marketing"]["offered"][0]["name"] == "Meta Ads"


async def test_confirm_without_scope_still_works_and_stays_unscoped(db_session, api_client):
    client = await _client(db_session)
    _user, token = await make_user(db_session, "head_of_department", "scope-hod2@test.com")
    resp = await api_client.put(
        f"/api/v1/clients/{client.id}/service-prioritization",
        json={"selected_service_ids": ["seo"], "confirmed": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["service_prioritization"]["service_scope"] == {}
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_service_prioritization_api.py -v -k scope`
Expected: FAIL — the first test's `selected_subservice_ids` is `["meta ads", "tiktok-ads"]` because the field is not passed through.

- [ ] **Step 3: Implement**

In `ServicePrioritizationUpdate` add after `service_catalog`:
```python
    service_scope: dict = {}
```
In `save_service_prioritization`, inside the `if body.confirmed` dict (the one with `"service_catalog": body.service_catalog,`), add:
```python
            "service_scope": body.service_scope,
```
(The unconfirmed branch uses `model_dump`, which already includes it.)

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_service_prioritization_api.py -v`
Expected: PASS (new and old).

---

### Task 4: Keyword gate drops keywords about non-offered sub-services

**Files:**
- Modify: `backend/app/services/keyword_validation_gate.py` (`validate_keywords` signature ~L490, hard-rule block ~L555-575)
- Modify: `backend/app/agents/search_demand.py` (the `validate_keywords(` call ~L1197)
- Test: `backend/tests/test_service_scope_gates.py` (append)

**Interfaces:**
- Consumes: `partition_scope_rejects` from Task 1.
- Produces: `validate_keywords(..., prioritization: dict | None = None)`; dropped rows carry `relevance_drop_reason == "sub_service_not_offered"`; `audit["scope_dropped"]` is an int.

- [ ] **Step 1: Write the failing test** (append to `test_service_scope_gates.py`)

```python
from app.services.keyword_validation_gate import validate_keywords


async def test_validate_keywords_drops_non_offered_subservice_keywords_without_llm():
    rows = [
        {"keyword": "meta ads agency melbourne", "target": "meta ads"},
        {"keyword": "tiktok ads agency melbourne", "target": "meta ads"},
    ]
    kept, dropped, audit = await validate_keywords(
        rows,
        client_id="c1",
        services=["Social Media Marketing"],
        enabled=False,  # LLM judge off: scope drop must not depend on it
        prioritization=_prio(),
    )
    assert [r["keyword"] for r in kept] == ["meta ads agency melbourne"]
    assert [r["keyword"] for r in dropped] == ["tiktok ads agency melbourne"]
    assert dropped[0]["relevance_drop_reason"] == "sub_service_not_offered"
    assert audit["scope_dropped"] == 1


async def test_validate_keywords_without_prioritization_is_unchanged():
    rows = [{"keyword": "tiktok ads agency melbourne", "target": "meta ads"}]
    kept, dropped, audit = await validate_keywords(
        rows, client_id="c1", services=[], enabled=False
    )
    assert audit["scope_dropped"] == 0
    assert dropped == [] or all(d.get("relevance_drop_reason") != "sub_service_not_offered" for d in dropped)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_service_scope_gates.py -v -k validate_keywords`
Expected: FAIL — `TypeError: validate_keywords() got an unexpected keyword argument 'prioritization'`.

- [ ] **Step 3: Implement**

In `validate_keywords` add the parameter `prioritization: dict[str, Any] | None = None,` after `geography`. In `audit` initial dict add `"scope_dropped": 0,`. Replace:

```python
    from app.services.keyword_service_contract import partition_service_rejects

    rows, hard_dropped = partition_service_rejects(
        rows, cdd_keywords=cdd_keywords, geography=geography
    )
```
with:
```python
    from app.services.keyword_service_contract import partition_service_rejects
    from app.services.service_scope import partition_scope_rejects

    # Operator-declared scope first: a keyword about a sub-service we do not
    # offer is dropped before any ontology or LLM judgement is spent on it.
    rows, scope_dropped = partition_scope_rejects(rows, prioritization)
    audit["scope_dropped"] = len(scope_dropped)
    rows, contract_dropped = partition_service_rejects(
        rows, cdd_keywords=cdd_keywords, geography=geography
    )
    hard_dropped = [*scope_dropped, *contract_dropped]
```
The existing loop right after (`audit["hard_rule_dropped"] = len(hard_dropped)` and the sample loop) then covers both lists unchanged. The sample entry uses `row.get("relevance_drop_reason")`, which scope rows set.

In `search_demand.py`, add `prioritization=prioritization,` to the `validate_keywords(` call (after `geography=...`). `prioritization` is already defined in that function (L690).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_service_scope_gates.py -v -k validate_keywords`
Expected: PASS.

- [ ] **Step 5: Confirm the rejection log picks the new reason up**

Run: `python -m pytest tests -k "keyword and (validation or rejection or relevance or accounting)" -q`
Expected: PASS. Then read `search_demand.py` right after the `validate_keywords` call: the loop that builds `rejected_keywords` uses `row.get("relevance_drop_reason")`, so `sub_service_not_offered` is logged with `source: "service_contract"` (it has a `classifier_version`). No change needed; if that loop ever stops reading `relevance_drop_reason`, fix it there.

---

### Task 5: Content context carries the scope and the prompt states it

**Files:**
- Modify: `backend/app/services/content_signals/context.py` (`build_content_context`, `render_context_block`)
- Modify: `backend/app/services/content_signals/validation.py` (`business_rules_block` only — additive)
- Modify: `backend/app/agents/content_production.py` (`_build` inside `_content_context_builder`)
- Test: `backend/tests/test_service_scope_gates.py` (append)

**Interfaces:**
- Consumes: `scope_signal` from Task 1.
- Produces: `build_content_context(..., service_id: str | None = None)`; context gains top-level `"service_scope": dict | None` (shape from `scope_signal`). It is deliberately NOT a weighted signal, so it can never be switched off and never changes the relevance score.

- [ ] **Step 1: Write the failing tests** (append)

```python
from app.services.content_signals.context import build_content_context, render_context_block
from app.services.content_signals.validation import business_rules_block


def _commercial():
    return {"service_prioritization": _prio()}


def test_context_carries_scope_for_the_service():
    ctx = build_content_context(
        keyword="meta ads melbourne",
        page_type="service",
        commercial=_commercial(),
        service_id="social media marketing",
    )
    assert ctx["service_scope"]["scoped"] is True
    assert ctx["service_scope"]["offered"] == ["Meta Ads", "Instagram Ads"]
    assert "TikTok Ads" in ctx["service_scope"]["excluded"]


def test_context_without_service_or_scope_has_none():
    ctx = build_content_context(keyword="x", page_type="blog", commercial={})
    assert ctx["service_scope"] is None


def test_scope_does_not_change_the_relevance_score():
    with_scope = build_content_context(
        keyword="k", commercial=_commercial(), service_id="social media marketing"
    )
    without = build_content_context(keyword="k", commercial={})
    assert with_scope["relevance"]["score"] == without["relevance"]["score"]


def test_prompt_block_and_rules_state_offered_and_excluded():
    ctx = build_content_context(
        keyword="meta ads", commercial=_commercial(), service_id="social media marketing"
    )
    block = render_context_block(ctx)
    assert "Meta Ads, Instagram Ads" in block
    assert "TikTok Ads" in block and "Never" in block
    assert "TikTok Ads" in business_rules_block(ctx)


def test_unscoped_service_gets_a_warning_note_not_a_rule():
    prio = _prio(service_catalog=CATALOG)
    prio.pop("service_scope")
    ctx = build_content_context(
        keyword="k", commercial={"service_prioritization": prio}, service_id="social media marketing"
    )
    assert ctx["service_scope"]["scoped"] is False
    assert "not been confirmed" in render_context_block(ctx)
    assert business_rules_block(ctx) == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_service_scope_gates.py -v -k "context or prompt or unscoped"`
Expected: FAIL (`unexpected keyword argument 'service_id'`).

- [ ] **Step 3: Implement**

`context.py`: add import `from app.services.service_scope import scope_signal`. Add parameter `service_id: str | None = None,` to `build_content_context` (after `page_type`). Before the `return {` add:
```python
    prioritization = dict(commercial.get("service_prioritization") or {})
    service_scope = scope_signal(prioritization, service_id) if service_id else None
```
and add `"service_scope": service_scope,` to the returned dict (after `"funnel_stage": stage,`).

In `render_context_block`, immediately after the `lines` header list is created (before the `for name in ...` loop) add:
```python
    scope = context.get("service_scope") or {}
    if scope.get("scoped"):
        offered = ", ".join(scope.get("offered") or []) or "none (service page only)"
        lines.append(
            f"\n[service scope · {scope.get('service')}] Offered sub-services: {offered}. "
            + (
                "Never mention, compare against, or promote: "
                + ", ".join(scope.get("excluded") or [])
                + ". "
                if scope.get("excluded")
                else ""
            )
            + "Do not introduce any sub-service that is not in the offered list."
        )
    elif scope:
        lines.append(
            f"\n[service scope · {scope.get('service')}] The offered sub-services for this "
            "service have not been confirmed. Do not add sub-services beyond what the brief names."
        )
```

`validation.py` — in `business_rules_block`, before the `if not lines: return ""` line, add:
```python
    scope = context.get("service_scope") or {}
    if scope.get("scoped"):
        offered = ", ".join(scope.get("offered") or []) or "none"
        lines.append(f"- Sub-services: only {offered} are offered.")
        if scope.get("excluded"):
            lines.append(
                "- Never name or promote: " + ", ".join(scope["excluded"]) + "."
            )
```

`agents/content_production.py` — in `_build`, pass the service: add `service_id=service_id,` to the `build_content_context(` call (after `page_type=page_type,`).

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_service_scope_gates.py tests/test_content_signals_context.py tests/test_content_business_validation.py -v`
Expected: PASS. If `test_content_signals_context.py` asserts the exact key set of the context dict, add `"service_scope"` to its expected keys and report it.

---

### Task 6: Refuse before generation, and check the finished draft

**Files:**
- Modify: `backend/app/services/create_content.py` (`write_one_page`, after the `related_keywords(...)` call ~L2241)
- Modify: `backend/app/services/content_signals/validation.py` (`validate_business_logic` — additive)
- Modify: `backend/app/services/phase_validation/deterministic.py` (new check + `_CHECK_FNS` entry), `backend/app/services/phase_validation/criteria.py` (~L328)
- Test: `backend/tests/test_service_scope_gates.py` (append)

**Interfaces:**
- Consumes: `scope_violations` (Task 1), `content_context["service_scope"]` (Task 5).
- Produces: `write_one_page` returns `{"ok": False, "refused": True, "route_to": "service_scope", "reason": ..., "scope_violations": [...], "keyword":, "url":}` before any LLM or image spend; `business_validation["checks"]` gains `sub_service_scope` (severity `fail`); phase check id `production_sub_service_scope`.

- [ ] **Step 1: Write the failing tests** (append)

```python
import pytest

from app.services.content_signals.validation import validate_business_logic
from app.services.create_content import write_one_page
from app.services.phase_validation.deterministic import _check_production_sub_service_scope

SCOPED_CTX = {
    "signals": {},
    "service_scope": {
        "service": "Social Media Marketing",
        "service_id": "social media marketing",
        "scoped": True,
        "offered": ["Meta Ads", "Instagram Ads"],
        "excluded": ["TikTok Ads", "Pinterest Ads"],
    },
}


def test_validate_business_logic_fails_when_draft_names_an_excluded_sub_service():
    result = validate_business_logic(
        {"markdown": "We run Meta Ads and TikTok Ads for retailers."}, SCOPED_CTX
    )
    check = next(c for c in result["checks"] if c["name"] == "sub_service_scope")
    assert check["ok"] is False and check["severity"] == "fail"
    assert "TikTok Ads" in check["detail"]
    assert result["ok"] is False


def test_validate_business_logic_passes_when_draft_stays_in_scope():
    result = validate_business_logic({"markdown": "We run Meta Ads and Instagram Ads."}, SCOPED_CTX)
    check = next(c for c in result["checks"] if c["name"] == "sub_service_scope")
    assert check["ok"] is True


def test_validate_business_logic_skips_scope_check_when_unscoped():
    result = validate_business_logic({"markdown": "TikTok Ads"}, {"signals": {}})
    assert not any(c["name"] == "sub_service_scope" for c in result["checks"])


def _brief(**over):
    brief = {
        "keyword": "tiktok ads agency melbourne",
        "url": "/social-media-marketing/tiktok-ads/",
        "parent": "/social-media-marketing/",
        "action": "create",
        "writer_ready": True,
        "author": "Sam Writer",
        "differentiation": "A case-study-led angle with original conversion data from three clients.",
    }
    brief.update(over)
    return brief


async def test_write_one_page_refuses_before_generation_on_excluded_keyword():
    out = await write_one_page(
        brief=_brief(),
        client_name="Acme",
        use_llm=False,
        enhance_brief=False,
        content_context=SCOPED_CTX,
    )
    assert out["ok"] is False and out["refused"] is True
    assert out["route_to"] == "service_scope"
    assert out["scope_violations"][0]["sub_service"] == "TikTok Ads"
    assert "TikTok Ads" in out["reason"]


async def test_write_one_page_refuses_when_a_secondary_keyword_is_excluded():
    out = await write_one_page(
        brief=_brief(keyword="meta ads agency melbourne", secondary_keywords=["pinterest ads cost"]),
        client_name="Acme",
        use_llm=False,
        enhance_brief=False,
        content_context=SCOPED_CTX,
    )
    assert out["refused"] is True and out["route_to"] == "service_scope"


async def test_write_one_page_in_scope_is_not_refused_for_scope():
    out = await write_one_page(
        brief=_brief(keyword="meta ads agency melbourne"),
        client_name="Acme",
        use_llm=False,
        enhance_brief=False,
        content_context=SCOPED_CTX,
    )
    assert out.get("route_to") != "service_scope"


def test_phase_check_fails_on_a_draft_with_a_scope_failure():
    drafts = [
        {
            "business_validation": {
                "failures": [
                    {"name": "sub_service_scope", "ok": False, "detail": "Draft names TikTok Ads."}
                ]
            }
        }
    ]
    results = _check_production_sub_service_scope(
        criteria=None, output={"drafts": drafts}, company_ctx={}, prior={}
    )
    assert results[0].status == "failed"
    assert results[0].check_id == "production_sub_service_scope"


def test_phase_check_passes_with_no_scope_failures_and_ignores_non_list_drafts():
    ok = _check_production_sub_service_scope(
        criteria=None, output={"drafts": [{"business_validation": {"failures": []}}]}, company_ctx={}, prior={}
    )
    assert ok[0].status == "passed"
    count_only = _check_production_sub_service_scope(
        criteria=None, output={"drafts": 1}, company_ctx={}, prior={}
    )
    assert count_only[0].status == "passed"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_service_scope_gates.py -v -k "validate_business or write_one_page or phase_check"`
Expected: FAIL (no `sub_service_scope` check; `ImportError` for `_check_production_sub_service_scope`; write path not refusing).

- [ ] **Step 3: Implement**

**3a. `validation.py` — `validate_business_logic`.** Add `from app.services.service_scope import scope_violations` inside the function (lazy, keeps import order safe). Before the `failures = [...]` line add:
```python
    scope = context.get("service_scope") or {}
    if scope.get("scoped"):
        found = scope_violations([text], scope)
        named = list(dict.fromkeys(v["sub_service"] for v in found))
        _check(
            "sub_service_scope",
            not named,
            "Only offered sub-services are named."
            if not named
            else f"Draft names a sub-service we do not offer: {', '.join(named[:3])}.",
        )
```

**3b. `create_content.py` — `write_one_page`.** `scope_violations` is imported lazily. Right after the `_primary, related = related_keywords(...)` statement (and before `specs = collect_image_specs(...)`), add:
```python
    scope = (content_context or {}).get("service_scope") or {}
    if scope.get("scoped"):
        from app.services.service_scope import scope_violations

        found = scope_violations(
            [
                _primary,
                str(brief.get("title") or ""),
                *[str(r) for r in related],
            ],
            scope,
        )
        if found:
            named = ", ".join(dict.fromkeys(v["sub_service"] for v in found))
            return {
                "ok": False,
                "refused": True,
                "route_to": "service_scope",
                "reason": (
                    f"This brief targets a sub-service the client does not offer ({named}). "
                    f"Offered under {scope.get('service')}: "
                    f"{', '.join(scope.get('offered') or []) or 'none'}. "
                    "Change the brief or update the service scope."
                ),
                "scope_violations": found,
                "keyword": brief.get("keyword"),
                "url": brief.get("url"),
            }
```

**3c. `deterministic.py`.** Add after `_check_production_action_fit`:
```python
def _check_production_sub_service_scope(
    *,
    criteria: PhaseCriteria | None,
    output: dict[str, Any],
    company_ctx: dict[str, Any],
    prior: dict[str, Any],
) -> list[CheckResult]:
    """A draft that names a sub-service the client does not offer must be revised."""
    _ = criteria, company_ctx, prior
    drafts = output.get("drafts")
    offenders: list[str] = []
    if isinstance(drafts, list):
        for draft in drafts:
            if not isinstance(draft, dict):
                continue
            validation = draft.get("business_validation")
            failures = (validation or {}).get("failures") if isinstance(validation, dict) else None
            for failure in failures or []:
                if isinstance(failure, dict) and failure.get("name") == "sub_service_scope":
                    offenders.append(str(failure.get("detail") or "sub-service not offered"))
    if offenders:
        return [
            _fail(
                "production_sub_service_scope",
                "phase_objective_compliance",
                "Draft names a sub-service the client does not offer.",
                severity=CheckSeverity.CRITICAL.value,
                evidence="; ".join(offenders[:3]),
                correction=(
                    "Rewrite the draft using only the offered sub-services listed in the "
                    "service scope; remove every mention of the excluded ones."
                ),
            )
        ]
    return [
        _pass(
            "production_sub_service_scope",
            "phase_objective_compliance",
            "Draft stays within the offered sub-services.",
        )
    ]
```
Register it: add `"production_sub_service_scope": _check_production_sub_service_scope,` to `_CHECK_FNS` after `production_action_fit`. In `criteria.py` (~L328) change the tuple to
`("output_present", "production_one_page", "production_action_fit", "production_sub_service_scope")`.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_service_scope_gates.py -v`
Expected: PASS. If `write_one_page` raises before reaching the scope block in the in-scope test, read the traceback: `use_llm=False` with `enhance_brief=False` should only need the brief keys in `_brief()`; adjust the helper brief (not the gate) to satisfy `draft_gate`.

- [ ] **Step 5: Run the existing content suites**

Run: `python -m pytest tests/test_create_content.py tests/test_content_pipeline.py tests/test_content_business_validation.py tests/test_content_signals_context.py tests/test_comparison_draft.py tests/test_phase_validation*.py -q`
Expected: PASS. Any test counting the exact checks tuple of the production phase needs `production_sub_service_scope` added; report it.

---

### Task 7: Selection UI — per-service sub-service panel

**Files:**
- Modify: `frontend/src/components/cards/ServicePrioritizationCard.tsx`

**Interfaces:**
- Consumes: pack fields `defaults.service_scope` and `existing_selection.service_scope` (Task 2), PUT field `service_scope` (Task 3). Backend scope item shape `{id, name}`.
- Produces: confirm payload `{ …, selected_subservice_ids: <flattened offered ids>, service_scope: { [serviceId]: { name, offered, excluded } } }`.

Behaviour: each *checked* service gets an expandable "Sub-services" panel. Catalog sub-services are checkboxes (checked = offered). Unchecked catalog subs and typed names become the excluded list, shown as removable chips. Two inputs: "Add a sub-service we offer" (adds a manual catalog sub, offered) and "Never mention" (adds an excluded chip). Backend-suggested exclusions arrive pre-filled in `defaults.service_scope[id].excluded`. A service that has sub-services or suggestions must be reviewed ("Looks right" or any change) before confirm enables; "Accept defaults for all" reviews everything at once.

- [ ] **Step 1: Types and pure helpers.** Directly under `function isOpportunity(...)` add:

```tsx
type SubItem = { id: string; name: string };
type ScopeEntry = { name: string; offered: SubItem[]; excluded: SubItem[] };
type ScopeMap = Record<string, ScopeEntry>;

function subItems(raw: unknown): SubItem[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((x) =>
      typeof x === "string"
        ? { id: x.trim().toLowerCase(), name: x.trim() }
        : x && typeof x === "object"
          ? {
              id: String((x as Record<string, unknown>).id || (x as Record<string, unknown>).name || ""),
              name: String((x as Record<string, unknown>).name || (x as Record<string, unknown>).id || ""),
            }
          : { id: "", name: "" },
    )
    .filter((x) => x.id && x.name);
}

function asScopeMap(raw: unknown): ScopeMap {
  const out: ScopeMap = {};
  if (!raw || typeof raw !== "object") return out;
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    if (!value || typeof value !== "object") continue;
    const v = value as Record<string, unknown>;
    out[key] = {
      name: String(v.name || key),
      offered: subItems(v.offered),
      excluded: subItems(v.excluded),
    };
  }
  return out;
}

// Mirrors the backend default: sub-services seen only on competitor sites are
// not something the client offers until the operator says so.
function defaultScopeFor(svc: Record<string, unknown>): ScopeEntry {
  const subs = asCatalog(svc.subservices).map((s) => ({
    id: String(s.id),
    name: String(s.name || s.id),
    competitor: String(s.source || "").toLowerCase() === "competitor",
  }));
  return {
    name: String(svc.name || svc.id),
    offered: subs.filter((s) => !s.competitor).map(({ id, name }) => ({ id, name })),
    excluded: subs.filter((s) => s.competitor).map(({ id, name }) => ({ id, name })),
  };
}
```

- [ ] **Step 2: State.** In the component, after `const [selectionKey, ...]` add:

```tsx
  const [scope, setScope] = React.useState<ScopeMap>(() => ({
    ...asScopeMap(defaults.service_scope),
    ...asScopeMap(existing.service_scope),
  }));
  // Services whose sub-services the operator has looked at. Anything already
  // saved with a scope counts as reviewed; fresh defaults do not.
  const [reviewed, setReviewed] = React.useState<Set<string>>(
    () => new Set(Object.keys(asScopeMap(existing.service_scope))),
  );
  const [openScope, setOpenScope] = React.useState<string | null>(null);
  const [newSub, setNewSub] = React.useState("");
  const [newExcluded, setNewExcluded] = React.useState("");

  const scopeFor = (svc: Record<string, unknown>): ScopeEntry =>
    scope[String(svc.id)] || defaultScopeFor(svc);

  const needsReview = (svc: Record<string, unknown>): boolean =>
    asCatalog(svc.subservices).length > 0 ||
    (asScopeMap(defaults.service_scope)[String(svc.id)]?.excluded.length || 0) > 0;

  const pendingReview = catalog.filter(
    (svc) =>
      selectedServices.has(String(svc.id)) && needsReview(svc) && !reviewed.has(String(svc.id)),
  );

  const writeScope = (svc: Record<string, unknown>, next: ScopeEntry) => {
    const id = String(svc.id);
    setScope((prev) => ({ ...prev, [id]: next }));
    setReviewed((prev) => new Set(prev).add(id));
  };

  const toggleSub = (svc: Record<string, unknown>, sub: SubItem, offer: boolean) => {
    const cur = scopeFor(svc);
    const rest = (list: SubItem[]) => list.filter((x) => x.id !== sub.id);
    writeScope(svc, {
      ...cur,
      offered: offer ? [...rest(cur.offered), sub] : rest(cur.offered),
      excluded: offer ? rest(cur.excluded) : [...rest(cur.excluded), sub],
    });
  };

  const addOfferedSub = (svc: Record<string, unknown>) => {
    const name = newSub.trim().replace(/\s+/g, " ");
    if (!name) return;
    const sub = { id: name.toLowerCase(), name };
    const id = String(svc.id);
    setCatalog((prev) =>
      prev.map((row) =>
        String(row.id) === id && !asCatalog(row.subservices).some((s) => String(s.id) === sub.id)
          ? { ...row, subservices: [...asCatalog(row.subservices), { ...sub, source: "manual" }] }
          : row,
      ),
    );
    const cur = scopeFor(svc);
    writeScope(svc, {
      ...cur,
      offered: [...cur.offered.filter((x) => x.id !== sub.id), sub],
      excluded: cur.excluded.filter((x) => x.id !== sub.id),
    });
    setNewSub("");
  };

  const addExcluded = (svc: Record<string, unknown>) => {
    const name = newExcluded.trim().replace(/\s+/g, " ");
    if (!name) return;
    const sub = { id: name.toLowerCase(), name };
    const cur = scopeFor(svc);
    if (cur.offered.some((x) => x.id === sub.id)) return;
    writeScope(svc, {
      ...cur,
      excluded: [...cur.excluded.filter((x) => x.id !== sub.id), sub],
    });
    setNewExcluded("");
  };

  const removeExcluded = (svc: Record<string, unknown>, subId: string) => {
    const cur = scopeFor(svc);
    writeScope(svc, { ...cur, excluded: cur.excluded.filter((x) => x.id !== subId) });
  };
```

- [ ] **Step 3: Replace the old flattening helper and the confirm payload.** Delete the comment and `subserviceIdsFor` callback (the "Sub-services are no longer selectable here…" block). In `handleConfirm`, before `await onConfirm({`, add:

```tsx
      const scopePayload: ScopeMap = {};
      for (const svc of catalog) {
        const id = String(svc.id);
        if (selectedServices.has(id) && (scope[id] || needsReview(svc))) {
          scopePayload[id] = scopeFor(svc);
        }
      }
      const offeredIds = Object.values(scopePayload).flatMap((e) => e.offered.map((o) => o.id));
```
and replace `selected_subservice_ids: subserviceIdsFor(selectedServices),` with:
```tsx
        selected_subservice_ids: offeredIds,
        service_scope: scopePayload,
```
Also change the guard at the top of `handleConfirm` to `if (!canAct || saving || pendingReview.length > 0) return;`.

- [ ] **Step 4: Render the panel.** In `renderServiceRow`, replace the `{subs.length ? ( <p …>includes N sub-services</p> ) : null}` block with:

```tsx
        {checked && needsReview(svc) ? (
          <div style={{ margin: "6px 0 0 24px" }}>
            <button
              type="button"
              className="cc-btn cc-btn--ghost"
              style={{ fontSize: 11 }}
              onClick={() => setOpenScope(openScope === id ? null : id)}
              aria-expanded={openScope === id}
            >
              {openScope === id ? "Hide sub-services" : "Sub-services we offer"}
              {reviewed.has(id) ? " ✓" : " — review needed"}
            </button>
            {openScope === id ? (
              <div style={{ marginTop: 8, fontSize: 12 }}>
                {subs.map((sub) => {
                  const item = { id: String(sub.id), name: String(sub.name || sub.id) };
                  const offered = scopeFor(svc).offered.some((o) => o.id === item.id);
                  const fromCompetitor = String(sub.source || "").toLowerCase() === "competitor";
                  return (
                    <label key={item.id} style={{ display: "flex", gap: 6, marginBottom: 4 }}>
                      <input
                        type="checkbox"
                        checked={offered}
                        disabled={!canAct}
                        onChange={(event) => toggleSub(svc, item, event.target.checked)}
                      />
                      {item.name}
                      <span style={{ color: "var(--muted)" }}>
                        {fromCompetitor ? "seen on competitors — not offered by default" : "on your site"}
                      </span>
                    </label>
                  );
                })}
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", margin: "6px 0" }}>
                  {scopeFor(svc)
                    .excluded.filter((x) => !subs.some((s) => String(s.id) === x.id))
                    .map((x) => (
                      <span
                        key={x.id}
                        style={{
                          fontSize: 11,
                          padding: "2px 8px",
                          borderRadius: 999,
                          border: "1px solid var(--line)",
                        }}
                      >
                        Never mention: {x.name}
                        {canAct ? (
                          <button
                            type="button"
                            aria-label={`Remove ${x.name}`}
                            onClick={() => removeExcluded(svc, x.id)}
                            style={{ marginLeft: 4, background: "none", border: 0, cursor: "pointer", color: "inherit" }}
                          >
                            ×
                          </button>
                        ) : null}
                      </span>
                    ))}
                </div>
                {canAct ? (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <input
                      type="text"
                      value={newSub}
                      onChange={(event) => setNewSub(event.target.value)}
                      placeholder="Add a sub-service we offer"
                      aria-label="Add a sub-service we offer"
                      style={{ flex: "1 1 180px", padding: "6px 8px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", color: "inherit" }}
                    />
                    <button type="button" className="cc-btn cc-btn--ghost" disabled={!newSub.trim()} onClick={() => addOfferedSub(svc)}>
                      Add
                    </button>
                    <input
                      type="text"
                      value={newExcluded}
                      onChange={(event) => setNewExcluded(event.target.value)}
                      placeholder="Never mention (e.g. TikTok Ads)"
                      aria-label="Never mention"
                      style={{ flex: "1 1 180px", padding: "6px 8px", borderRadius: 8, border: "1px solid var(--line)", background: "transparent", color: "inherit" }}
                    />
                    <button type="button" className="cc-btn cc-btn--ghost" disabled={!newExcluded.trim()} onClick={() => addExcluded(svc)}>
                      Block
                    </button>
                    <button
                      type="button"
                      className="cc-btn cc-btn--primary"
                      onClick={() => {
                        writeScope(svc, scopeFor(svc));
                        setOpenScope(null);
                      }}
                    >
                      Looks right
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
```

- [ ] **Step 5: Update the explanatory copy and gate the confirm button.** In the "Industry services (80/20)" paragraph replace `Sub-services are included automatically with their parent service.` with `Open "Sub-services we offer" on each selected service and tick only the ones you actually provide — everything else is kept out of keyword research and content.` Above the confirm button (inside the `canAct ?` branch, before `<button`) add:

```tsx
          {pendingReview.length ? (
            <p style={{ fontSize: 12, color: "var(--warning, #9a6700)", margin: "0 0 8px" }}>
              Review the sub-services for: {pendingReview.map((s) => String(s.name)).join(", ")}.{" "}
              <button
                type="button"
                className="cc-btn cc-btn--ghost"
                style={{ fontSize: 11 }}
                onClick={() =>
                  setReviewed((prev) => {
                    const next = new Set(prev);
                    pendingReview.forEach((s) => next.add(String(s.id)));
                    return next;
                  })
                }
              >
                Accept defaults for all
              </button>
            </p>
          ) : null}
```
and change the confirm button's `disabled` to `saving || selectedServices.size === 0 || pendingReview.length > 0`.

- [ ] **Step 6: Type-check and build**

Run (from `frontend/`): `npm run build`
Expected: succeeds with no TypeScript errors. Fix type errors in the new code only; do not change unrelated files. (No frontend test runner exists, so also read the diff once for stale references to `subserviceIdsFor` — there must be none.)

---

### Task 8: End-to-end verification

**Files:** none modified.

- [ ] **Step 1: Full backend suite**

Run (from `backend/`): `python -m pytest tests -q -x --ignore=tests/test_dataforseo_keyword_enrichment.py`
Expected: PASS. (The ignored file is the user's uncommitted work-in-progress; run it separately and report its status without editing it.)

- [ ] **Step 2: Scenario script — the Social Media Marketing case**

Run (from `backend/`):
```bash
python - <<'PY'
from app.services.service_scope import partition_scope_rejects, scope_signal, scope_violations
prio = {"service_scope": {"social media marketing": {
    "name": "Social Media Marketing",
    "offered": ["Meta Ads", "Instagram Ads"],
    "excluded": ["TikTok Ads", "LinkedIn Ads", "Pinterest Ads", "Twitter/X Ads"]}}}
rows = [{"keyword": k, "target": "meta ads"} for k in
        ["meta ads agency", "instagram ads cost", "tiktok ads agency", "pinterest ads melbourne", "linkedin ads"]]
kept, dropped = partition_scope_rejects(rows, prio)
print("kept   ", [r["keyword"] for r in kept])
print("dropped", [r["keyword"] for r in dropped])
print(scope_violations(["We also run TikTok Ads"], scope_signal(prio, "social media marketing")))
PY
```
Expected: kept = `meta ads agency`, `instagram ads cost`; dropped = the other three; violations lists `TikTok Ads`.

- [ ] **Step 3: Report**

State plainly: which tests were run and passed, which old tests were updated because they encoded the competitor-auto-select leak, what was NOT run (the UI was type-checked and built but not exercised in a browser), and that legacy clients stay unscoped until the operator re-confirms the selection card.
