"""API cost meter — estimation and context."""

from decimal import Decimal

from app.services.api_meter import estimate_llm_cost_usd, estimate_provider_cost_usd


def test_estimate_llm_cost_usd():
    cost = estimate_llm_cost_usd(
        "google/gemini-2.5-pro",
        prompt_tokens=1000,
        completion_tokens=500,
    )
    assert cost is not None
    assert cost > Decimal(0)


def test_estimate_provider_flat_cost():
    assert estimate_provider_cost_usd("dataforseo") == Decimal("0.0025")
    assert estimate_provider_cost_usd("ahrefs") == Decimal("0.01")
