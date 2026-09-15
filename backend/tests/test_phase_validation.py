"""Phase validation agent — company-aware, phase-specific QC."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services.phase_validation.context import extract_phase_output
from app.services.phase_validation.criteria import (
    PHASE_CRITERIA,
    get_criteria,
    list_applicable_parameters,
)
from app.services.phase_validation.deterministic import run_deterministic_checks
from app.services.phase_validation.revision import (
    escalate_after_max_attempts,
    should_retry,
)
from app.services.phase_validation.schema import (
    CheckResult,
    CheckSeverity,
    CheckStatus,
    ValidationDecision,
    ValidationResult,
    decide_from_checks,
)
from app.services.phase_validation.service import validate_phase_output


def _client(**kwargs):
    base = dict(
        display_name="Acme Solar",
        legal_name="Acme Solar Pty Ltd",
        primary_url="https://acmesolar.example",
        industry="residential solar installation",
        tier="B",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _profile(**kwargs):
    base = dict(
        commercial_scope={
            "products": ["rooftop solar panels", "battery storage", "EV charger install"],
            "business_model": "B2C installation services",
            "geographic_focus": "Sydney, NSW",
            "positioning": "Premium residential solar",
        },
        marketing_context={
            "target_audience": "homeowners",
            "brand_guidelines": "Plain language, no competitor brand names",
            "compliance_constraints": "No guaranteed savings claims",
        },
        tracking_baseline={},
        website_situation_summary={},
        competitive_landscape_summary={},
        search_demand_summary={},
        seo_strategy_summary={},
        site_architecture_summary={},
        technical_seo_summary={},
        content_audit_summary={},
        content_planning_summary={},
        content_production_summary={},
        on_page_seo_summary={},
        publishing_summary={},
        discovery_status="complete",
        search_demand_status="complete",
        seo_strategy_status="complete",
        site_architecture_status="complete",
        content_planning_status="complete",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _card(agent_key: str, **payload):
    return [
        {
            "type": "structured_card",
            "payload": {
                "card_type": f"{agent_key}_report",
                "title": "Test",
                "agent_key": agent_key,
                "actions": ["approve"],
                **payload,
            },
        }
    ]


_SOLAR_CLUSTER = {
    "clusters": [
        {
            "name": "Rooftop solar",
            "primary_keyword": "rooftop solar sydney",
            "keywords": [{"keyword": "rooftop solar sydney"}],
            "intent": "commercial",
            "funnel": "MOFU",
            "topic_disposition": "new_topic",
        }
    ]
}

_SOLAR_CLASSIFICATION = {
    "existing_topic_count": 0,
    "existing_review_count": 0,
    "new_topic_count": 1,
    "pages_used": 1,
}


@pytest.mark.asyncio
async def test_valid_search_demand_passes_deterministic_and_semantic():
    client = _client()
    profile = _profile()
    events = _card(
        "search_demand",
        topic_plan={"topic_ideas": [{"title": "Rooftop solar for Sydney homes", "keyword": "rooftop solar sydney"}]},
        topic_selection_context={"topic_kw_pool": [{"keyword": "rooftop solar sydney"}]},
        cluster_report=_SOLAR_CLUSTER,
        sitemap_classification=_SOLAR_CLASSIFICATION,
        topics=[{"title": "Rooftop solar for Sydney homes"}],
        seed_keywords=["rooftop solar", "battery storage sydney"],
        best_opportunities=[{"keyword": "rooftop solar sydney", "volume": 1200}],
        providers_used=["ahrefs"],
    )

    async def semantic_ok(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "company_ok",
                    "parameter": "company_alignment",
                    "status": "passed",
                    "severity": "info",
                    "message": "Topics match Acme Solar.",
                },
                {
                    "check_id": "domain_ok",
                    "parameter": "domain_alignment",
                    "status": "passed",
                    "severity": "info",
                    "message": "Solar domain.",
                },
                {
                    "check_id": "services_ok",
                    "parameter": "service_product_alignment",
                    "status": "passed",
                    "severity": "info",
                    "message": "Services match offerings.",
                },
            ],
            "summary": "Aligned.",
            "proposed_decision": "pass",
            "revision_guidance": None,
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="search_demand",
        events=events,
        semantic_caller=semantic_ok,
    )
    assert result.decision == ValidationDecision.PASS.value
    assert result.passed
    assert "service_product_alignment" in result.applicable_parameters


@pytest.mark.asyncio
async def test_unrelated_domain_fails_semantic():
    client = _client()
    profile = _profile()
    events = _card(
        "search_demand",
        topic_plan=[{"title": "Best crypto trading bots 2026"}],
        cluster_report=_SOLAR_CLUSTER,
        sitemap_classification=_SOLAR_CLASSIFICATION,
        topics=[{"title": "Best crypto trading bots 2026"}],
        seed_keywords=["crypto bots"],
    )

    async def semantic_wrong_domain(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "domain_mismatch",
                    "parameter": "domain_alignment",
                    "status": "failed",
                    "severity": "critical",
                    "message": "Output is about cryptocurrency, not solar installation.",
                    "evidence": "crypto trading bots",
                    "recommended_correction": "Regenerate topics from solar CDD seeds.",
                }
            ],
            "summary": "Wrong domain.",
            "proposed_decision": "reject",
            "revision_guidance": "Use solar products only.",
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="search_demand",
        events=events,
        semantic_caller=semantic_wrong_domain,
    )
    assert result.decision == ValidationDecision.REJECT.value
    assert result.blocks_pipeline
    assert any(c.parameter == "domain_alignment" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_wrong_industry_fails():
    client = _client(industry="residential solar installation")
    profile = _profile()
    events = _card(
        "competitor_market_agent",
        competitors=[
            {"name": "BigBank Finance", "domain": "bigbank.example", "tier": 2},
            {"name": "PayLater Co", "domain": "paylater.example", "tier": 3},
        ],
        scorecards=[{"name": "BigBank Finance"}],
    )

    async def semantic_wrong_industry(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "industry_mismatch",
                    "parameter": "industry_alignment",
                    "status": "failed",
                    "severity": "major",
                    "message": "Peers are fintech lenders, not solar installers.",
                    "evidence": "BigBank Finance",
                    "recommended_correction": "Select solar installer peers.",
                }
            ],
            "summary": "Wrong industry peers.",
            "proposed_decision": "needs_revision",
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="competitor_market_agent",
        events=events,
        semantic_caller=semantic_wrong_industry,
    )
    assert result.decision == ValidationDecision.NEEDS_REVISION.value
    assert any(c.parameter == "industry_alignment" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_recommends_services_company_does_not_provide():
    client = _client()
    profile = _profile()
    events = _card(
        "content_strategy",
        priority_queue=[
            {
                "title": "Enterprise Kubernetes consulting packages",
                "keyword": "kubernetes consulting",
                "from_phase5_topic": False,
            }
        ],
    )
    profile.search_demand_summary = {
        "topic_plan": [{"title": "Rooftop solar for Sydney homes"}]
    }

    async def semantic_bad_services(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "service_mismatch",
                    "parameter": "service_product_alignment",
                    "status": "failed",
                    "severity": "major",
                    "message": "Strategy recommends Kubernetes consulting; company sells solar installs.",
                    "recommended_correction": "Rebuild queue from Phase 5 solar topics.",
                }
            ],
            "summary": "Services mismatch.",
            "proposed_decision": "needs_revision",
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="content_strategy",
        events=events,
        semantic_caller=semantic_bad_services,
    )
    assert result.decision == ValidationDecision.NEEDS_REVISION.value
    assert any(c.parameter == "service_product_alignment" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_contradicts_earlier_phase_topic_titles():
    client = _client()
    profile = _profile(
        search_demand_summary={
            "topic_plan": [
                {"title": "Rooftop solar for Sydney homes"},
                {"title": "Home battery storage rebates"},
            ]
        }
    )
    events = _card(
        "content_strategy",
        priority_queue=[
            {"title": "Completely unrelated SaaS onboarding guide", "keyword": "saas onboarding"}
        ],
    )

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="content_strategy",
        events=events,
        skip_semantic=True,
    )
    assert result.blocks_pipeline
    assert any(c.check_id == "strategy_phase5_carry" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_company_standard_violation():
    client = _client()
    profile = _profile()
    events = _card(
        "content_production",
        briefs=[{"url": "/solar", "keyword": "rooftop solar"}],
        drafts=[
            {
                "url": "/solar",
                "body": "Guaranteed 80% savings forever — better than Sunrun.",
            }
        ],
        one_page_per_run=True,
    )
    profile.content_planning_summary = {"locked": True, "pages": [{"url": "/solar"}]}

    async def semantic_standards(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "compliance",
                    "parameter": "company_standard_compliance",
                    "status": "failed",
                    "severity": "major",
                    "message": "Draft makes guaranteed savings claims forbidden by compliance_constraints.",
                    "evidence": "Guaranteed 80% savings",
                    "recommended_correction": "Remove guarantee language; avoid competitor brand.",
                }
            ],
            "summary": "Standards violation.",
            "proposed_decision": "needs_revision",
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="content_production",
        events=events,
        semantic_caller=semantic_standards,
    )
    assert result.decision == ValidationDecision.NEEDS_REVISION.value
    assert any(c.parameter == "company_standard_compliance" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_incomplete_ia_tree():
    client = _client()
    profile = _profile()
    events = _card("site_architecture", target_url_tree=[])

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="site_architecture",
        events=events,
        skip_semantic=True,
    )
    assert result.decision == ValidationDecision.REJECT.value
    assert any(c.check_id == "ia_tree_nodes" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_unsupported_cwv_claims():
    client = _client()
    profile = _profile()
    events = _card(
        "technical_seo",
        score=88,
        findings_by_theme={"performance": ["LCP is 1.2 and CLS is 0.01"]},
        not_measured=[],
    )

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="technical_seo",
        events=events,
        skip_semantic=True,
    )
    assert result.blocks_pipeline
    assert any(c.check_id == "technical_seo_cwv" for c in result.failed_checks())


@pytest.mark.asyncio
async def test_technically_valid_but_poor_quality():
    client = _client()
    profile = _profile()
    events = _card(
        "search_demand",
        topic_plan=[{"title": "Stuff", "keyword": "stuff"}],
        cluster_report=_SOLAR_CLUSTER,
        sitemap_classification=_SOLAR_CLASSIFICATION,
        topics=[{"title": "Stuff"}],
        seed_keywords=["stuff"],
    )

    async def semantic_poor_quality(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "quality_low",
                    "parameter": "quality",
                    "status": "failed",
                    "severity": "major",
                    "message": "Topics are generic and non-actionable for solar demand research.",
                    "recommended_correction": "Produce specific, geo-aware solar topic titles.",
                }
            ],
            "summary": "Poor quality for phase objective.",
            "proposed_decision": "needs_revision",
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="search_demand",
        events=events,
        semantic_caller=semantic_poor_quality,
    )
    assert result.decision == ValidationDecision.NEEDS_REVISION.value
    assert any(c.parameter == "quality" for c in result.failed_checks())


def test_phase_specific_parameters_differ():
    tracking_params = set(list_applicable_parameters("tracking_access_agent"))
    demand_params = set(list_applicable_parameters("search_demand"))
    assert "service_product_alignment" not in tracking_params
    assert "service_product_alignment" in demand_params
    assert "geographic_market_alignment" in demand_params
    assert "honesty_anti_hallucination" in tracking_params or "honesty_anti_hallucination" in (
        get_criteria("tracking_access_agent").semantic_parameters  # type: ignore[union-attr]
    )
    # Publishing should not require Phase 5 title carry checks
    pub_checks = get_criteria("publishing").deterministic_checks  # type: ignore[union-attr]
    strat_checks = get_criteria("content_strategy").deterministic_checks  # type: ignore[union-attr]
    assert "strategy_phase5_carry" in strat_checks
    assert "strategy_phase5_carry" not in pub_checks
    assert "publishing_mode" in pub_checks
    assert len(PHASE_CRITERIA) >= 13


@pytest.mark.asyncio
async def test_revision_triggered_then_retry_limit():
    result = ValidationResult(
        agent_key="search_demand",
        phase_label="Search Demand",
        iteration=1,
        decision=ValidationDecision.NEEDS_REVISION.value,
        checks=[
            CheckResult(
                check_id="x",
                parameter="domain_alignment",
                category="domain_alignment",
                status=CheckStatus.FAILED.value,
                severity=CheckSeverity.MAJOR.value,
                message="wrong domain",
            )
        ],
        summary="needs work",
    )
    assert should_retry(result, attempt=1, max_attempts=2) is True
    assert should_retry(result, attempt=2, max_attempts=2) is False
    escalated = escalate_after_max_attempts(result)
    assert escalated.decision == ValidationDecision.REJECT.value


@pytest.mark.asyncio
async def test_malformed_validator_does_not_destroy_phase():
    client = _client()
    profile = _profile()
    events = _card(
        "search_demand",
        topic_plan=[{"title": "Rooftop solar for Sydney homes"}],
        cluster_report=_SOLAR_CLUSTER,
        sitemap_classification=_SOLAR_CLASSIFICATION,
        topics=[{"title": "Rooftop solar for Sydney homes"}],
        seed_keywords=["rooftop solar"],
    )

    async def malformed(system: str, user: str):
        _ = system, user
        return {"checks": [], "summary": ""}

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="search_demand",
        events=events,
        semantic_caller=malformed,
    )
    # Deterministic passed + semantic unavailable → pass_with_warnings, not reject
    assert result.decision == ValidationDecision.PASS_WITH_WARNINGS.value
    assert not result.blocks_pipeline
    assert result.validator_error


@pytest.mark.asyncio
async def test_unavailable_semantic_caller_none():
    client = _client()
    profile = _profile()
    events = _card(
        "site_architecture",
        target_url_tree=[
            {"url": "/", "parent": None, "depth": 0, "keyword": "home"},
            {"url": "/solar", "parent": "/", "depth": 1, "keyword": "rooftop solar"},
        ],
    )

    async def unavailable(system: str, user: str):
        _ = system, user

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="site_architecture",
        events=events,
        semantic_caller=unavailable,
    )
    assert result.decision == ValidationDecision.PASS_WITH_WARNINGS.value
    assert not result.blocks_pipeline


def _website_ctx():
    return {
        "display_name": "Acme Solar",
        "primary_url": "https://acmesolar.example",
        "industry": "solar",
    }


@pytest.mark.asyncio
async def test_website_off_domain_inventory_critical():
    events = _card(
        "website_situation_agent",
        sample_urls=["https://totally-other-site.com/page", "https://spam.example/x"],
        pages_found=2,
    )
    checks = run_deterministic_checks(
        agent_key="website_situation_agent",
        output=events[0]["payload"],
        company_ctx=_website_ctx(),
        prior={},
    )
    assert any(
        c.check_id == "website_inventory" and c.severity == CheckSeverity.CRITICAL.value
        for c in checks
    )


def test_website_inventory_from_nested_tabs_passes():
    output = {
        "card_type": "website_audit",
        "agent_key": "website_situation_agent",
        "tabs": {
            "technical": {
                "pages_found": 13,
                "note": "Indexed URLs recovered after fetch_bot_challenge (HTTP 202).",
                "status_samples": [
                    {"url": "https://acmesolar.example/borrow", "status": 202},
                    {"url": "https://www.acmesolar.example/bridging-finance", "status": "WORKING"},
                ],
                "discovered_urls": ["https://acmesolar.example/construction-loans"],
            }
        },
    }
    checks = run_deterministic_checks(
        agent_key="website_situation_agent",
        output=output,
        company_ctx=_website_ctx(),
        prior={},
    )
    inventory = next(c for c in checks if c.check_id == "website_inventory")
    assert inventory.status == CheckStatus.PASSED.value


def test_website_inventory_dict_pages_on_domain_pass():
    output = {
        "card_type": "website_audit",
        "pages": [
            {"url": "https://acmesolar.example/borrow", "status": "WORKING", "title": "Borrow"},
            {"url": "/construction-loans", "status": 200},
        ],
    }
    checks = run_deterministic_checks(
        agent_key="website_situation_agent",
        output=output,
        company_ctx=_website_ctx(),
        prior={},
    )
    inventory = next(c for c in checks if c.check_id == "website_inventory")
    assert inventory.status == CheckStatus.PASSED.value


def test_website_inventory_empty_card_still_fails():
    output = {
        "card_type": "website_audit",
        "tabs": {"technical": {"severity": "info"}},
        "severities": {"technical": "info"},
    }
    checks = run_deterministic_checks(
        agent_key="website_situation_agent",
        output=output,
        company_ctx=_website_ctx(),
        prior={},
    )
    inventory = next(c for c in checks if c.check_id == "website_inventory")
    assert inventory.status == CheckStatus.FAILED.value
    assert inventory.severity == CheckSeverity.MAJOR.value


def test_extract_phase_output_merges_website_summary_inventory():
    profile = _profile(
        website_situation_summary={
            "pages_found": 30,
            "sample_urls": [
                "https://acmesolar.example/borrow",
                "https://acmesolar.example/bridging-finance",
            ],
            "note": "SERP inventory after bot challenge",
        }
    )
    events = _card("website_situation_agent", card_type="website_audit", tabs={"technical": {}})
    output = extract_phase_output(
        profile=profile,
        agent_key="website_situation_agent",
        events=events,
    )
    assert output["pages_found"] == 30
    assert "https://acmesolar.example/borrow" in output["sample_urls"]

    checks = run_deterministic_checks(
        agent_key="website_situation_agent",
        output=output,
        company_ctx=_website_ctx(),
        prior={},
    )
    inventory = next(c for c in checks if c.check_id == "website_inventory")
    assert inventory.status == CheckStatus.PASSED.value


def test_extract_phase_output_skips_validation_report_card():
    profile = _profile(
        search_demand_summary={
            "topic_plan": [{"title": "Car loans Melbourne"}],
            "cluster_report": _SOLAR_CLUSTER,
            "sitemap_classification": _SOLAR_CLASSIFICATION,
        }
    )
    events = _card(
        "search_demand",
        card_type="search_demand_report",
        topic_plan=[{"title": "Car loans Melbourne"}],
        cluster_report=_SOLAR_CLUSTER,
        sitemap_classification=_SOLAR_CLASSIFICATION,
        seed_keywords=["car loan melbourne"],
    )
    events.extend(
        [
            {
                "type": "structured_card",
                "payload": {
                    "card_type": "phase_validation_report",
                    "agent_key": "search_demand",
                    "decision": "pass",
                    "checks": [],
                },
            }
        ]
    )
    output = extract_phase_output(
        profile=profile,
        agent_key="search_demand",
        events=events,
    )
    assert output.get("card_type") == "search_demand_report"
    assert output.get("topic_plan")


def test_semantic_prompt_keeps_phase_output_when_prior_is_huge():
    from app.services.phase_validation.prompts import build_semantic_user_prompt

    prior = {
        "website_situation_summary": {
            "pages": [{"url": f"https://acmesolar.example/p{i}", "body": "x" * 2000} for i in range(80)],
            "tabs": {"technical": {"status_samples": [{"url": f"https://acmesolar.example/{i}"} for i in range(80)]}},
        }
    }
    output = {
        "card_type": "competitor_landscape",
        "scorecards": [
            {"name": "Mortgage Choice", "url": "https://www.mortgagechoice.com.au", "composite": 58}
        ],
        "competitors": [{"name": "Mortgage Choice", "url": "https://www.mortgagechoice.com.au"}],
        "client_baseline": {"maturity_score": 36},
    }
    prompt = build_semantic_user_prompt(
        agent_key="competitor_market_agent",
        company_ctx={"display_name": "Acme Solar", "industry": "solar"},
        prior=prior,
        output=output,
        iteration=1,
    )
    assert prompt.index("phase_output") < prompt.index("prior_phase_outputs")
    assert "Mortgage Choice" in prompt
    assert "scorecards" in prompt


@pytest.mark.asyncio
async def test_semantic_missing_payload_fail_does_not_reject_real_peers():
    client = _client(industry="mortgage broking")
    profile = _profile()
    events = _card(
        "competitor_market_agent",
        scorecards=[
            {
                "name": "Mortgage Choice",
                "url": "https://www.mortgagechoice.com.au",
                "tier": 2,
                "composite": 58,
            }
        ],
        competitors=[{"name": "Mortgage Choice", "url": "https://www.mortgagechoice.com.au"}],
        client_baseline={"maturity_score": 36, "name": "ARG Finance"},
        excluded_tier5=[],
    )

    async def semantic_truncated(system: str, user: str):
        _ = system, user
        return {
            "checks": [
                {
                    "check_id": "missing_output",
                    "parameter": "phase_objective_compliance",
                    "status": "failed",
                    "severity": "critical",
                    "message": (
                        "Payload only contains company_context and prior_phase outputs, "
                        "missing the analysis phase output required for validation."
                    ),
                }
            ],
            "summary": "Cannot confirm phase objective.",
            "proposed_decision": "reject",
        }

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="competitor_market_agent",
        events=events,
        semantic_caller=semantic_truncated,
    )
    assert result.decision != ValidationDecision.REJECT.value
    assert not result.blocks_pipeline


@pytest.mark.asyncio
async def test_empty_output_critical():
    client = _client()
    profile = _profile()
    events = _card("content_audit")  # no inventory

    result = await validate_phase_output(
        client=client,
        profile=profile,
        agent_key="content_audit",
        events=events,
        skip_semantic=True,
    )
    assert result.blocks_pipeline


@pytest.mark.asyncio
async def test_run_phase_with_validation_revision_and_limit(db_session):
    import os

    from app.config import clear_settings_cache, get_settings
    from app.models import Client, ClientDigitalProfile
    from app.services.phase_validation.apply import run_phase_with_validation

    os.environ["VALIDATION_MAX_ATTEMPTS"] = "2"
    clear_settings_cache()
    assert get_settings().validation_max_attempts == 2

    client = Client(
        legal_name="Acme Solar Pty Ltd",
        display_name="Acme Solar",
        primary_url="https://acmesolar.example",
        industry="residential solar",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(client_id=client.id)
    db_session.add(profile)
    await db_session.flush()

    calls = {"n": 0}

    async def flaky_runner(db, *, client, session_id, user_id, message):
        _ = client, session_id, user_id, message
        calls["n"] += 1
        profile.site_architecture_status = "pending_signoff"
        # Always empty tree → deterministic reject path
        profile.site_architecture_summary = {"target_url_tree": []}
        await db.flush()
        return _card("site_architecture", target_url_tree=[])

    # Patch validate to skip semantic so mock LLM doesn't matter
    import app.services.phase_validation.apply as apply_mod
    import app.services.phase_validation.service as svc_mod

    original = svc_mod.validate_phase_output

    async def validate_no_semantic(**kwargs):
        kwargs["skip_semantic"] = True
        return await original(**kwargs)

    apply_mod.validate_phase_output = validate_no_semantic
    try:
        events = await run_phase_with_validation(
            db_session,
            runner=flaky_runner,
            client=client,
            profile=profile,
            session_id=uuid4(),
            user_id=uuid4(),
            agent_key="site_architecture",
            message="Run site architecture",
        )
    finally:
        apply_mod.validate_phase_output = original
        clear_settings_cache()

    # Empty tree is CRITICAL → REJECT immediately (no revision for critical)
    assert calls["n"] == 1
    assert profile.site_architecture_status == "in_progress"
    reports = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("card_type") == "phase_validation_report"
    ]
    assert reports
    assert reports[-1]["payload"]["decision"] == ValidationDecision.REJECT.value
    assert reports[-1]["payload"]["agent_key"] == "site_architecture"
    # Approve is stripped so a bad output cannot be signed off
    cards = [ev for ev in events if ev.get("type") == "structured_card"]
    arch_cards = [
        c
        for c in cards
        if (c.get("payload") or {}).get("agent_key") == "site_architecture"
        and (c.get("payload") or {}).get("card_type") != "phase_validation_report"
    ]
    assert arch_cards
    assert "approve" not in (arch_cards[0]["payload"].get("actions") or [])
    assert arch_cards[0]["payload"].get("validation_blocked_approve") is True


@pytest.mark.asyncio
async def test_revision_loop_for_major_then_pass(db_session):
    import os

    import app.services.phase_validation.apply as apply_mod
    import app.services.phase_validation.service as svc_mod
    from app.config import clear_settings_cache
    from app.models import Client, ClientDigitalProfile
    from app.services.phase_validation.apply import run_phase_with_validation

    os.environ["VALIDATION_MAX_ATTEMPTS"] = "2"
    clear_settings_cache()

    client = Client(
        legal_name="Acme Solar Pty Ltd",
        display_name="Acme Solar",
        primary_url="https://acmesolar.example",
        industry="residential solar",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(
        client_id=client.id,
        search_demand_summary={
            "topic_plan": [{"title": "Rooftop solar for Sydney homes"}]
        },
    )
    db_session.add(profile)
    await db_session.flush()

    calls = {"n": 0}

    async def improving_runner(db, *, client, session_id, user_id, message):
        _ = client, session_id, user_id, message
        calls["n"] += 1
        profile.seo_strategy_status = "pending_signoff"
        if calls["n"] == 1:
            profile.seo_strategy_summary = {
                "priority_queue": [{"title": "Unrelated SaaS guide", "keyword": "saas"}]
            }
            await db.flush()
            return _card(
                "content_strategy",
                priority_queue=[{"title": "Unrelated SaaS guide", "keyword": "saas"}],
            )
        profile.seo_strategy_summary = {
            "priority_queue": [
                {
                    "title": "Rooftop solar for Sydney homes",
                    "keyword": "rooftop solar sydney",
                    "from_phase5_topic": True,
                }
            ]
        }
        await db.flush()
        return _card(
            "content_strategy",
            priority_queue=[
                {
                    "title": "Rooftop solar for Sydney homes",
                    "keyword": "rooftop solar sydney",
                    "from_phase5_topic": True,
                }
            ],
        )

    original = svc_mod.validate_phase_output

    async def validate_no_semantic(**kwargs):
        kwargs["skip_semantic"] = True
        return await original(**kwargs)

    apply_mod.validate_phase_output = validate_no_semantic
    try:
        events = await run_phase_with_validation(
            db_session,
            runner=improving_runner,
            client=client,
            profile=profile,
            session_id=uuid4(),
            user_id=uuid4(),
            agent_key="content_strategy",
            message="Run content strategy",
        )
    finally:
        apply_mod.validate_phase_output = original
        clear_settings_cache()

    # Looped internally once then passed; only the final QC report is surfaced
    assert calls["n"] == 2
    reports = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("card_type") == "phase_validation_report"
    ]
    assert reports
    assert reports[-1]["payload"]["decision"] == ValidationDecision.PASS.value
    assert reports[-1]["payload"]["agent_key"] == "content_strategy"
    # The accepted phase card is returned with its normal approve action intact
    strat_cards = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("agent_key") == "content_strategy"
        and (ev.get("payload") or {}).get("card_type") != "phase_validation_report"
        and ev.get("type") == "structured_card"
    ]
    assert strat_cards
    assert strat_cards[-1]["payload"].get("validation_blocked_approve") is not True
    # Latest persisted validation is a pass — approve is not blocked
    from app.services.phase_validation import latest_validation_blocks_approve

    blocked = await latest_validation_blocks_approve(
        db_session, client_id=client.id, agent_key="content_strategy"
    )
    assert blocked is None


def test_decide_from_checks_severity_priority():
    checks = [
        CheckResult(
            check_id="a",
            parameter="quality",
            category="quality",
            status=CheckStatus.FAILED.value,
            severity=CheckSeverity.MINOR.value,
            message="minor",
        ),
        CheckResult(
            check_id="b",
            parameter="domain_alignment",
            category="domain_alignment",
            status=CheckStatus.FAILED.value,
            severity=CheckSeverity.CRITICAL.value,
            message="critical",
        ),
    ]
    assert decide_from_checks(checks) == ValidationDecision.REJECT.value


@pytest.mark.asyncio
async def test_major_failure_hits_retry_limit_then_rejects(db_session):
    import os

    import app.services.phase_validation.apply as apply_mod
    import app.services.phase_validation.service as svc_mod
    from app.config import clear_settings_cache
    from app.models import Client, ClientDigitalProfile
    from app.services.phase_validation.apply import run_phase_with_validation

    os.environ["VALIDATION_MAX_ATTEMPTS"] = "2"
    clear_settings_cache()

    client = Client(
        legal_name="Acme Solar Pty Ltd",
        display_name="Acme Solar",
        primary_url="https://acmesolar.example",
        industry="residential solar",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(
        client_id=client.id,
        search_demand_summary={
            "topic_plan": [{"title": "Rooftop solar for Sydney homes"}]
        },
    )
    db_session.add(profile)
    await db_session.flush()

    calls = {"n": 0}

    async def stubborn_runner(db, *, client, session_id, user_id, message):
        _ = client, session_id, user_id, message
        calls["n"] += 1
        profile.seo_strategy_status = "pending_signoff"
        profile.seo_strategy_summary = {
            "priority_queue": [{"title": "Unrelated SaaS guide", "keyword": "saas"}]
        }
        await db.flush()
        return _card(
            "content_strategy",
            priority_queue=[{"title": "Unrelated SaaS guide", "keyword": "saas"}],
        )

    original = svc_mod.validate_phase_output

    async def validate_no_semantic(**kwargs):
        kwargs["skip_semantic"] = True
        return await original(**kwargs)

    apply_mod.validate_phase_output = validate_no_semantic
    try:
        events = await run_phase_with_validation(
            db_session,
            runner=stubborn_runner,
            client=client,
            profile=profile,
            session_id=uuid4(),
            user_id=uuid4(),
            agent_key="content_strategy",
            message="Run content strategy",
        )
    finally:
        apply_mod.validate_phase_output = original
        clear_settings_cache()

    assert calls["n"] == 2
    assert profile.seo_strategy_status == "in_progress"
    reports = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("card_type") == "phase_validation_report"
    ]
    assert reports
    assert reports[-1]["payload"]["decision"] == ValidationDecision.REJECT.value
    # Approve is stripped on the phase card
    strat_cards = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("agent_key") == "content_strategy"
        and (ev.get("payload") or {}).get("card_type") != "phase_validation_report"
        and ev.get("type") == "structured_card"
    ]
    assert strat_cards
    assert strat_cards[-1]["payload"].get("validation_blocked_approve") is True

    from app.services.phase_validation import latest_validation_blocks_approve

    blocked = await latest_validation_blocks_approve(
        db_session, client_id=client.id, agent_key="content_strategy"
    )
    assert blocked is not None
    assert blocked.decision == ValidationDecision.REJECT.value


@pytest.mark.asyncio
async def test_latest_validation_unblocks_stale_semantic_reject_for_competitor(db_session):
    from app.models import Client, ClientDigitalProfile
    from app.models.governance import PhaseValidation
    from app.services.phase_validation import latest_validation_blocks_approve

    client = Client(
        legal_name="ARG Finance Pty Ltd",
        display_name="ARG Finance",
        primary_url="https://argfinance.com.au",
        industry="mortgage broking",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(
        client_id=client.id,
        competitive_landscape_summary={
            "_draft": True,
            "competitors": [
                {"name": "Mortgage Choice", "url": "https://www.mortgagechoice.com.au"}
            ],
            "scorecards": [
                {
                    "name": "Mortgage Choice",
                    "url": "https://www.mortgagechoice.com.au",
                    "tier": 2,
                    "composite": 58,
                }
            ],
            "client_baseline_maturity": 36,
        },
    )
    db_session.add(profile)
    await db_session.flush()

    stale = PhaseValidation(
        client_id=client.id,
        session_id=None,
        agent_key="competitor_market_agent",
        iteration=2,
        decision=ValidationDecision.REJECT.value,
        result={
            "agent_key": "competitor_market_agent",
            "phase_label": "Competitor & Market Analysis",
            "iteration": 2,
            "decision": ValidationDecision.REJECT.value,
            "summary": (
                "Output rejected — fundamentally invalid. "
                "The payload only contains company_context/prior."
            ),
            "checks": [],
        },
        output_fingerprint="deadbeefdeadbeef",
    )
    db_session.add(stale)
    await db_session.flush()

    blocked = await latest_validation_blocks_approve(
        db_session, client_id=client.id, agent_key="competitor_market_agent"
    )
    assert blocked is None


@pytest.mark.asyncio
async def test_latest_validation_still_blocks_when_live_output_is_empty(db_session):
    from app.models import Client, ClientDigitalProfile
    from app.models.governance import PhaseValidation
    from app.services.phase_validation import latest_validation_blocks_approve

    client = Client(
        legal_name="Acme Solar Pty Ltd",
        display_name="Acme Solar",
        primary_url="https://acmesolar.example",
        industry="solar",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(client_id=client.id, seo_strategy_summary={})
    db_session.add(profile)
    await db_session.flush()

    stale = PhaseValidation(
        client_id=client.id,
        session_id=None,
        agent_key="content_strategy",
        iteration=1,
        decision=ValidationDecision.REJECT.value,
        result={
            "agent_key": "content_strategy",
            "phase_label": "Content Strategy",
            "iteration": 1,
            "decision": ValidationDecision.REJECT.value,
            "summary": "No priority queue.",
            "checks": [],
        },
    )
    db_session.add(stale)
    await db_session.flush()

    blocked = await latest_validation_blocks_approve(
        db_session, client_id=client.id, agent_key="content_strategy"
    )
    assert blocked is not None
    assert blocked.decision == ValidationDecision.REJECT.value


@pytest.mark.asyncio
async def test_search_demand_surfaces_validation_report(db_session):
    import app.services.phase_validation.apply as apply_mod
    import app.services.phase_validation.service as svc_mod
    from app.config import clear_settings_cache
    from app.models import Client, ClientDigitalProfile
    from app.services.phase_validation.apply import run_phase_with_validation

    client = Client(
        legal_name="Acme Solar Pty Ltd",
        display_name="Acme Solar",
        primary_url="https://acmesolar.example",
        industry="residential solar",
    )
    db_session.add(client)
    await db_session.flush()
    profile = ClientDigitalProfile(
        client_id=client.id,
        commercial_scope={"products": ["rooftop solar panels"]},
    )
    db_session.add(profile)
    await db_session.flush()

    async def demand_runner(db, *, client, session_id, user_id, message):
        _ = db, client, session_id, user_id, message
        profile.search_demand_status = "pending_signoff"
        profile.search_demand_summary = {
            "topic_plan": [
                {"title": "Rooftop solar for Sydney homes", "keyword": "rooftop solar sydney"}
            ],
            "cluster_report": _SOLAR_CLUSTER,
            "sitemap_classification": _SOLAR_CLASSIFICATION,
            "seed_keywords": ["rooftop solar", "battery storage sydney"],
        }
        return _card(
            "search_demand",
            card_type="search_demand_report",
            topic_plan=profile.search_demand_summary["topic_plan"],
            cluster_report=_SOLAR_CLUSTER,
            sitemap_classification=_SOLAR_CLASSIFICATION,
            seed_keywords=profile.search_demand_summary["seed_keywords"],
        )

    original = svc_mod.validate_phase_output

    async def validate_no_semantic(**kwargs):
        kwargs["skip_semantic"] = True
        return await original(**kwargs)

    apply_mod.validate_phase_output = validate_no_semantic
    try:
        events = await run_phase_with_validation(
            db_session,
            runner=demand_runner,
            client=client,
            profile=profile,
            session_id=uuid4(),
            user_id=uuid4(),
            agent_key="search_demand",
            message="Run keyword research / search demand",
        )
    finally:
        apply_mod.validate_phase_output = original
        clear_settings_cache()

    reports = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("card_type") == "phase_validation_report"
    ]
    assert reports
    payload = reports[-1]["payload"]
    assert payload["agent_key"] == "search_demand"
    assert payload["phase_label"] == "Search Demand & Keywords"
    assert payload["decision"] in (
        ValidationDecision.PASS.value,
        ValidationDecision.PASS_WITH_WARNINGS.value,
    )
    demand_cards = [
        ev
        for ev in events
        if (ev.get("payload") or {}).get("card_type") == "search_demand_report"
    ]
    assert demand_cards
    assert demand_cards[-1]["payload"].get("validation_blocked_approve") is not True
