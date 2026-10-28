"""API cost meter — estimation and context."""

from decimal import Decimal

from app.services.api_meter import (
    _MeterContext,
    _meter_ctx,
    anthropic_call_cost,
    apply_paid_cost,
    begin_paid_call,
    end_paid_call,
    estimate_llm_cost_usd,
    estimate_provider_cost_usd,
    release_phase_amount,
    reported_usage_cost,
    reserve_phase_amount,
    resolve_recorded_cost,
    service_budget_shares,
    service_research_budget,
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


def test_service_shares_sum_to_the_remaining_budget():
    assert service_budget_shares(7, 7) == [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert service_budget_shares(3, 1) == [0.3334, 0.3333, 0.3333]
    assert service_budget_shares(0, 7) == []
    assert service_budget_shares(4, 0) == [0.0, 0.0, 0.0, 0.0]


def test_one_service_cannot_spend_another_services_share():
    token = _meter_ctx.set(_MeterContext(phase_budget_usd=2.0))
    try:
        with service_research_budget(1.0):
            assert begin_paid_call(0.6) is True
            apply_paid_cost(0.6)
            end_paid_call(0.6)
            assert begin_paid_call(0.6) is False
        with service_research_budget(1.0):
            assert begin_paid_call(0.6) is True
            end_paid_call(0.6)
    finally:
        _meter_ctx.reset(token)


def test_every_service_share_is_reserved_before_the_first_call():
    token = _meter_ctx.set(_MeterContext(phase_budget_usd=1.2))
    try:
        assert reserve_phase_amount(0.6) == 0.6
        assert reserve_phase_amount(0.6) == 0.6
        assert begin_paid_call(0.5) is False
        release_phase_amount(0.6)
        release_phase_amount(0.6)
        assert begin_paid_call(0.5) is True
        end_paid_call(0.5)
    finally:
        _meter_ctx.reset(token)


def test_phase_cap_still_blocks_inside_a_larger_service_share():
    token = _meter_ctx.set(_MeterContext(phase_budget_usd=1.0))
    try:
        with service_research_budget(5.0):
            assert begin_paid_call(0.6) is True
            apply_paid_cost(0.6)
            end_paid_call(0.6)
            assert begin_paid_call(0.6) is False
    finally:
        _meter_ctx.reset(token)


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
