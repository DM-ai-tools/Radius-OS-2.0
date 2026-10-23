"""Phase 7 expansion: page sample, twelve-group report, quick wins, backlog, PDF/DOCX.

The fake site is served by httpx.MockTransport, so nothing touches the network.
"""

from __future__ import annotations

import io
import json

import httpx
import pytest

from app.integrations.providers import (
    parse_pagespeed_response,
    robots_full_disallow_agents,
)
from app.services import technical_seo_report as report
from app.services import technical_seo_sample as sample_mod
from app.services.technical_seo_checklist import build_audit_coverage
from app.services.technical_seo_sample import (
    parse_page,
    parse_structured_data,
    run_page_sample,
)

SITE = "https://acme.example"

HOME = f"""<!doctype html><html lang="en"><head>
<title>Acme Plumbing Melbourne</title>
<meta name="description" content="Emergency plumbers across Melbourne, 24/7.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="WordPress 6.4.2">
<link rel="canonical" href="{SITE}/">
<link rel="stylesheet" href="/wp-content/themes/acme/style.css">
<script src="/wp-includes/js/jquery.js?ver=6.4.2"></script>
<script src="https://www.googletagmanager.com/gtm.js" async></script>
<script type="application/ld+json">{{"@context":"https://schema.org","@graph":[
 {{"@type":"Organization","@id":"{SITE}/#org","name":"Acme","url":"{SITE}/"}},
 {{"@type":"WebSite","@id":"{SITE}/#site","name":"Acme","url":"{SITE}/","publisher":{{"@id":"{SITE}/#org"}},
  "potentialAction":{{"@type":"SearchAction","target":"{SITE}/?s={{q}}","query-input":"required name=q"}}}}]}}</script>
</head><body><header><nav><a href="/services/">Services</a><a href="/about/">About</a></nav></header>
<h1>Plumbers in Melbourne</h1><h2>Services</h2><h4>Skipped level</h4>
<p>{"Real words about plumbing work in Melbourne. " * 40}</p>
<img src="/wp-content/uploads/hero.jpg" alt="A plumber"><img src="http://acme.example/old.png">
</body></html>"""

SERVICES = f"""<!doctype html><html><head><title>Services</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"Service","name":"Blocked drains"}}</script>
<script type="application/ld+json">{{ not json </script>
</head><body><h1>Our services</h1><p>{"Short. " * 20}</p></body></html>"""


