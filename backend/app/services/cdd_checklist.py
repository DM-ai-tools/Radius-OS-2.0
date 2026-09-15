"""APSA CDD checklist — required fields, priorities, sub-phase load (no LLM).

Pure Python validation against ``discovery_fields`` catalog. Use from the CLI
script or call ``analyse_cdd`` with a dict / JSON / xlsx path.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from app.services.discovery_fields import (
    ACCESS_FIELDS,
    CDD_LABEL_ALIASES,
    CLIENT_ONLY_FIELDS,
    DISCOVERY_FIELDS,
    FIELD_META,
    FIELD_SECTIONS,
    OBJECTIVE_OPTIONS,
    RESEARCH_FIELDS,
)

# ---------------------------------------------------------------------------
# Priority tiers — what the pipeline actually needs
# ---------------------------------------------------------------------------

# P0 — cannot start SEO work without these
TIER_P0_CRITICAL = (
    "inferred_industry",
    "business_keywords",
    "products",
    "products_for_promotion",
    "geographic_focus",
    "competitors",
    "b2b_b2c",
)

# P1 — needed for strategy / content quality
TIER_P1_HIGH = (
    "business_model",
    "positioning",
    "target_demographic",
    "business_goal",
    "objectives",
    "brand_guidelines",
)

# P2 — commercial + measurement targets
TIER_P2_COMMERCIAL = (
    "average_ticket_size",
    "lifetime_value",
    "lead_modes",
    "seo_traffic_current",
    "seo_traffic_target",
    "sales_promises",
)

# P3 — tracking / publish access (Phase 2 + 12)
TIER_P3_ACCESS = ACCESS_FIELDS

# P4 — useful SWOT / depth, not blockers
TIER_P4_OPTIONAL = (
    "strengths",
    "weaknesses",
    "opportunities",
    "threats",
    "strategy_approach",
    "industry_targeting",
    "revenue_split",
    "sales_cycle",
    "seasonality",
    "content_creation_notes",
    "blogs_notes",
    "other_marketing_spend",
    "sem_leads_current",
    "sem_leads_target",
    "public_reviews_summary",
    "social_presence",
    "google_business_signals",
)

TIER_ORDER = (
    ("P0", "critical", TIER_P0_CRITICAL),
    ("P1", "high", TIER_P1_HIGH),
    ("P2", "commercial", TIER_P2_COMMERCIAL),
    ("P3", "access", TIER_P3_ACCESS),
    ("P4", "optional", TIER_P4_OPTIONAL),
)

# Which pipeline phases a missing field blocks
FIELD_BLOCKS_PHASE: dict[str, tuple[str, ...]] = {
    "inferred_industry": ("discovery", "search_demand", "content_strategy"),
    "business_keywords": ("search_demand", "content_strategy", "content_production"),
    "products": ("search_demand", "content_strategy"),
    "products_for_promotion": ("search_demand", "content_strategy", "content_planning"),
    "geographic_focus": ("search_demand", "competitor", "content_strategy"),
    "competitors": ("competitor", "search_demand"),
    "b2b_b2c": ("content_strategy", "content_brief"),
    "business_model": ("discovery", "content_strategy"),
    "positioning": ("content_brief", "create_content"),
    "target_demographic": ("content_strategy", "content_brief"),
    "business_goal": ("content_strategy", "content_planning"),
    "objectives": ("content_strategy", "content_planning"),
    "brand_guidelines": ("create_content", "on_page_seo"),
    "analytics_access": ("tracking",),
    "search_console_access": ("tracking",),
    "gtm_access": ("tracking",),
    "cms_access": ("publishing",),
    "hosting_access": ("publishing", "technical_seo"),
    "google_ads_access": ("tracking",),
    "google_business_access": ("tracking", "site_architecture"),
    "access_level": ("tracking",),
}

# Discovery sub-phases (D1–D5) — load distribution, no LLM
SUB_PHASES: list[dict[str, Any]] = [
    {
        "id": "D1",
        "name": "Public pre-research",
        "owner": "client_success_manager",
        "fields": list(RESEARCH_FIELDS),
        "load_weight": 25,
        "work": (
            "Fill researchable CDD keys from public site / GBP / reviews / competitors. "
            "Tag confidence. Do not invent AOV, LTV, or access passwords."
        ),
    },
    {
        "id": "D2",
        "name": "Client questionnaire confirm",
        "owner": "client_success_manager",
        "fields": list(CLIENT_ONLY_FIELDS) + list(TIER_P0_CRITICAL),
        "load_weight": 30,
        "work": (
            "Client confirms D1 drafts and supplies client-only commercial + access fields. "
            "P0 gaps must be closed here."
        ),
    },
    {
        "id": "D3",
        "name": "Completeness scoring",
        "owner": "client_success_manager",
        "fields": list(DISCOVERY_FIELDS),
        "load_weight": 10,
        "work": "Score 0–100 against DISCOVERY_FIELDS; list missing / low-confidence keys.",
    },
    {
        "id": "D4",
        "name": "CSM sign-off",
        "owner": "client_success_manager",
        "fields": list(DISCOVERY_FIELDS),
        "load_weight": 20,
        "work": "Side-by-side research vs client answers; Approve / Edit / Reject.",
    },
    {
        "id": "D5",
        "name": "Publish to CDP",
        "owner": "client_success_manager",
        "fields": list(DISCOVERY_FIELDS),
        "load_weight": 15,
        "work": "Write commercial_scope + marketing_context; set discovery_status=complete.",
    },
]

_EMPTY = (None, "", [], {}, ())
_PLACEHOLDER = re.compile(
    r"^(tbd|n/?a|na|none|unknown|todo|pending|to confirm|—|-|\.)$",
    re.IGNORECASE,
)

# Access checklist often uses these as real statuses (not empty)
_ACCESS_STATUS_OK = re.compile(
    r"^(pending|granted|standard access|available|have access|no access|denied|requested)$",
    re.IGNORECASE,
)


def is_filled(value: Any, *, access_field: bool = False) -> bool:
    if value in _EMPTY:
        return False
    if isinstance(value, dict):
        inner = value.get("value", value)
        if inner is value:
            return any(is_filled(v, access_field=access_field) for v in value.values())
        return is_filled(inner, access_field=access_field)
    if isinstance(value, (list, tuple, set)):
        return any(is_filled(v, access_field=access_field) for v in value)
    text = str(value).strip()
    if not text:
        return False
    if access_field and _ACCESS_STATUS_OK.match(text):
        return True
    return not _PLACEHOLDER.match(text)


def unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value:
        return value.get("value")
    return value


def _as_list(value: Any) -> list[str]:
    raw = unwrap(value)
    if raw in _EMPTY:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    parts = re.split(r"[\n;,|]+", text)
    return [p.strip() for p in parts if p.strip()]


def normalize_cdd(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten commercial_scope / questionnaire / nested packs into field keys."""
    src = dict(raw or {})
    out: dict[str, Any] = {}

    # Nested packs often used in CDP
    for nest in ("commercial_scope", "marketing_context", "client_intake", "cdd", "fields"):
        blob = src.get(nest)
        if isinstance(blob, dict):
            for k, v in blob.items():
                if k not in out and is_filled(v):
                    out[k] = unwrap(v)

    for k, v in src.items():
        if k in ("commercial_scope", "marketing_context", "client_intake", "cdd", "fields"):
            continue
        # Alias industry → inferred_industry
        key = "inferred_industry" if k == "industry" else k
        if key == "known_competitors" and "competitors" not in out:
            key = "competitors"
        if is_filled(v):
            out[key] = unwrap(v)

    return out


