"""Cost tracker vendor/model segregation."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.cost_tracker import (
    _aggregate_vendor_model_rows,
    model_label,
    vendor_label,
)


def test_vendor_label_claude_ahrefs_dataforseo():
    assert vendor_label("openrouter", "anthropic/claude-sonnet-5") == "Claude"
    assert vendor_label("anthropic", "claude-3-5-sonnet-20241022") == "Claude"
    assert vendor_label("ahrefs", None) == "Ahrefs"
    assert vendor_label("dataforseo", None) == "DataForSEO"
    assert vendor_label("openrouter", "google/gemini-2.5-pro") == "Gemini"


def test_aggregate_vendor_model_rows():
    rows = [
        SimpleNamespace(
            provider="openrouter",
            model="anthropic/claude-sonnet-5",
            operation="chat.completions",
            status="success",
            estimated_cost_usd=0.05,
            prompt_tokens=1000,
            completion_tokens=200,
        ),
        SimpleNamespace(
            provider="ahrefs",
            model=None,
            operation="/v3/site-explorer/backlinks-stats",
            status="success",
            estimated_cost_usd=0.01,
            prompt_tokens=None,
            completion_tokens=None,
        ),
        SimpleNamespace(
            provider="dataforseo",
            model=None,
            operation="keywords_data",
            status="success",
            estimated_cost_usd=0.0025,
            prompt_tokens=None,
            completion_tokens=None,
        ),
    ]
    vendors, models = _aggregate_vendor_model_rows(rows, success_only=False)
    assert {v["vendor"] for v in vendors} == {"Claude", "Ahrefs", "DataForSEO"}
    assert len(models) == 3
    assert model_label("ahrefs", None, "/v3/site-explorer/backlinks-stats") == "/v3/site-explorer/backlinks-stats"
