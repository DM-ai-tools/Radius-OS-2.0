"""Sitemap XML audit joined onto hierarchy and the URL tree. No HTTP."""

from __future__ import annotations

from app.services.sitemap_audit import (
    URL_CAP,
    audit_documents,
    build_sitemap_hierarchy,
    parse_sitemap_xml,
    stamp_page_hierarchy,
    stamp_target_url_tree,
)

INDEX = """<?xml version="1.0"?>
<sitemapindex>
  <sitemap><loc>https://example.com/page-sitemap.xml</loc></sitemap>
</sitemapindex>
"""

CHILD = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://example.com/services/</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://example.com/tag/seo/</loc><lastmod>2024-06-01</lastmod></url>
</urlset>
"""

IDENTICAL = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://example.com/a</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://example.com/b</loc><lastmod>2024-01-01</lastmod></url>
</urlset>
"""

PAGE_XML = """<?xml version="1.0"?>
<urlset>
  <url><loc>https://example.com/feeds/catalog.xml</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>https://example.com/services/</loc><lastmod>2024-02-01</lastmod></url>
</urlset>
"""


def test_index_child_keeps_loc_lastmod_and_cap():
    audit = audit_documents(
        [
            {"url": "https://example.com/sitemap_index.xml", "status_code": 200, "text": INDEX},
            {"url": "https://example.com/page-sitemap.xml", "status_code": 200, "text": CHILD},
        ]
    )
    assert audit["summary"]["url_cap"] == URL_CAP == 50_000
    listed = {row["path"]: row for row in audit["urls"]}
    assert listed["/services"]["lastmod"] == "2024-01-01"
    assert listed["/services"]["sitemap_file"] == "page-sitemap.xml"
    assert listed["/tag/seo"]["excluded_from_inventory"] is True
    assert any(f["kind"] == "index" for f in audit["files"])


def test_identical_lastmod_is_an_issue():
    audit = audit_documents(
        [{"url": "https://example.com/page-sitemap.xml", "status_code": 200, "text": IDENTICAL}]
    )
    assert any(issue["code"] == "lastmod_identical" for issue in audit["issues"])


def test_xml_loc_inside_urlset_is_not_a_child_sitemap():
    parsed = parse_sitemap_xml(PAGE_XML, source_url="https://example.com/page-sitemap.xml")
    assert parsed["kind"] == "urlset"
    assert parsed["children"] == []
    assert any(row["loc"].endswith("catalog.xml") for row in parsed["urls"])


def test_over_cap_is_reported_without_http():
    body = "<urlset>" + "".join(
        f"<url><loc>https://example.com/p{i}</loc><lastmod>2024-01-0{1 if i % 2 else 2}</lastmod></url>"
        for i in range(3)
    ) + "</urlset>"
    audit = audit_documents(
        [{"url": "https://example.com/page-sitemap.xml", "status_code": 200, "text": body}],
        cap=3,
    )
    assert any(issue["code"] == "over_50000" for issue in audit["issues"])


def test_tree_and_hierarchy_verdicts():
    audit = audit_documents(
        [{"url": "https://example.com/page-sitemap.xml", "status_code": 200, "text": CHILD}]
    )
    tree = stamp_target_url_tree(
        [
            {"url": "/services/", "parent": "/", "depth": 1, "page_type": "service"},
            {"url": "/services/new/", "parent": "/services", "depth": 2, "page_type": "service"},
            {"url": "/services/old/", "parent": "/services", "depth": 2, "page_type": "service"},
        ],
        audit,
        existing_paths={"/services", "/services/old"},
    )
    by_url = {row["url"]: row["sitemap_verdict"] for row in tree}
    assert by_url["/services/"] == "in_sitemap"
    assert by_url["/services/new/"] == "planned_only"
    assert by_url["/services/old/"] == "sitemap_gap"

    hierarchy = stamp_page_hierarchy(
        [{"path": "/services", "title": "Services"}, {"path": "/services/old", "title": "Old"}],
        audit,
    )
    assert hierarchy[0]["in_xml_sitemap"] is True
    assert hierarchy[0]["sitemap_file"] == "page-sitemap.xml"
    assert hierarchy[1]["sitemap_issues"] == ["missing_from_sitemap"]

    packed = build_sitemap_hierarchy(
        tree,
        audit,
        hierarchy_paths={"/services", "/services/old"},
    )
    hub = next(node for node in packed["nodes"] if node["url"] == "/services/")
    assert "/services/old/" in hub["children_missing"]
    assert all(row["path"] != "/services" for row in packed["unassigned"])