def parse_cdd_xlsx(path: str | Path | bytes, *, filename: str = "cdd.xlsx") -> dict[str, Any]:
    """Map APSA-style label|value rows to catalog keys — no LLM."""
    import openpyxl

    if isinstance(path, (bytes, bytearray)):
        wb = openpyxl.load_workbook(io.BytesIO(path), data_only=True)
    else:
        wb = openpyxl.load_workbook(str(path), data_only=True)

    out: dict[str, Any] = {}
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        for row in ws.iter_rows(values_only=True):
            vals = ["" if c is None else str(c).strip() for c in row]
            if not any(vals):
                continue
            label = ""
            value = ""
            if len(vals) >= 2 and vals[0]:
                label, value = vals[0], vals[1]
            elif len(vals) >= 3 and not vals[0] and vals[1]:
                label, value = vals[1], vals[2]
            elif len(vals) == 1 and ":" in vals[0]:
                label, value = vals[0].split(":", 1)
            else:
                # Access-style "GTM? (pending)"
                label = vals[0]
                value = ""
                if "(" in label and ")" in label:
                    start = label.rfind("(")
                    end = label.rfind(")")
                    if start >= 0 and end > start:
                        value = label[start + 1 : end].strip()
                        label = label[:start].strip()

            label_n = re.sub(r"\s+", " ", label.lower().strip())
            if not value and "(" in label_n and ")" in label_n:
                start = label_n.rfind("(")
                end = label_n.rfind(")")
                if start >= 0 and end > start:
                    value = label_n[start + 1 : end].strip()
                    label_n = label_n[:start].strip()
            key = CDD_LABEL_ALIASES.get(label_n) or CDD_LABEL_ALIASES.get(label_n.rstrip("?").strip())
            if not key:
                continue
            if key == "industry":
                key = "inferred_industry"
            if is_filled(value, access_field=key in ACCESS_FIELDS):
                out[key] = value.strip() if isinstance(value, str) else value
            elif key in ACCESS_FIELDS:
                out.setdefault(key, "To confirm")
    _ = filename
    return out


