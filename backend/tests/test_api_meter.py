"""API cost meter — estimation and context."""

from decimal import Decimal

from app.services.api_meter import (
    anthropic_call_cost,
    estimate_llm_cost_usd,
    estimate_provider_cost_usd,
    reported_usage_cost,
    resolve_recorded_cost,
)


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


def test_openrouter_reported_cost_is_used_as_the_charge():
    assert reported_usage_cost({"cost": 0.042}) == 0.042
    assert (
        resolve_recorded_cost(
            provider="openrouter",
            status="success",
            estimated_cost_usd=0.042,
            model="google/gemini-2.5-pro",
            prompt_tokens=10,
            completion_tokens=10,
        )
        == Decimal("0.042")
    )


def test_ahrefs_units_are_not_priced_as_a_flat_dollar():
    assert (
        resolve_recorded_cost(
            provider="ahrefs",
            status="success",
            estimated_cost_usd=None,
            model=None,
            prompt_tokens=4300,
            completion_tokens=None,
            apply_flat_estimate=False,
        )
        is None
    )


def test_anthropic_cache_tokens_add_to_the_call_cost():
    plain = anthropic_call_cost("claude-sonnet-5", input_tokens=0, output_tokens=0)
    cached = anthropic_call_cost(
        "claude-sonnet-5",
        input_tokens=0,
        output_tokens=0,
        cache_write_tokens=1_000_000,
        cache_read_tokens=1_000_000,
    )
    assert plain == Decimal("0")
    assert cached == Decimal("4.05")
