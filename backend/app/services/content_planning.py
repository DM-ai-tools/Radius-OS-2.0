"""Phase 9 — lock a page roadmap by joining strategy + architecture + audit.

Zero judgement: every field is copied from an upstream pack or derived by catalog.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

PRIORITY_TIERS = frozenset({"quick_win", "big_bet", "fill_in", "avoid"})
PAGE_TYPES = frozenset(
    {
        "home",
        "hub",
        "spoke",
        "pillar",
        "cluster",
        "article",
        "blog",
        "service",
        "product",
        "location",
        "commercial",
        "supporting",
        "utility",
    }
)
TIER_ALIASES = {
    "quick win": "quick_win",
    "quick_win": "quick_win",
    "big bet": "big_bet",
    "big_bet": "big_bet",
    "fill-in": "fill_in",
    "fill in": "fill_in",
    "fill_in": "fill_in",
    "fillin": "fill_in",
    "avoid": "avoid",
}
PAGE_TYPE_ALIASES = {
    "planned": "article",
    "page": "article",
    "post": "article",
    "guide": "article",
}
DISPOSITION_ALIASES = {
    "keep": "KEEP",
    "refresh": "REFRESH",
    "optimise": "OPTIMISE",
    "optimize": "OPTIMISE",
    "retitle": "RETITLE",
    "consolidate": "CONSOLIDATE",
    "noindex": "NOINDEX",
    "delete_candidate": "DELETE_CANDIDATE",
    "delete": "DELETE_CANDIDATE",
    "retire": "DELETE_CANDIDATE",
}
REFRESH_DISPOSITIONS = frozenset({"REFRESH", "OPTIMISE", "RETITLE"})
RETIRE_DISPOSITIONS = frozenset({"CONSOLIDATE", "NOINDEX", "DELETE_CANDIDATE"})
TIER_RANK = {"quick_win": 0, "big_bet": 1, "fill_in": 2, "avoid": 3}
ACTION_RANK = {"refresh": 0, "create": 1, "no_action": 2, "retire": 3}


def url_n(url: str | None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        path = urlparse(raw).path or "/"
    else:
        path = raw if raw.startswith("/") else f"/{raw}"
    path = path.lower()
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def _tier(row: dict[str, Any]) -> str | None:
    raw = row.get("priority_tier") or row.get("priority") or row.get("priority_label") or row.get("bucket")
    if raw is None or raw == "":
        return None
    key = str(raw).strip().lower()
    return TIER_ALIASES.get(key) or TIER_ALIASES.get(key.replace("_", " ")) or TIER_ALIASES.get(key.replace("-", " "))


def _page_type(row: dict[str, Any]) -> str | None:
    raw = row.get("page_type") or row.get("type")
    if not raw:
        return None
    key = str(raw).strip().lower()
    if key in PAGE_TYPES:
        return key
    return PAGE_TYPE_ALIASES.get(key, key)


def _disposition(row: dict[str, Any] | None) -> str | None:
    if not row:
        return None
    raw = row.get("disposition")
    if raw is None or raw == "":
        return None
    s = str(raw).strip()
    upper = s.upper().replace(" ", "_")
    if upper in DISPOSITION_ALIASES.values() or upper in (
        "KEEP",
        "REFRESH",
        "OPTIMISE",
        "RETITLE",
        "CONSOLIDATE",
        "NOINDEX",
        "DELETE_CANDIDATE",
    ):
        if upper == "OPTIMIZE":
            return "OPTIMISE"
        return DISPOSITION_ALIASES.get(s.lower(), upper)
    return DISPOSITION_ALIASES.get(s.lower())


def _action_from_disposition(disp: str | None) -> str:
    if not disp:
        return "create"
    if disp in REFRESH_DISPOSITIONS:
        return "refresh"
    if disp in RETIRE_DISPOSITIONS:
        return "retire"
    if disp == "KEEP":
        return "no_action"
    return "create"


def _content_action_label(*, action: str, disposition: str | None, page_type: str | None) -> str:
    """Canonical action family used downstream for optimize-vs-create decisions."""
    if action == "create":
        return "NEW_CONTENT"
    if action == "retire":
        if disposition == "CONSOLIDATE":
            return "CONSOLIDATE_CONTENT"
        return "REDIRECT_OR_RETIRE"
    if action == "refresh":
        if page_type in {"pillar", "hub"}:
            return "PILLAR_OPTIMIZATION"
        if page_type in {"cluster", "supporting", "spoke"}:
            return "CLUSTER_CONTENT_OPTIMIZATION"
        if disposition == "RETITLE":
            return "UPDATE_EXISTING"
        if disposition == "OPTIMISE":
            return "OPTIMIZE_EXISTING"
        return "UPDATE_EXISTING"
    return "LEAVE_UNCHANGED"


def _token_set(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(t) > 1}


def _keyword_overlap(a: str, b: str) -> bool:
    na = " ".join(str(a or "").lower().split())
    nb = " ".join(str(b or "").lower().split())
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    at, bt = _token_set(na), _token_set(nb)
    if len(at) < 2 or len(bt) < 2:
        return False
    return len(at & bt) / max(1, min(len(at), len(bt))) >= 0.7


def _audit_keyword_matches(
    audit: dict[str, Any] | None,
    *,
    keyword: str | None,
) -> list[dict[str, Any]]:
    if not audit or not keyword:
        return []
    matches: list[dict[str, Any]] = []
    for row in list(audit.get("inventory") or []) + list(audit.get("refresh_queue") or []):
        if not isinstance(row, dict):
            continue
        probe = str(row.get("keyword") or row.get("primary_keyword") or row.get("title") or "")
        if _keyword_overlap(keyword, probe):
            matches.append(row)
    return matches


def _breadcrumb_list(raw: Any, parent: str | None, path: str) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if isinstance(raw, str) and raw.strip():
        if ">" in raw:
            return [p.strip() for p in raw.split(">") if p.strip()]
        return [raw.strip()]
    bits = ["Home"]
    if parent and url_n(parent) not in ("", "/"):
        bits.append(url_n(parent).strip("/").replace("-", " ").title())
    if path not in ("", "/"):
        bits.append(path.strip("/").split("/")[-1].replace("-", " ").title())
    return bits


def _indexable(row: dict[str, Any]) -> bool | None:
    if "indexable" in row:
        val = row.get("indexable")
        if isinstance(val, bool):
            return val
        if str(val).lower() in ("false", "0", "no"):
            return False
        if str(val).lower() in ("true", "1", "yes"):
            return True
    return None


def ia_gate_ok(site_architecture_status: str | None, site_architecture: dict[str, Any]) -> bool:
    if (site_architecture_status or "") == "complete":
        return True
    tree = site_architecture.get("target_url_tree") or []
    if not isinstance(tree, list) or not tree:
        return False
    return any(isinstance(n, dict) and (n.get("url") or n.get("path")) for n in tree)


def _strategy_rows(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key in ("priority_queue", "combined_priority_queue", "priority_pages"):
        for row in strategy.get(key) or []:
            if isinstance(row, dict):
                out.append(row)
    return out


def _arch_rows(architecture: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (architecture.get("target_url_tree") or []) if isinstance(r, dict)]


def _audit_by_url(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for key in ("dispositions", "inventory", "refresh_queue"):
        for row in audit.get(key) or []:
            if not isinstance(row, dict):
                continue
            key_n = url_n(str(row.get("url") or row.get("path") or ""))
            if key_n:
                by[key_n] = row
    return by


def _cluster_owners(architecture: dict[str, Any]) -> dict[str, str]:
    owners: dict[str, str] = {}
    for row in architecture.get("cluster_owners") or architecture.get("cluster_ownership") or []:
        if not isinstance(row, dict):
            continue
        cluster = str(row.get("cluster") or "").strip().lower()
        owner = url_n(str(row.get("owner_url") or row.get("canonical_owner_url") or row.get("url") or ""))
        if cluster and owner:
            owners[cluster] = owner
    return owners


def _strategy_url(row: dict[str, Any]) -> str:
    return url_n(str(row.get("url") or row.get("suggested_url") or row.get("path") or ""))


def _norm_kw(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _row_keyword(row: dict[str, Any]) -> str:
    return _norm_kw(row.get("keyword") or row.get("primary_keyword"))


def build_roadmap(
    strategy: dict[str, Any],
    architecture: dict[str, Any],
    audit: dict[str, Any] | None = None,
    *,
    strict: bool = False,
    client_name: str | None = None,
    primary_url: str | None = None,
) -> dict[str, Any]:
    """Join three packs on url_n. Does not invent topics, URLs, or dispositions."""
    strat_rows = _strategy_rows(strategy)
    arch_rows = _arch_rows(architecture)
    audit_by = _audit_by_url(audit or {})
    owners = _cluster_owners(architecture)

    strat_by: dict[str, dict[str, Any]] = {}
    strat_no_url: list[dict[str, Any]] = []
    for row in strat_rows:
        key = _strategy_url(row)
        if key:
            strat_by.setdefault(key, row)
        else:
            strat_no_url.append(row)

    arch_by: dict[str, dict[str, Any]] = {}
    for row in arch_rows:
        key = url_n(str(row.get("url") or row.get("path") or ""))
        if key:
            arch_by.setdefault(key, row)

    # URL map from site architecture — attach crawled URLs to clusters before join.
    from app.services.url_mapping import lookup_url_map_entry

    for entry in architecture.get("final_url_map") or []:
        if not isinstance(entry, dict) or entry.get("action") == "CREATE":
            continue
        mapped = url_n(str(entry.get("selected_url") or ""))
        if not mapped or mapped == "/":
            continue
        if mapped not in arch_by:
            arch_by[mapped] = {
                "url": mapped,
                "path": mapped,
                "cluster": entry.get("cluster"),
                "keyword": entry.get("primary_keyword"),
                "primary_keyword": entry.get("primary_keyword"),
                "from_url_map": True,
                "url_score": entry.get("url_score"),
                "url_map_action": entry.get("action"),
                "score_band": entry.get("score_band"),
            }

    # 1. Exact URL join.
    merged_keys = sorted(set(strat_by) & set(arch_by))
    pairs: list[tuple[str, dict[str, Any], dict[str, Any], str | None]] = [
        (k, strat_by[k], arch_by[k], None) for k in merged_keys
    ]

    # 2. Reconcile same-topic/different-URL rows before excluding either side.
    #    Phase 6 and Phase 6b routinely slug the same keyword under different parents
    #    (/guides/local-seo-pricing vs /blog/local-seo-pricing). A URL-only join drops
    #    BOTH rows, so a planned page silently disappears and is double-reported as a
    #    strategy_only + architecture_only gap. Match those on keyword and keep the
    #    Site Architecture URL — the url_conflict_winner declared in
    #    content_queue.build_combined_priority_queue.
    arch_leftover_by_kw: dict[str, list[str]] = {}
    for key, row in arch_by.items():
        if key in strat_by:
            continue
        nk = _row_keyword(row)
        if nk:
            arch_leftover_by_kw.setdefault(nk, []).append(key)
    for cands in arch_leftover_by_kw.values():
        cands.sort()

    claimed_arch: set[str] = set()
    # A keyword already planned by the exact-URL join must not get a second page from
    # reconciliation — two URLs on one keyword is the cannibalisation this phase exists
    # to prevent.
    planned_kw: set[str] = {
        nk for nk in (_row_keyword(s) or _row_keyword(a) for _k, s, a, _n in pairs) if nk
    }

    def _claim_arch_for(nk: str) -> str | None:
        if nk in planned_kw:
            return None
        for cand in arch_leftover_by_kw.get(nk, []):
            if cand not in claimed_arch:
                claimed_arch.add(cand)
                planned_kw.add(nk)
                return cand
        return None

    reconciled_strat_urls: set[str] = set()
    reconciled_no_url: set[int] = set()

    for key in sorted(set(strat_by) - set(arch_by)):
        row = strat_by[key]
        nk = _row_keyword(row)
        if not nk:
            continue
        cand = _claim_arch_for(nk)
        if cand:
            pairs.append(
                (cand, row, arch_by[cand], f"url_conflict_resolved: strategy {key} → architecture {cand}")
            )
            reconciled_strat_urls.add(key)

    for idx, row in enumerate(strat_no_url):
        nk = _row_keyword(row)
        if not nk:
            continue
        cand = _claim_arch_for(nk)
        if cand:
            pairs.append((cand, row, arch_by[cand], f"url_assigned_from_architecture: {cand}"))
            reconciled_no_url.add(idx)

    # 3. Whatever is still unmatched is a genuine gap.
    excluded: list[dict[str, Any]] = []
    for idx, row in enumerate(strat_no_url):
        if idx in reconciled_no_url:
            continue
        excluded.append(
            {
                "url_n": None,
                "reason": "strategy_only — topic planned but no URL/parent assigned",
                "source_pack": "strategy",
                "title": row.get("title"),
                "keyword": row.get("keyword") or row.get("primary_keyword"),
            }
        )
    for key, row in strat_by.items():
        if key not in arch_by and key not in reconciled_strat_urls:
            excluded.append(
                {
                    "url_n": key,
                    "reason": "strategy_only — topic planned but no URL/parent assigned",
                    "source_pack": "strategy",
                    "title": row.get("title"),
                    "keyword": row.get("keyword") or row.get("primary_keyword"),
                }
            )
    for key, row in arch_by.items():
        if key not in strat_by and key not in claimed_arch:
            excluded.append(
                {
                    "url_n": key,
                    "reason": "architecture_only — URL in taxonomy with no content plan",
                    "source_pack": "architecture",
                    "title": row.get("title"),
                    "keyword": row.get("keyword") or row.get("primary_keyword"),
                }
            )

    pages: list[dict[str, Any]] = []
    for key, s, a, reconcile_note in pairs:
        au = audit_by.get(key)
        disp = _disposition(au)
        inferred_existing = None
        if not disp:
            kw_probe = str(
                s.get("keyword")
                or s.get("primary_keyword")
                or a.get("keyword")
                or a.get("primary_keyword")
                or ""
            ).strip()
            kw_matches = _audit_keyword_matches(audit, keyword=kw_probe)
            if kw_matches:
                inferred_existing = kw_matches[0]
                disp = _disposition(inferred_existing) or "REFRESH"
        action = _action_from_disposition(disp)
        cluster = str(s.get("cluster") or s.get("pillar") or a.get("cluster") or "").strip()
        primary = s.get("keyword") or s.get("primary_keyword") or a.get("keyword") or a.get("primary_keyword")
        url_map_entry = lookup_url_map_entry(
            {"final_url_map": architecture.get("final_url_map") or []},
            cluster=cluster,
            keyword=str(primary or ""),
        )
        if url_map_entry:
            map_action = url_map_entry.get("action")
            mapped_url = url_n(str(url_map_entry.get("selected_url") or ""))
            if map_action == "OPTIMIZE_EXISTING":
                action = "refresh"
                disp = disp or "OPTIMISE"
            elif map_action == "REVIEW_MERGE_REDIRECT":
                action = "refresh"
            if mapped_url and mapped_url != "/":
                key = mapped_url
        owner = owners.get(cluster.lower()) if cluster else None
        cannibal = bool(owner and owner != key)
        depth = a.get("depth")
        try:
            depth_i = int(depth) if depth is not None else (key.count("/") or 1)
        except (TypeError, ValueError):
            depth_i = key.count("/") or 1
        parent = a.get("parent") or a.get("parent_url")
        raw_crumbs = a.get("breadcrumb")
        crumbs = _breadcrumb_list(raw_crumbs, str(parent) if parent else None, key)
        indexable = _indexable(a)
        tier = _tier(s)
        ptype = _page_type(a)
        flags: list[str] = []
        if reconcile_note:
            flags.append("url_reconciled")
        if cannibal:
            flags.append("cannibal_conflict")
        if inferred_existing:
            flags.append("existing_intent_match")
        if url_map_entry:
            flags.append(f"url_map_{str(url_map_entry.get('score_band') or 'mapped').lower()}")
        if tier in ("quick_win", "big_bet") and depth_i > 4:
            flags.append("depth_gt_4")
        if indexable is False and action in ("create", "refresh"):
            flags.append("non_indexable_active")
        if raw_crumbs and crumbs and len(crumbs) != depth_i + 1:
            flags.append("breadcrumb_mismatch")
        kws = (
            s.get("secondary_keywords")
            or s.get("supporting_keywords")
            or s.get("keywords")
            or a.get("secondary_keywords")
            or a.get("supporting_keywords")
            or []
        )
        if isinstance(kws, str):
            kws = [kws]
        primary = s.get("keyword") or s.get("primary_keyword") or a.get("keyword") or a.get("primary_keyword")
        title = s.get("title") or a.get("title") or primary
        score = s.get("opportunity_score") or s.get("priority_score") or 0
        try:
            score_n = float(score)
        except (TypeError, ValueError):
            score_n = 0.0
        existing_match_url = None
        if inferred_existing:
            existing_match_url = inferred_existing.get("url") or inferred_existing.get("path")
        # Create rows must stamp a create-gap basis (validator rejects audit_disposition on create).
        if action == "create":
            if url_map_entry and url_map_entry.get("action") == "CREATE":
                decision_basis = "url_map_create"
            elif inferred_existing:
                decision_basis = "existing_content_match"
            else:
                decision_basis = "new_content_gap"
        else:
            decision_basis = "audit_disposition" if au else "new_content_gap"
            if url_map_entry:
                if url_map_entry.get("action") == "OPTIMIZE_EXISTING":
                    decision_basis = "url_map_optimize_existing"
                elif url_map_entry.get("action") == "REVIEW_MERGE_REDIRECT":
                    decision_basis = "url_map_review_merge"
                elif url_map_entry.get("action") == "CREATE":
                    decision_basis = "url_map_create"
            if inferred_existing and not str(decision_basis).startswith("url_map"):
                decision_basis = "existing_content_match"
        content_action = _content_action_label(
            action=action,
            disposition=disp,
            page_type=ptype,
        )
        new_content_justification = None
        if action == "create":
            new_content_justification = (
                "No relevant existing page matched by URL/keyword in content-audit inventory."
            )
        secondary = [str(k).strip() for k in kws if str(k).strip() and str(k).strip().lower() != str(primary or "").strip().lower()][:8]
        pages.append(
            {
                "url_n": key,
                "url": a.get("url") or s.get("url") or s.get("suggested_url") or key,
                "path": key,
                "action": action,
                "content_action": content_action,
                "disposition": disp,
                "disposition_reason": (au or {}).get("reason") if au else None,
                "decision_basis": decision_basis,
                "existing_content_evidence_url": existing_match_url,
                "existing_content_check": "matched_existing" if inferred_existing else "no_match",
                "new_content_justification": new_content_justification,
                "effort": (au or {}).get("effort") if au else None,
                "title": title,
                "primary_keyword": primary,
                "keyword": primary,
                "secondary_keywords": secondary,
                "supporting_keywords": secondary,
                "keywords": secondary,
                "intent": s.get("intent") or a.get("intent"),
                "funnel": s.get("funnel") or a.get("funnel"),
                "angle": s.get("angle") or a.get("angle"),
                "business_fit": s.get("business_fit") or a.get("business_fit"),
                "from_phase5_topic": bool(s.get("from_phase5_topic")),
                "competitor_domains": list(s.get("beat_competitors") or s.get("competitor_domains") or []),
                "content_type": s.get("content_type") or a.get("content_type"),
                "image_suggestions": s.get("image_suggestions") or [],
                "cluster": cluster or None,
                "parent": parent,
                "parent_url_n": url_n(str(parent)) if parent else None,
                "depth": depth_i,
                "breadcrumb": crumbs,
                "page_type": ptype,
                "indexable": indexable,
                "priority_tier": tier,
                "priority_score": score_n,
                "cannibal_conflict": cannibal,
                "flags": flags,
                "reconcile_note": reconcile_note,
                "status": "planned",
                "url_map_score": url_map_entry.get("url_score") if url_map_entry else None,
                "url_map_band": url_map_entry.get("score_band") if url_map_entry else None,
                "url_map_action": url_map_entry.get("action") if url_map_entry else None,
            }
        )

    pages.sort(
        key=lambda r: (
            TIER_RANK.get(str(r.get("priority_tier") or ""), 9),
            ACTION_RANK.get(str(r.get("action") or ""), 9),
            -float(r.get("priority_score") or 0),
            str(r.get("url_n") or ""),
        )
    )
    for i, row in enumerate(pages, 1):
        row["priority_rank"] = i
        row["wave"] = 1 if row.get("priority_tier") == "quick_win" else 2 if row.get("priority_tier") == "big_bet" else 3

    lock_problems: list[str] = []
    seen: set[str] = set()
    for row in pages:
        key = str(row.get("url_n") or "")
        if key in seen:
            lock_problems.append(f"duplicate url_n {key}")
        seen.add(key)
        if row.get("action") == "create":
            if not row.get("title"):
                lock_problems.append(f"create {key} missing title")
            if not row.get("primary_keyword"):
                lock_problems.append(f"create {key} missing primary_keyword")
        ptype = row.get("page_type")
        if ptype and ptype not in PAGE_TYPES:
            lock_problems.append(f"{key} unrecognised page_type {ptype}")
        tier = row.get("priority_tier")
        if tier and tier not in PRIORITY_TIERS:
            lock_problems.append(f"{key} unrecognised priority_tier {tier}")

    flagged = sum(1 for r in pages if r.get("flags"))
    if strict:
        if flagged:
            lock_problems.append(f"{flagged} row(s) have structural/cannibal flags")
        if excluded:
            lock_problems.append(f"{len(excluded)} excluded row(s)")

    locked = not lock_problems
    by_action = {
        "create": sum(1 for r in pages if r.get("action") == "create"),
        "refresh": sum(1 for r in pages if r.get("action") == "refresh"),
        "retire": sum(1 for r in pages if r.get("action") == "retire"),
        "no_action": sum(1 for r in pages if r.get("action") == "no_action"),
    }
    briefable = [r for r in pages if r.get("action") in ("create", "refresh")]
    from app.services.phase_pipeline import enrich_phase89_pack

    return enrich_phase89_pack(
        {
        "card_type": "content_planning_report",
        "client_name": client_name,
        "primary_url": primary_url,
        "locked": locked,
        "lock_reason": None if locked else "; ".join(lock_problems[:8]),
        "pages": pages,
        "roadmap": pages,
        "excluded": excluded,
        "summary": {
            "by_action": by_action,
            "merged": len(pages),
            "excluded": len(excluded),
            "flagged": flagged,
            "briefable": len(briefable),
        },
        "planned_count": len(pages),
        "create_count": by_action["create"],
        "refresh_count": by_action["refresh"],
        "retire_count": by_action["retire"],
        "no_action_count": by_action["no_action"],
        "excluded_count": len(excluded),
        "source": "content_planning",
        "note": (
            "Locked merge of strategy + architecture + audit. No new topics invented. "
            "Phase 10 briefs pages with action create|refresh in priority_rank order. "
            "Re-run this merge if any upstream pack changes."
            if locked
            else f"Unlocked — {'; '.join(lock_problems[:4])}. Phase 10 must not brief from this report."
        ),
        }
    )


def validate_report(report: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """Re-check lock conditions on an existing report."""
    pages = report.get("pages") or report.get("roadmap") or []
    rebuilt = build_roadmap(
        {"priority_queue": pages},
        {
            "target_url_tree": pages,
            "cluster_owners": [
                {"cluster": p.get("cluster"), "owner_url": p.get("url_n")}
                for p in pages
                if isinstance(p, dict) and p.get("cluster")
            ],
        },
        {
            "dispositions": [
                {
                    "url": p.get("url_n"),
                    "disposition": p.get("disposition"),
                    "reason": p.get("disposition_reason"),
                }
                for p in pages
                if isinstance(p, dict) and p.get("disposition")
            ]
        },
        strict=strict,
        client_name=report.get("client_name"),
        primary_url=report.get("primary_url"),
    )
    # Prefer validating the stored pages in place rather than re-joining
    lock_problems: list[str] = []
    seen: set[str] = set()
    for row in pages:
        if not isinstance(row, dict):
            continue
        key = str(row.get("url_n") or url_n(str(row.get("url") or row.get("path") or "")))
        if key in seen:
            lock_problems.append(f"duplicate url_n {key}")
        seen.add(key)
        if row.get("action") == "create":
            if not (row.get("title") or row.get("primary_keyword")):
                lock_problems.append(f"create {key} missing title/keyword")
            if not row.get("primary_keyword") and not row.get("keyword"):
                lock_problems.append(f"create {key} missing primary_keyword")
        ptype = row.get("page_type")
        if ptype and ptype not in PAGE_TYPES:
            lock_problems.append(f"{key} unrecognised page_type {ptype}")
        tier = row.get("priority_tier")
        if tier and tier not in PRIORITY_TIERS:
            lock_problems.append(f"{key} unrecognised priority_tier {tier}")
    if strict:
        if any(isinstance(r, dict) and r.get("flags") for r in pages):
            lock_problems.append("flags present")
        if report.get("excluded"):
            lock_problems.append("excluded rows present")
    locked = not lock_problems
    return {
        "locked": locked,
        "lock_reason": None if locked else "; ".join(lock_problems[:8]),
        "ok": locked,
        "rebuilt_locked": rebuilt.get("locked"),
    }


async def run_content_planning_plan(
    *,
    client_name: str,
    primary_url: str,
    site_architecture_status: str | None = None,
    site_architecture: dict[str, Any] | None = None,
    seo_strategy: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    website: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    seo_strategy_status: str | None = None,
    website_status: str | None = None,
    content_audit_status: str | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    _ = search_demand, marketing, industry
    ia = dict(site_architecture or {})
    strategy = dict(seo_strategy or {})
    audit = dict(content_audit or {})
    site = dict(website or {})

    if not ia_gate_ok(site_architecture_status, ia):
        return {
            "blocked": True,
            "locked": False,
            "reason": (
                "Content Planning needs an approved Site Architecture blueprint "
                "(target_url_tree with URL + parent + depth). "
                "Run and approve Site Architecture first."
            ),
        }

    queue = _strategy_rows(strategy)
    if (seo_strategy_status or "") != "complete" and not queue:
        return {
            "blocked": True,
            "locked": False,
            "reason": (
                "Content Planning joins approved Content Strategy with architecture. "
                "Approve Phase 6 strategy first — this phase does not invent topics."
            ),
        }

    has_live_site = (website_status or "") == "complete" or bool(
        site.get("sample_urls")
        or (isinstance(site.get("crawl"), dict) and site.get("crawl"))
    )
    has_audit = bool(
        audit.get("dispositions") or audit.get("inventory") or audit.get("refresh_queue")
    )
    if has_live_site and (content_audit_status or "") not in ("complete", "pending_signoff") and not has_audit:
        return {
            "blocked": True,
            "locked": False,
            "reason": (
                "Established site — approve Content Audit (Phase 8) first so existing pages "
                "get dispositions. Without audit, every URL would be treated as create."
            ),
        }

    if has_live_site and has_audit:
        inventory = list(audit.get("inventory") or [])
        with_disposition = sum(
            1
            for row in inventory
            if isinstance(row, dict) and row.get("disposition") not in (None, "")
        )
        if inventory and with_disposition == 0:
            return {
                "blocked": True,
                "locked": False,
                "reason": (
                    "Content Audit inventory exists but has no dispositions yet. "
                    "Classify existing pages first so planning can decide optimize/update/"
                    "consolidate before any new creation."
                ),
            }

    return build_roadmap(
        strategy,
        ia,
        audit,
        strict=strict,
        client_name=client_name,
        primary_url=primary_url,
    )
