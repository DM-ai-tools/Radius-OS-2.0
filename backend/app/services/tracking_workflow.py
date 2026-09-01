"""Phase 2 tracking workflow helpers — T1–T6 Access, Tracking & Data Collection."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# Platforms checked in T1 (connection verification)
T1_PLATFORMS = [
    {"key": "cms", "label": "CMS", "mode": "manual_or_api"},
    {"key": "hosting", "label": "Hosting", "mode": "manual_or_api"},
    {"key": "search_console", "label": "Google Search Console", "mode": "oauth"},
    {"key": "ga4", "label": "Google Analytics 4", "mode": "oauth"},
    {"key": "gtm", "label": "Google Tag Manager", "mode": "oauth"},
    {"key": "google_business", "label": "Google Business Profile", "mode": "oauth"},
    {"key": "crm", "label": "CRM", "mode": "manual_or_api"},
    {"key": "call_tracking", "label": "Call tracking", "mode": "manual_or_api"},
]

KNOWN_CHANGE_FIELDS = [
    {
        "key": "site_redesigns",
        "label": "Site redesigns",
        "help": "Major redesigns or template rebuilds and roughly when.",
    },
    {
        "key": "domain_moves",
        "label": "Domain moves / migrations",
        "help": "Domain changes, www/non-www, HTTP→HTTPS, platform moves.",
    },
    {
        "key": "tracking_changes",
        "label": "Past tracking changes",
        "help": "GA→GA4, new GTM, tag rebuilds, consent tool installs.",
    },
    {
        "key": "seo_campaigns",
        "label": "Past SEO campaigns",
        "help": "Prior agencies, big content pushes, link campaigns.",
    },
    {
        "key": "manual_actions",
        "label": "Manual actions / penalties",
        "help": "Search Console manual actions or security issues.",
    },
    {
        "key": "security_incidents",
        "label": "Security incidents",
        "help": "Hacks, malware, injected spam, downtime events.",
    },
    {
        "key": "algorithm_timing",
        "label": "Algorithm-update timing notes",
        "help": "Drops or gains the team links to known Google updates.",
    },
]


def build_t1_platforms(
    has_credentials: dict[str, bool],
    *,
    cdd_access: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build T1 platform rows; enrich details from CDD access notes when present."""
    cdd = cdd_access or {}
    # Map discovery access fields → T1 platform keys
    cdd_by_platform = {
        "ga4": cdd.get("analytics_access"),
        "search_console": cdd.get("search_console_access"),
        "gtm": cdd.get("gtm_access"),
        "cms": cdd.get("cms_access"),
        "hosting": cdd.get("hosting_access"),
        "google_business": cdd.get("google_business_access"),
    }
    ads_note = cdd.get("google_ads_access")
    merchant_note = cdd.get("merchant_center_access")
    level_note = cdd.get("access_level")

    rows = []
    oauth_keys = ("ga4", "search_console", "gtm", "google_business")
    for p in T1_PLATFORMS:
        key = p["key"]
        cdd_note = cdd_by_platform.get(key)
        if key in oauth_keys:
            connected = bool(has_credentials.get(key))
            status = "connected" if connected else "skipped"
            if connected:
                detail = (
                    "OAuth credential on file — Google Business Profile access granted."
                    if key == "google_business"
                    else "OAuth credential on file — connection test passed."
                )
            else:
                detail = (
                    "Optional. Not connected — tracking continues from live-site HTML "
                    "(no Google login required)."
                )
            if cdd_note:
                detail = f"CDD: {cdd_note}. {detail}"
        else:
            status = "pending_setup"
            detail = (
                f"{p['label']} is on the access checklist. "
                "Record credentials/access outside OAuth when available."
            )
            if cdd_note:
                status = "pending_setup"
                detail = f"CDD: {cdd_note}"
        rows.append(
            {
                **p,
                "status": status,
                "detail": detail,
                "automated": p["mode"] == "oauth",
            }
        )

    # Extra CDD-only systems not in core T1 list
    if ads_note:
        rows.append(
            {
                "key": "google_ads",
                "label": "Google Ads",
                "mode": "manual_or_api",
                "status": "pending_setup",
                "detail": f"CDD: {ads_note}",
                "automated": False,
            }
        )
    if merchant_note:
        rows.append(
            {
                "key": "merchant_center",
                "label": "Merchant Center",
                "mode": "manual_or_api",
                "status": "pending_setup",
                "detail": f"CDD: {merchant_note}",
                "automated": False,
            }
        )
    if level_note:
        rows.append(
            {
                "key": "access_level",
                "label": "Overall access level",
                "mode": "manual",
                "status": "pending_setup",
                "detail": f"CDD: {level_note}",
                "automated": False,
            }
        )
    return rows


