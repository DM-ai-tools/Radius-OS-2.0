"""The scope is enforced everywhere the selection used to be advisory."""

from app.services.service_prioritization import (
    apply_service_selection,
    compact_selected_services,
    confirm_prioritization,
    default_selection,
    expand_vertical_subservice_seeds,
    ia_nodes_from_competitor_tree,
)

CATALOG = [
    {
        "id": "social media marketing",
        "name": "Social Media Marketing",
        "source": "website",
        "subservices": [
            {"id": "meta ads", "name": "Meta Ads", "source": "website", "path": "/smm/meta-ads"},
            {"id": "instagram ads", "name": "Instagram Ads", "source": "website"},
            {"id": "tiktok-ads", "name": "TikTok Ads", "source": "competitor"},
            {"id": "pinterest-ads", "name": "Pinterest Ads", "source": "competitor"},
        ],
    }
]

SCOPE = {
    "social media marketing": {
        "name": "Social Media Marketing",
        "offered": ["Meta Ads", "Instagram Ads"],
        "excluded": ["TikTok Ads", "Pinterest Ads"],
    }
}


def _prio(**extra):
    return {
        "confirmed_at": "x",
        "selected_service_ids": ["social media marketing"],
        # the old UI sent EVERY sub-service, competitor ones included
        "selected_subservice_ids": ["meta ads", "instagram ads", "tiktok-ads", "pinterest-ads"],
        "service_catalog": CATALOG,
        "service_scope": SCOPE,
        **extra,
    }


def test_default_selection_does_not_select_competitor_subservices():
    catalog = [dict(CATALOG[0], subservices=[dict(s) for s in CATALOG[0]["subservices"]])]
    result = default_selection(catalog)
    assert result["selected_subservice_ids"] == ["meta ads", "instagram ads"]
    assert [s["selected"] for s in catalog[0]["subservices"]] == [True, True, False, False]
    entry = result["service_scope"]["social media marketing"]
    assert [o["name"] for o in entry["offered"]] == ["Meta Ads", "Instagram Ads"]
    assert "TikTok Ads" in [e["name"] for e in entry["excluded"]]


def test_compact_selected_services_only_lists_offered_subservices():
    out = compact_selected_services(_prio(), service_catalog=CATALOG)
    assert [s["name"] for s in out[0]["subservices"]] == ["Meta Ads", "Instagram Ads"]


def test_compact_selected_services_empty_offered_means_no_subservices():
    prio = _prio(
        service_scope={"social media marketing": {"name": "SMM", "offered": [], "excluded": []}}
    )
    out = compact_selected_services(prio, service_catalog=CATALOG)
    assert out[0]["subservices"] == []


def test_compact_selected_services_unscoped_keeps_legacy_behaviour():
    prio = _prio()
    prio.pop("service_scope")
    out = compact_selected_services(prio, service_catalog=CATALOG)
    assert len(out[0]["subservices"]) == 4  # legacy flat list lists all four


def test_expand_vertical_seeds_skips_non_offered_subservices():
    roots, targets = expand_vertical_subservice_seeds(
        [], {}, prioritization=_prio(), service_catalog=CATALOG
    )
    assert sorted(roots) == ["Instagram Ads", "Meta Ads"]
    assert "tiktok ads" not in targets


def test_expand_vertical_seeds_scoped_empty_offered_adds_nothing():
    prio = _prio(service_scope={"social media marketing": {"name": "SMM", "offered": [], "excluded": []}})
    roots, _ = expand_vertical_subservice_seeds([], {}, prioritization=prio, service_catalog=CATALOG)
    assert roots == []


def test_apply_service_selection_drops_non_offered_subservice_seed():
    targets = {
        "meta ads": {"target": "Meta Ads", "target_type": "sub_service", "parent_segment": "social-media-marketing"},
        "tiktok ads": {"target": "TikTok Ads", "target_type": "sub_service", "parent_segment": "social-media-marketing"},
    }
    _services, kept = apply_service_selection(["Social Media Marketing"], targets, _prio())
    assert list(kept) == ["meta ads"]


