"""A wide or wordy metric strip must not crash the PDF export.

`GET /api/v1/clients/{id}/reports/export` returned 500 for a real client.
Cause: `metric_strip` lays every metric out in ONE table row of N equal
columns, and — unlike `data_table` — never truncates the values. Eight metrics
give ~51pt-wide columns, so a single wordy value wraps into dozens of lines and
the row measures taller than the 722pt frame. A one-row table cannot be split,
so reportlab raises LayoutError and the whole export dies.

Metric labels and values come straight from agent payloads, so neither their
count nor their length is bounded by anything upstream.
"""

from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate

from app.services.report_pdf import build_reports_pdf
from app.services.report_pdf_theme import metric_strip

# The usable frame in the real document: A4 height minus 0.75" margins.
FRAME_H = A4[1] - 1.5 * inch
FRAME_W = 6.5 * inch


def _height(flowable) -> float:
    return flowable.wrap(FRAME_W, FRAME_H)[1]


def test_an_8kb_csv_dumped_into_a_metric_cell_does_not_overflow():
    """The real killer: a page-inventory CSV arrived as a "metric" value and
    rendered 10,461pt tall in a 51pt-wide column."""
    metrics = [
        ("Truncated", "False"),
        ("Url Count", "89"),
        ("Primary Url", "https://clicktrends.com.au"),
        ("Shown Count", "89"),
        ("Generated At", "2026-09-22T09:05:31.112028+00:00"),
        ("Page Inventory Csv", "url,page_type,title,title_length " + ("/page,service,Title,42 " * 380)),
        ("Page Inventory Method", "Inventory assembled from public search-index evidence. " * 11),
    ]
    assert _height(metric_strip(metrics)) <= FRAME_H


def test_eight_paragraph_length_metrics_do_not_overflow():
    """The discovery strip: 8 columns, several values 100-500 chars."""
    metrics = [
        ("B2B B2C", "B2B"),
        ("Gtm Access", "Pending"),
        ("Blogs Notes", "Four supporting SEO blog articles on email-marketing topics. " * 3),
        ("Positioning", "AI + human expertise delivering behaviour-based email journeys. " * 4),
        ("Sales Cycle", "30 days: page rebuild and campaigns live; 60 days: first leads. " * 3),
        ("Access Level", "Admin requested; Standard minimum"),
        ("Business Goal", "SEO: increase organic sessions and achieve page-one rankings. " * 8),
        ("Business Model", "Services"),
    ]
    assert _height(metric_strip(metrics)) <= FRAME_H


def test_a_long_competitor_context_blob_does_not_overflow():
    metrics = [
        ("Audience", "Primary: Australian founders, owners, directors, marketing managers. " * 6),
        ("Industry", "Digital Marketing Agency"),
        ("Topic Seed", "email marketing agency"),
        ("Selection Mode", "new_clusters_vs_sitemap"),
        ("Selection Version", "phase5_sitemap_then_topics_v1"),
        ("Competitor Context", "COMPETITORS (from shared memory / competitor research). " * 17),
    ]
    assert _height(metric_strip(metrics)) <= FRAME_H


def test_many_metrics_stay_within_the_frame():
    metrics = [(f"Metric {i}", f"value {i}") for i in range(16)]
    assert _height(metric_strip(metrics)) <= FRAME_H


def test_a_single_very_long_value_is_truncated_rather_than_overflowing():
    metrics = [("Everything", "word " * 400)]
    assert _height(metric_strip(metrics)) <= FRAME_H


def test_short_metrics_are_left_alone():
    """The common case must keep its compact single-row KPI look."""
    strip = metric_strip([("Pages", "89"), ("Score", "70"), ("Status", "ok")])
    assert 0 < _height(strip) < 80


def test_export_survives_a_report_with_a_wide_wordy_metric_strip():
    """End-to-end: this bundle shape is what returned 500 in the running app."""
    bundle = {
        "client": {"name": "Click Trends", "primary_url": "https://clicktrends.com.au"},
        "exported_at": "2026-09-24T09:52:40+00:00",
        "phase_statuses": {"overall_readiness_score": 70},
        "reports": [
            {
                "card_type": "discovery",
                "title": "Discovery",
                "payload": {
                    "audience": ["B2B", "B2C"],
                    "services": [
                        "SEO", "PPC", "CRO", "content marketing", "link building",
                        "digital PR", "analytics", "email marketing",
                    ],
                    "regions": ["Melbourne", "Sydney", "Brisbane", "Perth", "Adelaide"],
                    "stage": "growth",
                    "goal": "increase qualified inbound leads from organic search",
                    "channels": ["organic", "paid", "referral", "direct", "email"],
                    "owner": "head of department",
                    "budget": "unspecified",
                },
            }
        ],
    }
    pdf = build_reports_pdf(bundle)
    assert pdf[:4] == b"%PDF"


def test_a_lone_oversized_flowable_cannot_wedge_the_whole_export():
    """Whatever one section does, the export must still produce a file."""
    doc_bundle = {
        "client": {"name": "Acme", "primary_url": "https://acme.com"},
        "exported_at": "2026-09-24T00:00:00+00:00",
        "phase_statuses": {},
        "reports": [
            {
                "card_type": "search_demand",
                "title": "Search demand",
                "payload": {f"metric_{i}": "long value " * 60 for i in range(10)},
            }
        ],
    }
    pdf = build_reports_pdf(doc_bundle)
    assert pdf[:4] == b"%PDF"


def _unused_doc() -> SimpleDocTemplate:  # pragma: no cover - documents the frame math
    return SimpleDocTemplate("x.pdf", pagesize=A4)
