"""AUDIT-007: Celery task stubs must not claim work was dispatched when it
wasn't. site_crawl already reported this honestly; backlink_pull,
competitor_scan, and anomaly_detection returned a generic {"status": "ok"}
that reads as "the background job ran," when in fact nothing is dispatched —
every agent still executes inline. These pin the honest status string."""

from __future__ import annotations

from app.tasks import jobs


def test_site_crawl_reports_not_dispatched():
    result = jobs.site_crawl("client-1", "session-1", "https://acme.example")
    assert result["status"] == "not_dispatched_execution_is_inline"


def test_backlink_pull_reports_not_dispatched():
    result = jobs.backlink_pull("client-1", "acme.example")
    assert result["status"] == "not_dispatched_execution_is_inline"


def test_competitor_scan_reports_not_dispatched():
    result = jobs.competitor_scan("client-1", "session-1")
    assert result["status"] == "not_dispatched_execution_is_inline"


def test_anomaly_detection_reports_not_dispatched():
    result = jobs.anomaly_detection("client-1")
    assert result["status"] == "not_dispatched_execution_is_inline"


def test_recheck_unverified_tracking_reports_not_implemented():
    result = jobs.recheck_unverified_tracking()
    assert result["status"] == "not_implemented"