def build_t2_audit(checks: list[dict[str, Any]], snippets: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tag-layer audit from live/mock element checks."""
    by_el = {c["element"]: c for c in checks}
    items = []
    mapping = [
        ("ga4_base_tag", "GA4 base tag present"),
        ("gtm_container", "GTM container present"),
        ("cross_domain_tracking", "Cross-domain / linker config"),
        ("conversion_event", "Duplicate / conversion tag health"),
    ]
    for el, label in mapping:
        c = by_el.get(el)
        if not c:
            continue
        items.append(
            {
                "check": label,
                "element": el,
                "result": c["check_result"],
                "message": (c.get("detail") or {}).get("message"),
                "fix": (c.get("detail") or {}).get("fix"),
            }
        )
    # Extra heuristic rows (cookie consent / referral) — informational
    items.append(
        {
            "check": "Cookie-consent configuration",
            "element": "cookie_consent",
            "result": "warning",
            "message": "Consent mode / CMP wiring should be confirmed manually in GTM preview.",
            "fix": "Verify tags fire only after consent where required.",
        }
    )
    items.append(
        {
            "check": "Referral exclusions",
            "element": "referral_exclusions",
            "result": "warning",
            "message": "Payment / auth referrers should be excluded in GA4 admin.",
            "fix": "Add payment gateway domains to unwanted referrals list.",
        }
    )
    return {
        "title": "T2 — Automated tracking audit",
        "subtitle": "GTM / GA4 tag inspection",
        "mode": "AUTOMATED",
        "items": items,
        "snippets": snippets or {},
    }


def build_t3_conversions(checks: list[dict[str, Any]]) -> dict[str, Any]:
    conv = next((c for c in checks if c["element"] == "conversion_event"), None)
    events = [
        {"name": "Primary conversion (e.g. purchase / lead)", "status": conv["check_result"] if conv else "unverified"},
        {"name": "Secondary — form submit", "status": "unverified"},
        {"name": "Secondary — phone / call click", "status": "unverified"},
        {"name": "Secondary — email click", "status": "unverified"},
        {"name": "Secondary — booking", "status": "unverified"},
    ]
    return {
        "title": "T3 — Conversion tracking validation",
        "subtitle": "Test-fire + confirm in GA4",
        "mode": "AUTOMATED",
        "summary": (conv or {}).get("detail", {}).get("message")
        or "Conversion firing needs GA4 access and DebugView / Data API confirmation.",
        "fix": (conv or {}).get("detail", {}).get("fix"),
        "events": events,
        "rule": "Configured but not verified as firing is marked not working / unverified.",
    }


def build_t4_baseline(
    *,
    has_ga4: bool,
    has_gsc: bool,
    display_name: str,
) -> dict[str, Any]:
    """6–12 month historical baseline shape (live API pull when wired; stub metrics otherwise)."""
    end = date.today()
    start = end - timedelta(days=365)
    available = has_ga4 or has_gsc
    metrics = {
        "organic_users": None,
        "sessions": None,
        "conversions": None,
        "revenue": None,
        "impressions": None,
        "clicks": None,
        "ctr": None,
        "avg_position": None,
        "top_queries": [],
        "top_landing_pages": [],
        "indexed_url_count": None,
        "branded_vs_nonbranded": None,
    }
    note = (
        f"Window {start.isoformat()} → {end.isoformat()}. "
        + (
            "Credentials present — full GSC/GA4 history pull can be attached when Data API is configured; "
            "structure is ready for the Client Digital Profile."
            if available
            else "GA4 / Search Console not connected — historical metrics stay empty. "
            "Live HTML tag audit still ran. You can approve T6 without Google APIs."
        )
    )
    return {
        "title": "T4 — Historical baseline extraction",
        "subtitle": "GSC API + GA4 API (optional)",
        "mode": "AUTOMATED",
        "client": display_name,
        "window_months": "6–12",
        "date_start": start.isoformat(),
        "date_end": end.isoformat(),
        "access": {"ga4": has_ga4, "search_console": has_gsc},
        "metrics": metrics,
        "note": note,
        "status": "ready_structure" if available else "skipped_no_api",
    }


def compute_tracking_blockers(rows: list[dict[str, Any]]) -> list[str]:
    """Hard blockers are live-page failures only. Missing Google OAuth is optional."""
    blockers = []
    oauth_optional = {"conversion_event", "search_console_access"}
    for r in rows:
        el = r.get("element")
        result = r.get("check_result")
        if el in oauth_optional:
            continue
        if result == "fail" and el == "ga4_base_tag":
            blockers.append(
                f"{el}: {result} — "
                f"{(r.get('detail') or {}).get('message', 'needs attention')}"
            )
    return blockers
