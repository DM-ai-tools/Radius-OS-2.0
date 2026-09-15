"""Lightweight ML / heuristic scorers used by agents."""

from __future__ import annotations

from typing import Any

import numpy as np


def spam_risk_score(links: list[dict[str, Any]]) -> float:
    """Heuristic stand-in for GradientBoostingClassifier on link features."""
    if not links:
        return 0.1
    spammy = sum(1 for l in links if l.get("spammy"))
    exact = sum(1 for l in links if "exact" in str(l.get("anchor", "")).lower())
    ratio = spammy / len(links)
    score = min(0.99, 0.15 + ratio * 0.7 + (0.1 if exact > 2 else 0))
    return round(score, 2)


def detect_traffic_anomalies(series: list[float] | None = None) -> dict[str, Any]:
    """Change-point style detection. Without a real series, report unavailable (no fake drop)."""
    from app.config import get_settings

    if series is None:
        if not get_settings().use_mock_providers:
            return {
                "severity": "info",
                "message": (
                    "No GA4/GSC time series available — anomaly detection skipped. "
                    "Connect analytics to enable live change-point detection."
                ),
                "drop_percent": 0,
                "sparkline": [],
                "source": "unavailable",
            }
        series = list(np.concatenate([
            np.random.normal(1000, 50, 45),
            np.random.normal(700, 40, 45),
        ]))
    arr = np.asarray(series, dtype=float)
    midpoint = len(arr) // 2
    before = arr[:midpoint].mean()
    after = arr[midpoint:].mean()
    drop_pct = round(100 * (before - after) / max(before, 1), 1)
    change_day = int(np.argmin(np.diff(arr))) if len(arr) > 2 else midpoint
    severity = "critical" if drop_pct > 25 else "warning" if drop_pct > 10 else "info"
    return {
        "change_point_index": change_day,
        "change_point_date": None,
        "drop_percent": drop_pct,
        "severity": severity,
        "sparkline": [round(float(x), 1) for x in arr[:: max(1, len(arr) // 24)]],
        "correlation_hint": None,
        "source": "series",
    }


def cluster_competitors(items: list[dict[str, str]]) -> dict[str, str]:
    """Assign positioning cluster labels (mock embedding clusters)."""
    labels = ["Value players", "Premium/DTC", "Category generalists", "Niche specialists"]
    mapping: dict[str, str] = {}
    for i, item in enumerate(items):
        name = item.get("name", "")
        lower = name.lower()
        if "value" in lower:
            mapping[item["url"]] = "Value players"
        elif "premium" in lower or "dtc" in lower:
            mapping[item["url"]] = "Premium/DTC"
        else:
            mapping[item["url"]] = labels[i % len(labels)]
    return mapping


def tracking_anomaly_flags(event_counts: list[int] | None = None) -> list[str]:
    """Advisory anomaly flags from a daily event-count series.

    Requires a real event-count series. Without one, this raises no flags
    (no fabricated anomalies) unless running in mock-provider mode, where a
    deterministic demo spike is used — mirroring detect_traffic_anomalies().

    Uses a median/MAD modified z-score (Iglewicz & Hoaglin) rather than
    mean/std: with the small samples this runs on (a week of daily counts),
    a single-point outlier included in a mean/std calculation inflates its
    own threshold enough to mask itself — a plain std-based check can never
    fire here regardless of how extreme the outlier is.
    """
    from app.config import get_settings

    if event_counts is None:
        if not get_settings().use_mock_providers:
            return []
        event_counts = [100, 105, 98, 102, 400, 99, 101]  # mock-mode demo spike
    if len(event_counts) < 2:
        return []
    arr = np.asarray(event_counts, dtype=float)
    median = np.median(arr)
    mad = np.median(np.abs(arr - median)) or 1.0
    modified_z = 0.6745 * (arr - median) / mad
    flags = []
    if (modified_z > 3.5).any():
        flags.append("Suspicious traffic spike — possible bot inflation")
    if (modified_z < -3.5).any():
        flags.append("Sudden drop in event volume — possible broken tag")
    return flags
