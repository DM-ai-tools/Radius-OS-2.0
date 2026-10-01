"""Phase 1 — AI gap fill for discovery fields the user left blank."""

import pytest

from app.services import discovery_gap_fill
from app.services.discovery_fields import ACCESS_FIELDS
from app.services.discovery_gap_fill import (
    AI_FILLABLE_FIELDS,
    MAX_AI_FILL_CONFIDENCE,
    NEVER_AI_FILL,
    build_gap_fill_prompt,
    fill_missing_discovery_fields,
    normalize_gap_fill_response,
    pending_gap_fill_fields,
)


def test_access_fields_are_never_ai_fillable():
    for key in ACCESS_FIELDS:
        assert key in NEVER_AI_FILL
        assert key not in AI_FILLABLE_FIELDS


def test_money_and_commitment_fields_are_never_ai_fillable():
    for key in (
        "average_ticket_size",
        "lifetime_value",
        "revenue_split",
        "seo_traffic_current",
        "seo_traffic_target",
        "sem_leads_current",
        "sem_leads_target",
        "other_marketing_spend",
        "sales_promises",
    ):
        assert key in NEVER_AI_FILL
        assert key not in AI_FILLABLE_FIELDS


def test_competitors_stay_with_phase_4():
    assert "competitors" in NEVER_AI_FILL
    assert "competitors" not in AI_FILLABLE_FIELDS


def test_research_and_judgment_fields_are_ai_fillable():
    for key in (
        "inferred_industry",
        "business_model",
        "business_keywords",
        "products",
        "positioning",
        "geographic_focus",
        "target_demographic",
        "b2b_b2c",
        "strengths",
        "weaknesses",
        "brand_guidelines",
        "business_goal",
        "objectives",
        "lead_modes",
        "strategy_approach",
    ):
        assert key in AI_FILLABLE_FIELDS


def test_normalize_keeps_only_requested_fields():
    out = normalize_gap_fill_response(
        {
            "business_model": {"value": "Professional services", "confidence": 0.4},
            "positioning": {"value": "Premium local provider", "confidence": 0.4},
        },
        missing=["business_model"],
    )
    assert set(out) == {"business_model"}


def test_normalize_rejects_never_fillable_fields_even_when_requested():
    out = normalize_gap_fill_response(
        {
            "analytics_access": {"value": "Granted", "confidence": 0.4},
            "average_ticket_size": {"value": "$4,500", "confidence": 0.4},
            "competitors": {"value": "Acme, Globex", "confidence": 0.4},
        },
        missing=["analytics_access", "average_ticket_size", "competitors"],
    )
    assert out == {}


def test_normalize_drops_empty_and_null_values():
    out = normalize_gap_fill_response(
        {
            "business_model": {"value": None},
            "positioning": {"value": "   "},
            "products": {"value": []},
            "strengths": {"value": "Long-standing local reputation"},
        },
        missing=["business_model", "positioning", "products", "strengths"],
    )
    assert set(out) == {"strengths"}


def test_normalize_clamps_confidence_below_verified_threshold():
    out = normalize_gap_fill_response(
        {"business_model": {"value": "Ecommerce", "confidence": 0.95}},
        missing=["business_model"],
    )
    assert out["business_model"]["confidence"] <= MAX_AI_FILL_CONFIDENCE
    assert MAX_AI_FILL_CONFIDENCE < 0.5


def test_normalize_accepts_plain_scalar_shape():
    out = normalize_gap_fill_response(
        {"business_model": "Trade services"},
        missing=["business_model"],
    )
    assert out["business_model"]["value"] == "Trade services"
    assert out["business_model"]["confidence"] <= MAX_AI_FILL_CONFIDENCE


def test_normalize_coerces_select_field_to_declared_options():
    out = normalize_gap_fill_response(
        {"b2b_b2c": {"value": "b2b"}},
        missing=["b2b_b2c"],
    )
    assert out["b2b_b2c"]["value"] == "B2B"


def test_normalize_drops_select_value_outside_declared_options():
    out = normalize_gap_fill_response(
        {"b2b_b2c": {"value": "wholesale marketplace"}},
        missing=["b2b_b2c"],
    )
    assert out == {}


def test_normalize_keeps_only_known_objectives():
    out = normalize_gap_fill_response(
        {
            "objectives": {
                "value": [
                    "Increase organic traffic",
                    "Become a unicorn",
                    "Improve conversion rate",
                ]
            }
        },
        missing=["objectives"],
    )
    assert out["objectives"]["value"] == [
        "Increase organic traffic",
        "Improve conversion rate",
    ]