def test_ia_nodes_exclude_non_offered_subservices():
    tree = {"name": "catalog", "services": CATALOG}
    nodes = ia_nodes_from_competitor_tree(
        tree,
        domain="example.com",
        selected_service_ids=["social media marketing"],
        selected_subservice_ids=["meta ads", "instagram ads", "tiktok-ads", "pinterest-ads"],
        service_scope=SCOPE,
    )
    subs = sorted(n["title"] for n in nodes if n["page_type"] == "subservice")
    assert subs == ["Instagram Ads", "Meta Ads"]


def test_confirm_prioritization_normalises_scope_and_derives_flat_ids():
    out = confirm_prioritization(
        {
            "selected_service_ids": ["social media marketing"],
            "selected_subservice_ids": ["meta ads", "instagram ads", "tiktok-ads", "pinterest-ads"],
            "service_catalog": CATALOG,
            "service_scope": SCOPE,
        }
    )
    assert out["selected_subservice_ids"] == ["meta ads", "instagram ads"]
    entry = out["service_scope"]["social media marketing"]
    assert entry["decided_at"]
    assert [e["id"] for e in entry["excluded"]] == ["tiktok ads", "pinterest ads"]


from app.services.keyword_validation_gate import validate_keywords


async def test_validate_keywords_drops_non_offered_subservice_keywords_without_llm():
    rows = [
        {"keyword": "meta ads agency melbourne", "target": "meta ads"},
        {"keyword": "tiktok ads agency melbourne", "target": "meta ads"},
    ]
    kept, dropped, audit = await validate_keywords(
        rows,
        client_id="c1",
        services=["Social Media Marketing"],
        enabled=False,  # LLM judge off: scope drop must not depend on it
        prioritization=_prio(),
    )
    assert [r["keyword"] for r in kept] == ["meta ads agency melbourne"]
    assert [r["keyword"] for r in dropped] == ["tiktok ads agency melbourne"]
    assert dropped[0]["relevance_drop_reason"] == "sub_service_not_offered"
    assert audit["scope_dropped"] == 1


async def test_validate_keywords_without_prioritization_is_unchanged():
    rows = [{"keyword": "tiktok ads agency melbourne", "target": "meta ads"}]
    kept, dropped, audit = await validate_keywords(
        rows, client_id="c1", services=[], enabled=False
    )
    assert audit["scope_dropped"] == 0
    assert all(d.get("relevance_drop_reason") != "sub_service_not_offered" for d in dropped)


from app.services.content_signals.context import build_content_context, render_context_block
from app.services.content_signals.validation import business_rules_block


def _commercial():
    return {"service_prioritization": _prio()}


def test_context_carries_scope_for_the_service():
    ctx = build_content_context(
        keyword="meta ads melbourne",
        page_type="service",
        commercial=_commercial(),
        service_id="social media marketing",
    )
    assert ctx["service_scope"]["scoped"] is True
    assert ctx["service_scope"]["offered"] == ["Meta Ads", "Instagram Ads"]
    assert "TikTok Ads" in ctx["service_scope"]["excluded"]


def test_context_without_service_or_scope_has_none():
    ctx = build_content_context(keyword="x", page_type="blog", commercial={})
    assert ctx["service_scope"] is None


def test_scope_does_not_change_the_relevance_score():
    with_scope = build_content_context(
        keyword="k", commercial=_commercial(), service_id="social media marketing"
    )
    without = build_content_context(keyword="k", commercial={})
    assert with_scope["relevance"]["score"] == without["relevance"]["score"]


def test_prompt_block_and_rules_state_offered_and_excluded():
    ctx = build_content_context(
        keyword="meta ads", commercial=_commercial(), service_id="social media marketing"
    )
    block = render_context_block(ctx)
    assert "Meta Ads, Instagram Ads" in block
    assert "TikTok Ads" in block and "Never" in block
    assert "TikTok Ads" in business_rules_block(ctx)


def test_unscoped_service_gets_a_warning_note_not_a_rule():
    prio = _prio(service_catalog=CATALOG)
    prio.pop("service_scope")
    ctx = build_content_context(
        keyword="k", commercial={"service_prioritization": prio}, service_id="social media marketing"
    )
    assert ctx["service_scope"]["scoped"] is False
    assert "not been confirmed" in render_context_block(ctx)
    assert "Sub-services" not in business_rules_block(ctx)