def load_cdd(source: str | Path | dict[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, dict):
        return normalize_cdd(source)
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"CDD file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in (".json", ".jsonl"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list) and data and isinstance(data[0], dict):
            # list of {field, value} or discovery_responses shape
            merged: dict[str, Any] = {}
            for row in data:
                k = row.get("field_key") or row.get("key") or row.get("field")
                v = row.get("field_value") or row.get("value")
                if k:
                    merged[str(k)] = unwrap(v)
            return normalize_cdd(merged)
        if not isinstance(data, dict):
            raise ValueError("JSON CDD must be an object or list of field rows")
        return normalize_cdd(data)
    if suffix in (".xlsx", ".xlsm"):
        return normalize_cdd(parse_cdd_xlsx(path))
    if suffix == ".csv":
        import csv

        merged = {}
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                label = re.sub(r"\s+", " ", row[0].lower().strip())
                key = CDD_LABEL_ALIASES.get(label) or row[0].strip()
                if key == "industry":
                    key = "inferred_industry"
                if is_filled(row[1]):
                    merged[key] = row[1].strip()
        return normalize_cdd(merged)
    raise ValueError(f"Unsupported CDD format: {suffix} (use .json / .xlsx / .csv)")


def _validate_competitors(value: Any) -> list[str]:
    issues: list[str] = []
    items = _as_list(value)
    if len(items) < 3:
        issues.append(f"competitors: need at least 3 (found {len(items)})")
    return issues


def _validate_objectives(value: Any) -> list[str]:
    issues: list[str] = []
    items = _as_list(value)
    if not items:
        issues.append("objectives: empty — pick from the CDD checklist")
        return issues
    known = {o.lower() for o in OBJECTIVE_OPTIONS}
    unknown = [i for i in items if i.lower() not in known and len(i) < 4]
    if unknown:
        issues.append(f"objectives: suspicious short entries {unknown}")
    return issues


def analyse_cdd(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Validate CDD completeness, priorities, and Discovery sub-phase load."""
    fields = normalize_cdd(raw)
    present = sorted(
        k for k, v in fields.items() if is_filled(v, access_field=k in ACCESS_FIELDS)
    )
    issues: list[str] = []

    if "competitors" in fields:
        issues.extend(_validate_competitors(fields.get("competitors")))
    if "objectives" in fields and is_filled(fields.get("objectives")):
        issues.extend(_validate_objectives(fields.get("objectives")))

    tiers: list[dict[str, Any]] = []
    missing_p0: list[str] = []
    for code, label, keys in TIER_ORDER:
        filled: list[str] = []
        missing: list[str] = []
        for key in keys:
            if is_filled(fields.get(key), access_field=key in ACCESS_FIELDS):
                filled.append(key)
            else:
                missing.append(key)
                if code == "P0":
                    missing_p0.append(key)
        total = len(keys) or 1
        tiers.append(
            {
                "tier": code,
                "label": label,
                "filled": filled,
                "missing": missing,
                "score_pct": round(100 * len(filled) / total, 1),
                "count": f"{len(filled)}/{total}",
            }
        )

    # Readiness-style score on DISCOVERY_FIELDS (same set as Phase 1 D3)
    scored = sum(
        1
        for k in DISCOVERY_FIELDS
        if is_filled(fields.get(k), access_field=k in ACCESS_FIELDS)
    )
    discovery_score = round(100 * scored / len(DISCOVERY_FIELDS), 1)

    # Requirements & priorities inferred from filled CDD (deterministic)
    priorities = _derive_priorities(fields)

    # Sub-phase load split
    subphases = _subphase_plan(fields, missing_p0)

    blocked_phases: dict[str, list[str]] = {}
    for key in missing_p0 + [
        m for t in tiers if t["tier"] in ("P1", "P3") for m in t["missing"]
    ]:
        for phase in FIELD_BLOCKS_PHASE.get(key, ()):
            blocked_phases.setdefault(phase, []).append(key)

    ready_for_d5 = not missing_p0 and discovery_score >= 70
    verdict = (
        "ready"
        if ready_for_d5 and not any(t["missing"] for t in tiers if t["tier"] == "P1")
        else "blocked"
        if missing_p0
        else "partial"
    )

    return {
        "verdict": verdict,
        "discovery_score": discovery_score,
        "discovery_fields_filled": f"{scored}/{len(DISCOVERY_FIELDS)}",
        "ready_for_d5_publish": ready_for_d5,
        "field_count_present": len(present),
        "present_keys": present,
        "tiers": tiers,
        "issues": issues,
        "priorities": priorities,
        "sub_phases": subphases,
        "blocked_pipeline_phases": blocked_phases,
        "sections": _section_scores(fields),
        "evidence": "APSA CDD V1 catalog — backend/app/services/discovery_fields.py",
        "note": (
            "Pure Python checklist (no LLM). P0 gaps block Search Demand / Strategy. "
            "P3 access gaps block Tracking / Publishing."
        ),
    }


def _section_scores(fields: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for sec in FIELD_SECTIONS:
        keys = [k for k, meta in FIELD_META.items() if meta.get("section") == sec["id"]]
        filled = [
            k for k in keys if is_filled(fields.get(k), access_field=k in ACCESS_FIELDS)
        ]
        total = len(keys) or 1
        rows.append(
            {
                "id": sec["id"],
                "title": sec["title"],
                "filled": len(filled),
                "total": len(keys),
                "score_pct": round(100 * len(filled) / total, 1),
                "missing": [k for k in keys if k not in filled],
            }
        )
    return rows


def _derive_priorities(fields: dict[str, Any]) -> dict[str, Any]:
    products = _as_list(fields.get("products_for_promotion")) or _as_list(fields.get("products"))
    keywords = _as_list(fields.get("business_keywords"))
    objectives = _as_list(fields.get("objectives"))
    geo = str(unwrap(fields.get("geographic_focus")) or "").strip()
    goal = str(unwrap(fields.get("business_goal")) or "").strip()

    ranked_objectives = []
    # Prefer lead/conversion objectives first for commercial SEO load
    lead_first = (
        "Generate more qualified leads",
        "Improve conversion rate",
        "Increase organic traffic",
        "Improve keyword rankings",
        "Outrank specific competitors",
        "Support a product or service launch",
        "Expand into new markets",
        "Reduce customer acquisition cost",
        "Strengthen brand authority",
        "Grow paid search / SEM leads",
    )
    for obj in lead_first:
        if any(obj.lower() == o.lower() for o in objectives):
            ranked_objectives.append(obj)
    for o in objectives:
        if o not in ranked_objectives:
            ranked_objectives.append(o)

    return {
        "promote_first": products[:5],
        "keyword_themes": keywords[:10],
        "geography": geo or None,
        "business_goal": goal or None,
        "objectives_ranked": ranked_objectives,
        "buyer_type": unwrap(fields.get("b2b_b2c")),
        "summary": _priority_summary(products, keywords, geo, ranked_objectives),
    }


def _priority_summary(
    products: list[str],
    keywords: list[str],
    geo: str,
    objectives: list[str],
) -> str:
    bits = []
    if products:
        bits.append("Promote: " + ", ".join(products[:3]))
    if keywords:
        bits.append("Themes: " + ", ".join(keywords[:4]))
    if geo:
        bits.append(f"Geo: {geo}")
    if objectives:
        bits.append("Objective #1: " + objectives[0])
    return " | ".join(bits) if bits else "Insufficient CDD to rank priorities — fill P0 first."


def _subphase_plan(fields: dict[str, Any], missing_p0: list[str]) -> list[dict[str, Any]]:
    """Distribute remaining CDD work across D1–D5 with load weights."""
    plan = []
    for sp in SUB_PHASES:
        todo = [
            k
            for k in sp["fields"]
            if not is_filled(fields.get(k), access_field=k in ACCESS_FIELDS)
        ]
        # D2 also owns confirming P0 even if listed under research
        if sp["id"] == "D2":
            for k in missing_p0:
                if k not in todo:
                    todo.append(k)
        if sp["id"] == "D1":
            # D1 only researchable empties
            todo = [k for k in todo if k in RESEARCH_FIELDS]
        open_count = len(todo)
        status = "done" if open_count == 0 and sp["id"] in ("D1", "D2") else (
            "blocked" if sp["id"] in ("D4", "D5") and missing_p0 else
            "ready" if open_count == 0 else "in_progress" if open_count else "pending"
        )
        if sp["id"] == "D3":
            status = "ready" if not missing_p0 else "blocked"
        if sp["id"] == "D5":
            status = "ready" if not missing_p0 and open_count == 0 else "blocked" if missing_p0 else "pending"

        # Load share among open work (normalize weights of incomplete phases)
        plan.append(
            {
                "id": sp["id"],
                "name": sp["name"],
                "owner": sp["owner"],
                "load_weight": sp["load_weight"],
                "status": status,
                "open_fields": todo[:40],
                "open_count": open_count,
                "work": sp["work"],
            }
        )

    open_weights = sum(p["load_weight"] for p in plan if p["open_count"] or p["status"] != "done")
    for p in plan:
        if open_weights and (p["open_count"] or p["status"] not in ("done",)):
            p["load_share_pct"] = round(100 * p["load_weight"] / open_weights, 1)
        else:
            p["load_share_pct"] = 0.0 if p["status"] == "done" else p["load_weight"]
    return plan


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"CDD checklist verdict: {report.get('verdict', '').upper()}",
        f"Discovery score: {report.get('discovery_score')} "
        f"({report.get('discovery_fields_filled')})  |  D5 publish ready: {report.get('ready_for_d5_publish')}",
        f"Fields present: {report.get('field_count_present')}",
        "",
        "== Priority tiers ==",
    ]
    for t in report.get("tiers") or []:
        lines.append(f"  {t['tier']} ({t['label']}): {t['count']} — {t['score_pct']}%")
        if t["missing"]:
            lines.append(f"    missing: {', '.join(t['missing'])}")
    if report.get("issues"):
        lines.append("")
        lines.append("== Validation issues ==")
        for i in report["issues"]:
            lines.append(f"  - {i}")
    pri = report.get("priorities") or {}
    lines.append("")
    lines.append("== Requirements & priorities ==")
    lines.append(f"  {pri.get('summary')}")
    if pri.get("objectives_ranked"):
        lines.append("  Objectives: " + " > ".join(pri["objectives_ranked"][:5]))
    lines.append("")
    lines.append("== Sub-phases (load distribution) ==")
    for sp in report.get("sub_phases") or []:
        lines.append(
            f"  {sp['id']} {sp['name']} [{sp['status']}] "
            f"load={sp['load_share_pct']}%  open={sp['open_count']}  owner={sp['owner']}"
        )
        if sp.get("open_fields"):
            lines.append(f"    todo: {', '.join(sp['open_fields'][:12])}")
    blocked = report.get("blocked_pipeline_phases") or {}
    if blocked:
        lines.append("")
        lines.append("== Pipeline phases blocked by CDD gaps ==")
        for phase, keys in sorted(blocked.items()):
            lines.append(f"  {phase}: {', '.join(sorted(set(keys)))}")
    lines.append("")
    lines.append(str(report.get("note") or ""))
    return "\n".join(lines)


def empty_template() -> dict[str, Any]:
    """Blank CDD skeleton for operators to fill."""
    keys = list(dict.fromkeys([*TIER_P0_CRITICAL, *TIER_P1_HIGH, *TIER_P2_COMMERCIAL, *TIER_P3_ACCESS]))
    return {k: None for k in keys}
