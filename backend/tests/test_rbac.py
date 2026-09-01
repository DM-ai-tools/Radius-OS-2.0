from app.services.role_skills import triggerable_agents


def test_csm_triggers_discovery_and_tracking():
    # CSM owns Discovery outright and can trigger Tracking's known-changes step
    # (T5), even though Tech SEO owns the T6 sign-off — see role_skills.py.
    assert set(triggerable_agents("client_success_manager")) == {
        "discovery_agent",
        "tracking_access_agent",
    }


def test_tech_has_tracking_website_and_technical_seo():
    assert set(triggerable_agents("technical_seo_specialist")) == {
        "tracking_access_agent",
        "website_situation_agent",
        "competitor_market_agent",
        "technical_seo",
        "on_page_seo",
    }


def test_qa_only_gate():
    assert set(triggerable_agents("seo_qa_lead")) == {
        "readiness_gate",
        "competitor_market_agent",
    }


def test_strategist_phase_7_12_triggers():
    agents = set(triggerable_agents("seo_strategist"))
    assert "content_audit" in agents
    assert "content_planning" in agents
    assert "publishing" in agents
    assert "site_architecture" in agents


def test_content_seo_production_trigger():
    agents = set(triggerable_agents("content_seo_specialist"))
    assert "search_demand" in agents
    assert "content_production" in agents
    assert "content_planning" in agents