from app.services.content_signals.validation import validate_business_logic
from app.services.create_content import write_one_page
from app.services.phase_validation.deterministic import _check_production_sub_service_scope

SCOPED_CTX = {
    "signals": {},
    "service_scope": {
        "service": "Social Media Marketing",
        "service_id": "social media marketing",
        "scoped": True,
        "offered": ["Meta Ads", "Instagram Ads"],
        "excluded": ["TikTok Ads", "Pinterest Ads"],
    },
}


def test_validate_business_logic_fails_when_draft_names_an_excluded_sub_service():
    result = validate_business_logic(
        {"markdown": "We run Meta Ads and TikTok Ads for retailers."}, SCOPED_CTX
    )
    check = next(c for c in result["checks"] if c["name"] == "sub_service_scope")
    assert check["ok"] is False and check["severity"] == "fail"
    assert "TikTok Ads" in check["detail"]
    assert result["ok"] is False


def test_validate_business_logic_passes_when_draft_stays_in_scope():
    result = validate_business_logic({"markdown": "We run Meta Ads and Instagram Ads."}, SCOPED_CTX)
    check = next(c for c in result["checks"] if c["name"] == "sub_service_scope")
    assert check["ok"] is True


def test_validate_business_logic_skips_scope_check_when_unscoped():
    result = validate_business_logic({"markdown": "TikTok Ads"}, {"signals": {}})
    assert not any(c["name"] == "sub_service_scope" for c in result["checks"])


def _brief(**over):
    brief = {
        "keyword": "tiktok ads agency melbourne",
        "url": "/social-media-marketing/tiktok-ads/",
        "parent": "/social-media-marketing/",
        "action": "create",
        "writer_ready": True,
        "author": "Sam Writer",
        "differentiation": "A case-study-led angle with original conversion data from three clients.",
    }
    brief.update(over)
    return brief


async def test_write_one_page_refuses_before_generation_on_excluded_keyword():
    out = await write_one_page(
        brief=_brief(),
        client_name="Acme",
        use_llm=False,
        enhance_brief=False,
        content_context=SCOPED_CTX,
    )
    assert out["ok"] is False and out["refused"] is True
    assert out["route_to"] == "service_scope"
    assert out["scope_violations"][0]["sub_service"] == "TikTok Ads"
    assert "TikTok Ads" in out["reason"]


async def test_write_one_page_refuses_when_a_secondary_keyword_is_excluded():
    out = await write_one_page(
        brief=_brief(keyword="meta ads agency melbourne", secondary_keywords=["pinterest ads cost"]),
        client_name="Acme",
        use_llm=False,
        enhance_brief=False,
        content_context=SCOPED_CTX,
    )
    assert out["refused"] is True and out["route_to"] == "service_scope"


async def test_write_one_page_in_scope_is_not_refused_for_scope():
    out = await write_one_page(
        brief=_brief(keyword="meta ads agency melbourne"),
        client_name="Acme",
        use_llm=False,
        enhance_brief=False,
        content_context=SCOPED_CTX,
    )
    assert out.get("route_to") != "service_scope"


def test_phase_check_fails_on_a_draft_with_a_scope_failure():
    drafts = [
        {
            "business_validation": {
                "failures": [
                    {"name": "sub_service_scope", "ok": False, "detail": "Draft names TikTok Ads."}
                ]
            }
        }
    ]
    results = _check_production_sub_service_scope(
        criteria=None, output={"drafts": drafts}, company_ctx={}, prior={}
    )
    assert results[0].status == "failed"
    assert results[0].check_id == "production_sub_service_scope"


def test_phase_check_passes_with_no_scope_failures_and_ignores_non_list_drafts():
    ok = _check_production_sub_service_scope(
        criteria=None,
        output={"drafts": [{"business_validation": {"failures": []}}]},
        company_ctx={},
        prior={},
    )
    assert ok[0].status == "passed"
    count_only = _check_production_sub_service_scope(
        criteria=None, output={"drafts": 1}, company_ctx={}, prior={}
    )
    assert count_only[0].status == "passed"