def test_normalize_keeps_rationale_when_present():
    out = normalize_gap_fill_response(
        {
            "business_model": {
                "value": "Professional services",
                "confidence": 0.4,
                "rationale": "Site sells consulting engagements, no cart.",
            }
        },
        missing=["business_model"],
    )
    assert "consulting" in out["business_model"]["rationale"]


def test_normalize_survives_non_dict_payload():
    assert normalize_gap_fill_response(None, missing=["business_model"]) == {}
    assert normalize_gap_fill_response("nope", missing=["business_model"]) == {}


def test_pending_fields_skips_answered_and_never_fillable():
    pending = pending_gap_fill_fields(
        {
            "business_model": "Professional services",
            "positioning": "   ",
            "products": [],
            "analytics_access": None,
        }
    )
    assert "business_model" not in pending
    assert "positioning" in pending
    assert "products" in pending
    assert "analytics_access" not in pending


def test_prompt_carries_client_answers_and_asks_only_for_blanks():
    prompt = build_gap_fill_prompt(
        display_name="Northside Plumbing",
        primary_url="https://northside.example",
        industry="Plumbing",
        known={"business_model": "Trade services"},
        missing=["positioning"],
        research={"public_reviews_summary": "Strong local reviews"},
    )
    assert "Northside Plumbing" in prompt
    assert "Trade services" in prompt
    assert "positioning" in prompt
    # Fields the client already answered must not be re-requested.
    assert "business_model" not in prompt.split("FIELDS TO DRAFT")[-1]


async def test_fill_returns_empty_without_calling_model_when_nothing_missing(monkeypatch):
    calls: list[str] = []

    async def _never(*args, **kwargs):
        calls.append("called")
        return {}

    monkeypatch.setattr(discovery_gap_fill, "synthesize_json", _never)
    out = await fill_missing_discovery_fields(
        display_name="Acme",
        primary_url="https://acme.example",
        industry=None,
        known={},
        missing=[],
        research={},
    )
    assert out == {}
    assert calls == []


async def test_fill_normalizes_model_payload(monkeypatch):
    async def _fake(system, user, **kwargs):
        return {
            "business_model": {"value": "Ecommerce", "confidence": 0.9},
            "analytics_access": {"value": "Granted", "confidence": 0.9},
        }

    monkeypatch.setattr(discovery_gap_fill, "synthesize_json", _fake)
    out = await fill_missing_discovery_fields(
        display_name="Acme",
        primary_url="https://acme.example",
        industry=None,
        known={},
        missing=["business_model", "analytics_access"],
        research={},
    )
    assert out["business_model"]["value"] == "Ecommerce"
    assert out["business_model"]["confidence"] <= MAX_AI_FILL_CONFIDENCE
    assert "analytics_access" not in out


async def test_fill_returns_empty_when_provider_unavailable(monkeypatch):
    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(discovery_gap_fill, "synthesize_json", _none)
    out = await fill_missing_discovery_fields(
        display_name="Acme",
        primary_url="https://acme.example",
        industry=None,
        known={},
        missing=["business_model"],
        research={},
    )
    assert out == {}


async def test_fill_returns_empty_when_provider_raises(monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(discovery_gap_fill, "synthesize_json", _boom)
    out = await fill_missing_discovery_fields(
        display_name="Acme",
        primary_url="https://acme.example",
        industry=None,
        known={},
        missing=["business_model"],
        research={},
    )
    assert out == {}


async def test_fill_in_mock_provider_mode_returns_empty():
    """Test env runs with USE_MOCK_LLM — synthesize_json yields None, not a guess."""
    out = await fill_missing_discovery_fields(
        display_name="Acme",
        primary_url="https://acme.example",
        industry=None,
        known={},
        missing=["business_model"],
        research={},
    )
    assert out == {}


@pytest.mark.parametrize("bad", [[], ["analytics_access"], ["competitors"]])
async def test_fill_skips_model_when_no_eligible_blanks(monkeypatch, bad):
    calls: list[str] = []

    async def _never(*args, **kwargs):
        calls.append("called")
        return {}

    monkeypatch.setattr(discovery_gap_fill, "synthesize_json", _never)
    out = await fill_missing_discovery_fields(
        display_name="Acme",
        primary_url="https://acme.example",
        industry=None,
        known={},
        missing=bad,
        research={},
    )
    assert out == {}
    assert calls == []
