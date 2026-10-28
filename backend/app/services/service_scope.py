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
    NOT_OFFERED_SOURCES,
    _PLATFORM_TOKEN_ALIASES,
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


def _platform_exclusion_hits(text: str, entry: dict[str, Any]) -> list[str]:
    """Excluded sub-services this text names by a platform, such as TikTok.

    Used when a keyword has no owning service. A broad exclusion like "Organic
    Social" is not applied here, so an SEO term that merely says "organic" stays.
    """
    platforms = set().union(*_PLATFORM_TOKEN_ALIASES.values())
    return [
        name
        for name in excluded_hits(text, entry)
        if _distinctive_tokens(name) & platforms
    ]


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
        origin = str(row.get("service") or row.get("target") or row.get("seed") or "")
        found = resolve_scope(prioritization, origin)
        keyword = str(row.get("keyword") or "")
        if found:
            hits = excluded_hits(keyword, found[1])
        else:
            # A keyword with no owning service still names a switched-off platform.
            hits = []
            for sid, entry in scope_of(prioritization).items():
                hits = _platform_exclusion_hits(keyword, entry)
                if hits:
                    origin = str(entry.get("name") or sid)
                    break
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
    service_name: str,
    offered: Iterable[Any],
    already: Iterable[Any] = (),
    existing: Iterable[Any] = (),
) -> list[dict[str, str]]:
    """Commonly associated siblings the client does not offer.

    ``existing`` are sub-services already listed under the service. A sibling one of
    them already covers ("Bing Ads" when "Bing Ads Management" is listed) is skipped.
    """
    family_key = _parent_family(service_name)
    if not family_key:
        return []
    entry = {"name": service_name, "offered": _items(offered)}
    covered = _family(entry)
    skip = {k for item in _items(already) for k in _item_keys(item)}
    listed = [_distinctive_tokens(str(n)) for n in existing]
    out: list[dict[str, str]] = []
    for sibling in COMMON_SIBLINGS[family_key]:
        item = _item(sibling)
        if item is None:
            continue
        if _item_keys(item) & skip:
            continue
        tokens = _distinctive_tokens(sibling)
        if tokens <= covered:
            continue
        if tokens and any(tokens <= other for other in listed):
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
        offered = _items(
            s for s in subs if str(s.get("source") or "").lower() not in NOT_OFFERED_SOURCES
        )
        excluded = _items(
            s for s in subs if str(s.get("source") or "").lower() in NOT_OFFERED_SOURCES
        )
        name = str(svc.get("name") or sid)
        excluded += suggest_exclusions(
            name, offered, excluded, existing=[str(s.get("name") or "") for s in subs]
        )
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