def fake_site(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    host = request.url.host
    path = request.url.path
    headers = {"content-type": "text/html; charset=utf-8", "strict-transport-security": "max-age=300"}
    if request.url.scheme == "http":
        return httpx.Response(301, headers={"location": url.replace("http://", "https://", 1)})
    if host == "www.acme.example":
        return httpx.Response(301, headers={"location": f"{SITE}{path}"})
    if path == "/":
        return httpx.Response(200, text=HOME, headers=headers)
    if path == "/services/":
        if request.url.query:
            return httpx.Response(200, text=SERVICES, headers=headers)  # parameter duplicate, no canonical
        return httpx.Response(200, text=SERVICES, headers=headers)
    if path == "/services":
        return httpx.Response(301, headers={"location": f"{SITE}/services/"})
    if path == "/SERVICES/":
        return httpx.Response(404, text="nope", headers=headers)
    if path == "/about/":
        return httpx.Response(301, headers={"location": f"{SITE}/about-us/"})
    if path == "/about-us/":
        return httpx.Response(301, headers={"location": f"{SITE}/company/"})
    if path == "/company/":
        return httpx.Response(200, text=SERVICES.replace("Services", "Company"), headers=headers)
    if path == "/wp-config.php.bak":
        return httpx.Response(200, text="define('DB_PASSWORD', 'x');", headers={"content-type": "text/plain"})
    if path == "/xmlrpc.php":
        return httpx.Response(405, text="XML-RPC server accepts POST requests only.")
    if path.endswith(".jpg"):
        return httpx.Response(200, headers={"content-length": "450000", "content-type": "image/jpeg"})
    return httpx.Response(404, text="not found", headers=headers)


def blocked_site(request: httpx.Request) -> httpx.Response:
    if request.url.scheme == "http":
        return httpx.Response(301, headers={"location": str(request.url).replace("http://", "https://", 1)})
    return httpx.Response(202, text="<html><script>challenge()</script></html>", headers={"content-type": "text/html"})


@pytest.fixture
def transport(monkeypatch):
    def use(handler):
        real = httpx.AsyncClient

        def factory(**kwargs):
            kwargs.pop("transport", None)
            return real(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(sample_mod.httpx, "AsyncClient", factory)
        monkeypatch.setattr(sample_mod, "assert_safe_url", lambda url: (True, url))

    return use


# ------------------------------------------------------------------ parsing


def test_parse_page_extracts_the_on_page_and_weight_fields():
    p = parse_page(HOME, f"{SITE}/")
    assert p["title"] == "Acme Plumbing Melbourne"
    assert p["canonical"] == f"{SITE}/"
    assert p["h1"] == ["Plumbers in Melbourne"]
    assert any("H4 follows H2" in i for i in p["heading_issues"])
    assert (p["image_count"], p["images_missing_alt"]) == (2, 1)
    assert p["mixed_content"] == ["http://acme.example/old.png"]
    assert p["third_party_hosts"] == ["www.googletagmanager.com"]
    assert len(p["render_blocking_js"]) == 1  # jquery in <head>, no async/defer
    assert p["nav_link_count"] == 2
    assert p["word_count"] > 250


def test_structured_data_types_requirements_and_nesting():
    p = parse_page(HOME, f"{SITE}/")["structured_data"]
    assert {"Organization", "WebSite", "SearchAction"} <= set(p["types"])
    assert p["missing_required"] == [] and p["malformed"] == []
    assert p["entities_linked"] is True and p["top_level_entities"] == 2
    s = parse_page(SERVICES, f"{SITE}/services/")["structured_data"]
    assert any("not valid JSON" in m for m in s["malformed"])
    bad = parse_structured_data(['{"@context":"https://schema.org","@type":"Product"}',
                                 '{"@context":"https://schema.org","@type":"Article","headline":"x"}'], [])
    assert any(m.startswith("Article: missing author, datePublished") for m in bad["missing_required"])
    website = (
        '{"@context":"https://schema.org","@type":"WebSite","name":"A","url":"u",'
        '"potentialAction":{"@type":"SearchAction"}}'
    )
    nested = parse_structured_data([website], [])
    assert nested["entities_linked"] is True  # nested, not two separate entities


def test_robots_only_a_wildcard_or_googlebot_block_counts():
    assert robots_full_disallow_agents("User-agent: HTTrack\nUser-agent: puf\nDisallow: /\n\nUser-agent: *\nDisallow: /wp-admin/\n") == ["httrack", "puf"]
    assert robots_full_disallow_agents("User-agent: *\nDisallow: /\n") == ["*"]
    assert robots_full_disallow_agents("User-agent: *\nDisallow: /\nAllow: /\n") == []


def test_pagespeed_parser_reads_all_six_metrics_and_weight_audits():
    data = {
        "loadingExperience": {"metrics": {
            "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2100, "category": "FAST"},
            "INTERACTION_TO_NEXT_PAINT": {"percentile": 250, "category": "AVERAGE"},
            "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 5, "category": "FAST"},
            "FIRST_CONTENTFUL_PAINT_MS": {"percentile": 1500, "category": "FAST"},
            "EXPERIMENTAL_TIME_TO_FIRST_BYTE": {"percentile": 900, "category": "AVERAGE"},
        }},
        "lighthouseResult": {"categories": {"performance": {"score": 0.71}}, "audits": {
            "total-blocking-time": {"numericValue": 420},
            "server-response-time": {"numericValue": 610},
            "render-blocking-resources": {"details": {"overallSavingsMs": 640, "items": [{"url": "https://a/x.css"}]}},
            "uses-optimized-images": {"details": {"overallSavingsBytes": 200000}},
            "third-party-summary": {"details": {"items": [{"entity": "GTM"}, {"entity": "FB"}]}},
        }},
    }
    parsed = parse_pagespeed_response(data)
    assert parsed["field"]["fcp_ms"] == 1500 and parsed["field"]["ttfb_ms"] == 900
    lab = parsed["lab"]
    assert lab["tbt_ms"] == 420 and lab["render_blocking_savings_ms"] == 640
    assert lab["image_savings_bytes"] == 200000 and lab["third_party_count"] == 2


# ------------------------------------------------------------------ page sample


@pytest.mark.asyncio
async def test_sample_on_a_reachable_wordpress_site(transport):
    transport(fake_site)
    sample = await run_page_sample(SITE, candidate_urls=[f"{SITE}/services/", f"{SITE}/about/"])
    assert sample["status"] == "available" and sample["headers_measured"] is True
    about = next(p for p in sample["pages"] if p["url"].endswith("/about/"))
    assert about["hops"] == 2  # /about/ -> /about-us/ -> /company/

    sec, header_rows = report.security_checks(sample)
    by = {r["field"]: r for r in sec}
    assert by["exposed WordPress files"]["status"] == "issue" and by["exposed WordPress files"]["severity"] == "Critical"
    assert by["XML-RPC"]["status"] == "issue"
    assert by["WordPress version exposure"]["status"] == "issue"
    assert by["HSTS"]["status"] == "issue"  # max-age=300 is too short
    assert by["mixed content"]["status"] == "issue"
    assert by["security headers"]["status"] == "issue"
    assert header_rows[0]["header"] == "Strict-Transport-Security"

    url_rows = {r["field"]: r for r in report.url_structure_checks(sample)}
    assert url_rows["HTTP/HTTPS"]["status"] == "pass"
    assert url_rows["www/non-www"]["status"] == "pass"
    assert url_rows["trailing slash"]["status"] == "pass"
    assert url_rows["case sensitivity"]["status"] == "pass"
    assert url_rows["parameters"]["status"] == "issue"
    assert url_rows["redirect chains"]["status"] == "issue"

    sd_rows, sd_table, eligible = report.structured_data_checks(sample)
    sd = {r["field"]: r for r in sd_rows}
    assert sd["validation"]["status"] == "issue"
    assert any("Site name" in e for e in eligible)
    assert any(t["types"] != "none" for t in sd_table)

    onpage, table = report.onpage_checks(sample)
    names = {(r["check"], r["status"]) for r in onpage}
    assert ("Heading hierarchy", "issue") in names
    assert ("Thin content", "issue") in names
    assert ("Meta description presence", "issue") in names
    assert table and table[0]["url"] == "/"

    perf, _metrics, weight = report.performance_checks(None, None, sample)
    perf_by = {r["field"]: r for r in perf}
    assert perf_by["image size"]["status"] == "issue"  # 450 KB hero
    assert perf_by["LCP"]["status"] == "not_measured"
    assert weight and weight[0]["third_party"] == 1


@pytest.mark.asyncio
async def test_a_site_that_blocks_this_server_reports_not_measured_not_passes(transport):
    transport(blocked_site)
    sample = await run_page_sample(SITE, candidate_urls=[f"{SITE}/services/"])
    assert sample["status"] == "NOT_AVAILABLE"
    assert all(p["blocked"] and not p["parsed"] for p in sample["pages"])
    checks = (
        report.onpage_checks(sample)[0]
        + report.url_structure_checks(sample)
        + report.canonical_checks(sample, gsc=None, sitemap_urls=[], duplicate_titles=[])[0]
        + report.js_checks(sample)[0]
        + report.structured_data_checks(sample)[0]
        + report.security_checks(sample)[0]
        + report.mobile_checks(sample, None, {"status": "available", "desktop_status": 202, "mobile_status": 202, "desktop_links": 0})
    )
    passes = {(c["group"], c["field"]) for c in checks if c["status"] == "pass"}
    # Only what the redirect chain proves may pass: http -> https and hop counts.
    assert passes <= {("URL structure", "HTTP/HTTPS"), ("URL structure", "redirect chains"), ("Security", "HTTPS")}
    assert not [c for c in checks if c["status"] == "issue"]


@pytest.mark.asyncio
async def test_firecrawl_copy_feeds_html_checks_but_never_the_raw_vs_rendered_check(transport):
    transport(blocked_site)

    async def firecrawl_copy(url):
        return HOME

    sample = await run_page_sample(SITE, candidate_urls=[], html_fallback=firecrawl_copy, rendered_fetch=firecrawl_copy)
    assert sample["fetched_via"] == "firecrawl"
    assert sample["pages"][0]["html_source"] == "firecrawl"
    canon = {r["field"]: r for r in report.canonical_checks(sample, gsc=None, sitemap_urls=[], duplicate_titles=[])[0]}
    assert canon["canonical existence"]["status"] == "pass"
    js = report.js_checks(sample)[0]
    assert all(r["status"] == "not_measured" for r in js)
    assert not sample["rendered"]


# ------------------------------------------------------------------ scoring and report


def _cwv(field=None, lab=None, passes=None):
    return {"field": field, "lab": lab, "passes_core_web_vitals": passes}


def test_performance_table_labels_field_and_lab_and_mobile_desktop():
    mobile = _cwv(field={"lcp_ms": 3000, "lcp_category": "AVERAGE", "inp_ms": 150, "inp_category": "FAST", "cls": 0.05,
                         "cls_category": "FAST", "fcp_ms": 1200, "fcp_category": "FAST", "ttfb_ms": 700, "ttfb_category": "FAST"},
                  lab={"tbt_ms": 650, "lcp_ms": 3500}, passes=False)
    desktop = _cwv(lab={"lcp_ms": 1200, "tbt_ms": 50, "cls": 0.01, "fcp_ms": 700, "ttfb_ms": 300})
    rows, metrics, _w = report.performance_checks(mobile, desktop, None)
    by = {r["field"]: r for r in rows}
    assert by["LCP"]["status"] == "issue" and "field" in by["LCP"]["detail"]
    assert by["TBT"]["status"] == "issue" and by["TBT"]["severity"] == "High"
    assert by["CWV"]["status"] == "issue"
    lcp = next(m for m in metrics if m["metric"].startswith("LCP"))
    assert "(field)" in lcp["mobile"] and "(lab)" in lcp["desktop"]
    inp = next(m for m in metrics if m["metric"].startswith("INP"))
    assert inp["desktop"] == "—"  # INP has no lab value


def test_group_scores_only_for_measured_groups():
    coverage = {"groups": [
        {"group": "Performance", "fields": [{"field": "HTML size", "status": "checked"}, {"field": "LCP", "status": "not_measured"}]},
        {"group": "Security", "fields": [{"field": "HTTPS", "status": "checked"}]},
        {"group": "Sitemap", "fields": [{"field": "200 status", "status": "not_measured"}]},
    ]}
    issues = [{"category": "Security", "severity": "High", "affected_url_count": 1}]
    scores = report.group_scores(issues, coverage)
    assert scores["Performance"]["score"] is None  # page weight alone is not a speed score
    assert scores["Sitemap"]["status"] == "NOT_AVAILABLE"
    assert scores["Security"]["score"] < 100
    rows, line = report.scorecard(scores)
    assert len(rows) == 12 and line.startswith("Scorecard") and "Performance —" in line


def test_backlog_is_grouped_by_category_and_capped_at_fifty():
    issues = [
        {"title": f"Issue {i}", "category": "Security" if i % 2 else "Crawlability", "severity": "High" if i < 5 else "Low",
         "recommended_action": "fix", "affected_url_count": 1}
        for i in range(70)
    ]
    rows = report.build_backlog(issues, [{"issue": "Missing canonical on homepage", "priority": "High", "fix": "add"}])
    assert len(rows) == 50
    order = [report.GROUP_INDEX[r["category"]] for r in rows]
    assert order == sorted(order)
    assert any(r["category"] == "Canonicalisation" for r in rows)  # inferred, not filed under Indexation


def test_quick_wins_are_high_priority_easy_fixes_only():
    backlog = [
        {"priority": "Critical", "issue": "Homepage is noindex", "fix": "Remove noindex", "category": "Indexation", "field": "noindex"},
        {"priority": "High", "issue": "LCP is poor", "fix": "Speed up", "category": "Performance", "field": "LCP"},
        {"priority": "Medium", "issue": "Missing title tag", "fix": "Add", "category": "On-page technical SEO", "field": "title"},
        {"priority": "High", "issue": "Missing title tag on 3 pages", "fix": "Add titles", "category": "On-page technical SEO", "field": "title"},
    ]
    wins = report.quick_wins(backlog)
    assert [w["issue"] for w in wins] == ["Homepage is noindex", "Missing title tag on 3 pages"]


def test_explicit_statuses_override_the_keyword_heuristic():
    explicit = {
        ("Security", "HSTS"): ("not_measured", "blocked"),
        ("Mobile", "viewport"): ("checked", "ok"),
        ("On-page technical SEO", "title"): ("checked", "6 sampled pages have titles"),
    }
    cov = build_audit_coverage(
        issues=[{"title": "Title tag missing or empty", "category": "On-page technical SEO"}],
        homepage_checked=True,
        explicit=explicit,
    )
    sec = next(g for g in cov["groups"] if g["group"] == "Security")
    assert next(f for f in sec["fields"] if f["field"] == "HSTS")["status"] == "not_measured"
    mob = next(g for g in cov["groups"] if g["group"] == "Mobile")
    assert next(f for f in mob["fields"] if f["field"] == "viewport")["status"] == "checked"
    # A full-crawl finding is not hidden by a clean sample of a few pages.
    onpage = next(g for g in cov["groups"] if g["group"] == "On-page technical SEO")
    assert next(f for f in onpage["fields"] if f["field"] == "title")["status"] == "issue"
    sitemap = next(g for g in cov["groups"] if g["group"] == "Sitemap")
    assert all(f["status"] == "not_measured" for f in sitemap["fields"])  # no sitemap read


# ------------------------------------------------------------------ PDF and DOCX


async def _summary(transport) -> dict:
    transport(fake_site)
    sample = await run_page_sample(SITE, candidate_urls=[f"{SITE}/services/", f"{SITE}/about/"])
    onpage, onpage_table = report.onpage_checks(sample)
    sd, sd_table, eligible = report.structured_data_checks(sample)
    sec, headers = report.security_checks(sample)
    perf, metrics, weight = report.performance_checks(None, None, sample)
    checks = onpage + report.url_structure_checks(sample) + sd + sec + perf
    issues = report.issues_from_checks(checks)
    coverage = build_audit_coverage(issues=issues, homepage_checked=True, explicit=report.field_statuses(checks))
    scores = report.group_scores(issues, coverage)
    _rows, line = report.scorecard(scores)
    backlog = report.build_backlog(issues, [])
    return {
        "card_type": "technical_seo_report", "client_name": "Acme", "primary_url": SITE, "score": 70,
        "severity": "warning", "executive_summary": "Narrative paragraph.", "scorecard_line": line,
        "coverage_mode": "reduced", "providers_used": ["page_sample"], "audit_coverage": coverage,
        "audit_groups": report.build_groups(issues=issues, coverage=coverage, scores=scores, checks=checks,
                                            coverage_notes={"Security": "Reduced coverage: sample only."}),
        "quick_wins": report.quick_wins(backlog), "priority_backlog": backlog,
        "on_page_audit": {"pages": onpage_table}, "performance_audit": {"metrics": metrics, "page_weight": weight},
        "structured_data_audit": {"pages": sd_table, "rich_results_eligible": eligible},
        "security_audit": {"headers": headers, "wordpress": sample["wordpress"]},
        "coverage_needs": {"Performance": "Set PAGESPEED_API_KEY."},
        "issues": issues, "audit_checks": checks,  # raw blobs must not spill into extra sections
    }


@pytest.mark.asyncio
async def test_pdf_and_docx_render_every_group_and_the_new_sections(transport):
    from docx import Document

    from app.services.report_docx import build_reports_docx
    from app.services.report_pdf import build_reports_pdf
    from app.services.report_pdf_normalize import normalize_report_payload

    summary = await _summary(transport)
    payload = normalize_report_payload("technical_seo_report", summary)
    assert set(payload["group"]) == {s for _t, s in __import__("app.services.report_pdf_document", fromlist=["x"]).TECH_GROUP_SLUGS}
    assert "issues" not in payload and "audit_checks" not in payload
    assert any(r["needs"] == "Set PAGESPEED_API_KEY." for r in payload["coverage_matrix"])
    assert payload["security_audit"]["wordpress_rows"][0]["result"].endswith("wp-config.php.bak")

    bundle = {"client": {"name": "Acme", "primary_url": SITE},
              "reports": [{"title": "Technical SEO", "card_type": "technical_seo_report", "payload": summary}]}
    pdf = build_reports_pdf(bundle, single=True)
    assert pdf[:4] == b"%PDF" and len(pdf) > 5000
    doc = Document(io.BytesIO(build_reports_docx(bundle, single=True)))
    # Body text in document order, including panels and tables.
    text = "\n".join(
        "".join(t.text or "" for t in el.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
        for el in doc.element.body
    )
    for heading in ("Quick Wins", "Crawlability", "Indexation", "URL Structure", "Canonicalisation", "Sitemap",
                    "Internal Linking", "On-Page Technical SEO", "JavaScript and Rendering", "Performance (Core Web Vitals)",
                    "Mobile and Usability", "Structured Data", "Security", "Priority Backlog by Category", "What We Checked"):
        assert heading in text, heading
    assert "At a glance:" in text and text.index("At a glance:") < text.index("Narrative paragraph.")
    cells = " ".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "wp-config.php.bak" in cells and "Strict-Transport-Security" in cells
    assert json.dumps(payload, default=str)  # serialisable for storage


@pytest.mark.asyncio
async def test_the_orchestrator_returns_the_twelve_group_report():
    from app.services import technical_seo

    result = await technical_seo.run_technical_seo_plan(client_name="Acme", primary_url="https://acme.example")
    assert [g["group"] for g in result["audit_groups"]] == list(report.ALL_CATEGORIES)
    assert set(result["category_scores"]) == set(report.ALL_CATEGORIES)
    assert result["scorecard_line"].startswith("Scorecard") and len(result["scorecard"]) == 12
    assert len(result["priority_backlog"]) <= 50
    assert all(q["priority"] in ("Critical", "High") for q in result["quick_wins"])
    assert result["coverage_mode"] == "crawl"
    assert result["performance_audit"]["metrics"][0]["mobile"].endswith("(field)")
    assert result["core_web_vitals_desktop"] is not None
    perf = next(g for g in result["audit_groups"] if g["group"] == "Performance")
    assert perf["score"] is not None  # mock CWV field data was measured


@pytest.mark.asyncio
async def test_broken_links_appear_once_without_cloudflare_email_links(monkeypatch):
    from app.services import technical_seo

    async def broken(*_a, **_kw):
        rows = [
            {"broken_url": "https://acme.example/?page_id=15596", "status": 404},
            {"broken_url": "https://acme.example/cdn-cgi/l/email-protection#abc", "status": 404},
        ]
        return {"broken_count": 2, "internal": rows, "external": [],
                "quick_fixes": [f"Fix {r['broken_url']}" for r in rows]}

    async def tech(*_a, **_kw):
        return {"score": 70, "sections": {}, "priority_fixes": []}

    monkeypatch.setattr(technical_seo, "check_broken_links", broken)
    monkeypatch.setattr(technical_seo, "run_technical_seo_audit", tech)
    result = await technical_seo.run_technical_seo_plan(client_name="Acme", primary_url="https://acme.example")
    rows = [b for b in result["priority_backlog"] if "broken" in str(b["issue"]).lower() or "Fix https" in str(b["issue"])]
    assert len(rows) == 1 and rows[0]["category"] == "Internal linking"
    assert rows[0]["issue"].startswith("1 broken link")
    issue = next(i for i in result["issues"] if i["rule_id"] == "BROKEN_LINKS")
    assert issue["affected_urls"] == ["https://acme.example/?page_id=15596"]


@pytest.mark.asyncio
async def test_crawl_audit_does_not_read_a_challenge_page_as_the_homepage(monkeypatch):
    from app.config import get_settings
    from app.integrations import providers

    async def fetch_url(url, **_kw):
        if url.endswith("robots.txt"):
            return {"status_code": 200, "text": "User-agent: *\nDisallow: /wp-admin/\n", "headers": {}}
        return {"status_code": 301, "headers": {"location": "https://acme.example/"}, "text": ""}

    async def page_html(url):
        return '<html><head><meta name="robots" content="noindex"></head><body>challenge</body></html>', {
            "status_code": 202, "final_url": url, "source": "fetch_url"}

    async def no_sitemap(*_a, **_kw):
        return {"status": "not_available", "issues": [], "urls": [], "summary": {}}

    monkeypatch.setattr(get_settings(), "use_mock_providers", False)
    monkeypatch.setattr("app.integrations.web_fetch.fetch_url", fetch_url)
    monkeypatch.setattr("app.integrations.web_fetch.fetch_page_html", page_html)
    monkeypatch.setattr("app.services.sitemap_audit.fetch_sitemap_audit", no_sitemap)
    out = await providers.run_technical_seo_audit("https://acme.example", display_name="Acme")
    issues = [f["issue"] for f in out["priority_fixes"]]
    assert "Homepage is noindex" not in issues
    assert "Homepage HTML not readable by crawler" in issues
    assert not any("robots.txt" in i for i in issues)  # Disallow: /wp-admin/ is not a site block


@pytest.mark.asyncio
async def test_technical_seo_never_calls_ahrefs_even_with_a_key(monkeypatch, transport):
    """Their decision (2026-10-18): no Ahrefs for technical SEO. With an Ahrefs key set,
    the pipeline still runs on the crawl audit, page sample and PageSpeed only."""
    from app.config import get_settings
    from app.integrations import ahrefs as ahrefs_api
    from app.services import technical_seo, technical_seo_companion
    from app.services.report_pdf import build_reports_pdf

    transport(fake_site)
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "ahrefs_api_key", "test-key")

    async def forbidden(*_a, **_kw):
        raise AssertionError("Ahrefs must not be called for technical SEO")

    for name in ("resolve_site_audit_project", "site_audit_issues", "site_audit_page_explorer", "site_audit_projects"):
        monkeypatch.setattr(ahrefs_api, name, forbidden)

    async def cwv(*_a, strategy="mobile", **_kw):
        return {"field": None, "lab": {"performance_score": 80, "lcp_ms": 2100, "tbt_ms": 120, "cls": 0.02,
                                       "fcp_ms": 1100, "ttfb_ms": 400}, "passes_core_web_vitals": None, "strategy": strategy}

    async def tech(*_a, **_kw):
        return {"score": 70, "sections": {}, "priority_fixes": [], "fetch_source": "fetch_url"}

    async def empty(*_a, **_kw):
        return {}

    async def snapshot(*_a, **_kw):
        return {"status": "available", "desktop_status": 200, "mobile_status": 200, "desktop_links": 20,
                "mobile_links": 20, "parity_issues": [], "parity_ok": True}

    async def hreflang(*_a, **_kw):
        return {"status": "available", "hreflang_found": False}

    async def no_sitemap(*_a, **_kw):
        return {"status": "not_available"}

    monkeypatch.setattr(technical_seo, "run_cwv_measurement", cwv)
    monkeypatch.setattr(technical_seo, "run_technical_seo_audit", tech)
    monkeypatch.setattr(technical_seo, "run_seo_audit", empty)
    monkeypatch.setattr(technical_seo, "check_broken_links", empty)
    monkeypatch.setattr(technical_seo_companion, "run_rendering_snapshot", snapshot)
    monkeypatch.setattr(technical_seo_companion, "run_hreflang_surface_check", hreflang)
    monkeypatch.setattr("app.services.sitemap_audit.fetch_sitemap_audit", no_sitemap)

    result = await technical_seo.run_technical_seo_plan(client_name="Acme", primary_url=SITE)
    assert "ahrefs_site_audit" not in result["providers_used"]
    assert result["ahrefs_site_audit"] is None and result["coverage_mode"] == "crawl"
    assert not any("ahrefs" in str(i.get("source")).lower() for i in result["issues"])
    assert not any("Ahrefs" in note for note in result["coverage_notes"].values())
    groups = {g["group"]: g for g in result["audit_groups"]}
    assert groups["Security"]["issue_count"] > 0 and groups["Performance"]["score"] is not None
    bundle = {"client": {"name": "Acme", "primary_url": SITE},
              "reports": [{"title": "Technical SEO", "card_type": "technical_seo_report", "payload": result}]}
    from app.services.report_pdf_normalize import normalize_report_payload

    payload = normalize_report_payload("technical_seo_report", result)
    assert "Ahrefs" not in json.dumps(payload, default=str)
    assert build_reports_pdf(bundle, single=True)[:4] == b"%PDF"
