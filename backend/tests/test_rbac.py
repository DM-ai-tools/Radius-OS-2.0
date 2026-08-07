from app.services.role_skills import triggerable_agents


def test_csm_triggers_discovery_and_tracking():
    # CSM owns Discovery outright and can trigger Tracking's known-changes step
    # (T5), even though Tech SEO owns the T6 sign-off — see role_skills.py.
    assert set(triggerable_agents("client_success_manager")) == {
        "discovery_agent",
        "tracking_access_agent",
    }


def test_tech_has_tracking_and_website():
    assert set(triggerable_agents("technical_seo_specialist")) == {
        "tracking_access_agent",
        "website_situation_agent",
    }


def test_qa_only_gate():
    assert triggerable_agents("seo_qa_lead") == ["readiness_gate"]
