"""tracking_anomaly_flags() honesty fix (AUDIT-002).

Previously this defaulted to a hardcoded event-count series containing a
built-in 3-sigma spike whenever called with no argument — and it was always
called with no argument in production (agents/tracking.py) — so every
tracking-phase run reported a fabricated "suspicious traffic spike" finding
regardless of the client's real data. It should now only report flags when
given real data, or a deterministic demo spike in mock-provider mode."""

from __future__ import annotations

from app.config import get_settings
from app.ml.scoring import tracking_anomaly_flags


def test_no_data_and_not_mock_mode_returns_no_flags(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    assert tracking_anomaly_flags() == []


def test_no_data_in_mock_mode_returns_demo_spike(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", True)

    flags = tracking_anomaly_flags()
    assert "Suspicious traffic spike — possible bot inflation" in flags


def test_real_data_with_spike_is_flagged_regardless_of_mock_mode(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    flags = tracking_anomaly_flags([100, 105, 98, 102, 400, 99, 101])
    assert "Suspicious traffic spike — possible bot inflation" in flags


def test_real_uniform_data_returns_no_flags(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    flags = tracking_anomaly_flags([100, 102, 98, 101, 99, 100, 103])
    assert flags == []


def test_real_data_with_drop_is_flagged(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    flags = tracking_anomaly_flags([100, 102, 98, 101, 5, 99, 100])
    assert "Sudden drop in event volume — possible broken tag" in flags


def test_too_short_series_returns_no_flags(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)

    assert tracking_anomaly_flags([100]) == []
