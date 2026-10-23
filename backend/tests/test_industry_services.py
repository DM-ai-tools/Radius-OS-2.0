"""Primary services of the client's industry and their sub-services.

The card should show a handful of primary services (SEO, PPC Management, Social Media
Marketing, ...), each with its specific sub-services, and not a pile of site pages,
page candidates and competitor services.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.industry_services import (
    MAX_SERVICES,
    MAX_SUBSERVICES,
    build_catalog_from_taxonomy,
    generate_industry_taxonomy,
    normalize_taxonomy,
    offering_names,
    taxonomy_for_client,
)
from app.services.service_prioritization import (
    build_prioritization_pack,
    confirm_prioritization,
    default_selection,
    merge_saved_catalog,
)
from app.services.service_scope import build_default_scope

SITE_CATALOG = [
    {
        "id": "seo",
        "name": "SEO",
        "source": "website",
        "path": "/seo",
        "subservices": [
            {"id": "local seo", "name": "Local SEO", "source": "website", "path": "/seo/local-seo"}
        ],
    },
    {"id": "google ads/ppc management", "name": "Google Ads/PPC Management", "source": "cdd", "subservices": []},
    {"id": "meta ads", "name": "Meta Ads", "source": "website", "path": "/meta-ads", "subservices": []},
    # a plain page the classifier offered as a candidate: not an offering
    {"id": "borrow", "name": "Borrow", "source": "website_page", "candidate_new_service": True, "subservices": []},
]
OFFERINGS = ["SEO", "Local SEO", "Google Ads/PPC Management", "Meta Ads"]

TAXONOMY = [
    {
        "name": "SEO",
        "covers": ["SEO"],
        "subservices": [
            {"name": "Local SEO", "covers": ["Local SEO"]},
            {"name": "Technical SEO", "covers": []},
            {"name": "Link Building", "covers": []},
        ],
    },
    {
        "name": "PPC Management",
        "covers": ["Google Ads/PPC Management"],
        "subservices": [
            {"name": "Google Ads Management", "covers": ["Google Ads/PPC Management"]},
            {"name": "Bing Ads Management", "covers": []},
        ],
    },
    {
        "name": "Social Media Marketing",
        "covers": ["Meta Ads"],
        "subservices": [
            {"name": "Meta Ads", "covers": ["Meta Ads"]},
            {"name": "Facebook Ads", "covers": []},
            {"name": "Instagram Ads", "covers": []},
        ],
    },
    {"name": "Content Marketing", "covers": [], "subservices": [{"name": "Blog Writing", "covers": []}]},
]


# --- what the client offers ---------------------------------------------------


def test_offering_names_are_the_services_and_subservices_but_not_page_candidates():
    names = offering_names(SITE_CATALOG, ["SEO"])
    assert set(names) == set(OFFERINGS)
    assert "Borrow" not in names
    # top-level offerings come before sub-services, so a cap drops the least important
    assert names.index("Local SEO") > names.index("Meta Ads")


def test_offering_names_are_capped_and_keep_the_top_level_ones():
    catalog = [
        {
            "id": f"svc {i}",
            "name": f"Service {chr(65 + i)}{chr(75 + i)}",
            "source": "website",
            "subservices": [
                {"id": f"s{i}{n}", "name": f"Sub {chr(65 + i)} {n}", "source": "website"} for n in range(5)
            ],
        }
        for i in range(10)
    ]
    names = offering_names(catalog, [])
    assert len(names) == 30
    assert all(f"Service {chr(65 + i)}{chr(75 + i)}" in names for i in range(10))


async def test_the_model_is_given_enough_output_budget_to_think_and_answer():
    from app.services import industry_services as module

    llm = AsyncMock(return_value=MODEL_REPLY)
    with (
        patch("app.services.industry_services.synthesize_json", new=llm),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        await generate_industry_taxonomy("Plumbing")
    assert llm.await_args.kwargs["max_tokens"] == module.LLM_MAX_TOKENS
    assert module.LLM_MAX_TOKENS >= 10000


# --- normalising what the model returns --------------------------------------


def test_normalize_accepts_a_bare_list_and_plain_string_subservices():
    raw = [{"name": "Email Marketing", "subservices": ["Automation", {"name": "Newsletters"}]}]
    out = normalize_taxonomy(raw)
    assert out == normalize_taxonomy({"services": raw})
    assert out == [
        {
            "name": "Email Marketing",
            "covers": [],
            "subservices": [
                {"name": "Automation", "covers": []},
                {"name": "Newsletters", "covers": []},
            ],
        }
    ]


def test_normalize_only_keeps_covers_that_were_really_offered():
    raw = {
        "services": [
            {
                "name": "SEO",
                "covers": ["seo", "Invented Offering"],
                "subservices": [{"name": "Local SEO", "covers": ["local  seo", "Another Invention"]}],
            }
        ]
    }
    out = normalize_taxonomy(raw, OFFERINGS)
    assert out[0]["covers"] == ["SEO"]
    assert out[0]["subservices"][0]["covers"] == ["Local SEO"]


def test_normalize_drops_junk_and_merges_services_that_are_one_offering():
    out = normalize_taxonomy(
        {
            "services": [
                {"name": "  Email   Marketing ", "subservices": ["Automation", "automation", "", "  "]},
                {"name": "email marketing services", "subservices": ["Newsletters"]},
                {"name": ""},
                {"name": "x"},
                "junk",
                None,
                {"name": "21/09/2026 (draft)", "subservices": ["Nope"]},
                {"name": "Web Design", "subservices": "not a list"},
            ]
        }
    )
    assert [s["name"] for s in out] == ["Email Marketing", "Web Design"]
    assert [s["name"] for s in out[0]["subservices"]] == ["Automation", "Newsletters"]
    assert out[1]["subservices"] == []


def test_normalize_caps_primary_services_and_subservices():
    many = {
        "services": [
            {"name": f"Service {chr(65 + i)}{chr(75 + i)}", "subservices": [f"Sub {n} {i}" for n in range(20)]}
            for i in range(20)
        ]
    }
    out = normalize_taxonomy(many)
    assert len(out) == MAX_SERVICES
    assert all(len(s["subservices"]) == MAX_SUBSERVICES for s in out)
    assert normalize_taxonomy({"services": [{"name": "a" * 120}]}) == []


def test_normalize_garbage_in_is_empty_out():
    assert normalize_taxonomy(None) == []
    assert normalize_taxonomy("text") == []
    assert normalize_taxonomy({"services": "text"}) == []


# --- building the catalog: primary services and sub-services only -------------


def test_catalog_is_just_the_primary_services_with_their_subservices():
    catalog, unplaced = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, OFFERINGS)
    assert [s["name"] for s in catalog] == [
        "SEO",
        "PPC Management",
        "Social Media Marketing",
        "Content Marketing",
    ]
    by_name = {s["name"]: s for s in catalog}
    assert [s["name"] for s in by_name["Social Media Marketing"]["subservices"]] == [
        "Meta Ads",
        "Facebook Ads",
        "Instagram Ads",
    ]
    assert [s["name"] for s in by_name["PPC Management"]["subservices"]] == [
        "Google Ads Management",
        "Bing Ads Management",
    ]
    assert unplaced == []
    # the page candidate and every other site page stay out
    assert "Borrow" not in {s["name"] for s in catalog}


def test_client_offerings_are_marked_as_the_clients_and_keep_their_urls():
    catalog, _ = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, OFFERINGS)
    by_name = {s["name"]: s for s in catalog}
    seo = by_name["SEO"]
    assert seo["selected"] is True and seo["candidate_new_service"] is False
    assert seo["source"] == "website" and seo["path"] == "/seo"
    local = next(s for s in seo["subservices"] if s["name"] == "Local SEO")
    assert local["selected"] is True and local["source"] == "website"
    assert local["path"] == "/seo/local-seo"
    technical = next(s for s in seo["subservices"] if s["name"] == "Technical SEO")
    assert technical["selected"] is False and technical["source"] == "industry"
    # the brief-only offering is the client's too, but has no page
    ppc = by_name["PPC Management"]
    assert ppc["selected"] is True and ppc["source"] == "cdd" and "path" not in ppc
    # a primary service the client has nothing under is an unticked suggestion
    content = by_name["Content Marketing"]
    assert content["selected"] is False and content["candidate_new_service"] is True
    assert content["source"] == "industry" and content["priority_tier"] is None


def test_defaults_select_only_what_the_client_has():
    catalog, _ = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, OFFERINGS)
    result = default_selection(catalog)
    assert result["selected_service_ids"] == ["seo", "ppc management", "social media marketing"]
    assert result["selected_subservice_ids"] == ["local seo", "google ads management", "meta ads"]
    smm = result["service_scope"]["social media marketing"]
    assert [o["name"] for o in smm["offered"]] == ["Meta Ads"]
    assert {"Facebook Ads", "Instagram Ads"} <= {e["name"] for e in smm["excluded"]}


def test_offerings_the_model_could_not_place_are_reported():
    offerings = [*OFFERINGS, "Bridging Finance"]
    _, unplaced = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, offerings)
    assert unplaced == ["Bridging Finance"]


def test_a_subservice_under_a_covered_service_counts_as_placed():
    site = [
        {
            "id": "ppc",
            "name": "PPC",
            "source": "website",
            "subservices": [{"id": "bing", "name": "Bing Ads", "source": "website"}],
        }
    ]
    taxonomy = [{"name": "PPC Management", "covers": ["PPC"], "subservices": []}]
    _, unplaced = build_catalog_from_taxonomy(taxonomy, site, ["PPC", "Bing Ads"])
    assert unplaced == []


def test_if_the_model_ignored_the_offerings_the_clients_services_are_kept():
    ignored = [{"name": "Content Marketing", "covers": [], "subservices": []}]
    catalog, unplaced = build_catalog_from_taxonomy(ignored, SITE_CATALOG, OFFERINGS)
    names = [s["name"] for s in catalog]
    assert "SEO" in names and "Content Marketing" in names
    assert "Borrow" not in names
    assert unplaced == []


def test_default_scope_blocks_the_suggested_subservices():
    catalog, _ = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, OFFERINGS)
    entry = build_default_scope(catalog, ["seo"])["seo"]
    assert [o["name"] for o in entry["offered"]] == ["Local SEO"]
    assert {"Technical SEO", "Link Building"} <= {e["name"] for e in entry["excluded"]}


# --- the pack -----------------------------------------------------------------


def _pack(**extra):
    return build_prioritization_pack(
        client_name="Acme",
        services=["SEO", "Google Ads/PPC Management"],
        website={
            "page_hierarchy": [
                {"title": "/seo", "path": "/seo", "url": "https://a.com/seo", "page_type": "service", "cluster": "services"},
                {"title": "/borrow", "path": "/borrow", "url": "https://a.com/borrow", "page_type": "page", "cluster": "other"},
            ]
        },
        page_seeds=[{"term": "SEO", "page_type": "service", "path": "/seo"}],
        competitors=[],
        existing={},
        **extra,
    )


def test_pack_with_a_taxonomy_shows_only_primary_services_even_with_competitor_noise():
    competitor_tree = {
        "name": "Rival",
        "competitor_id": "rival",
        "services": [{"id": "tiktok marketing", "name": "TikTok Marketing", "subservices": []}],
    }
    pack = _pack(competitor_trees=[competitor_tree], industry_taxonomy=TAXONOMY)
    names = [s["name"] for s in pack["service_catalog"]]
    assert names == ["SEO", "PPC Management", "Social Media Marketing", "Content Marketing"]
    assert "TikTok Marketing" not in names and "Borrow" not in names
    assert pack["industry_service_count"] == 4
    assert pack["defaults"]["selected_service_ids"][:2] == ["seo", "ppc management"]


def test_pack_without_a_taxonomy_still_falls_back_to_the_site_derived_list():
    pack = _pack(competitor_trees=[])
    assert [s["name"] for s in pack["service_catalog"]][:1] == ["SEO"]
    assert pack["industry_service_count"] == 0
    assert pack["unplaced_offerings"] == []


def test_adopting_an_industry_service_records_the_source():
    pack = _pack(competitor_trees=[], industry_taxonomy=TAXONOMY)
    out = confirm_prioritization(
        {
            "selected_service_ids": ["content marketing"],
            "adopt_competitor_service_ids": ["content marketing"],
            "service_catalog": pack["service_catalog"],
        }
    )
    row = next(s for s in out["service_catalog"] if s["id"] == "content marketing")
    assert row["source"] == "adopted"
    assert row["adopted_from"] == "industry"


# --- keeping the operator's picks when the catalog is rebuilt -----------------


def test_rebuilt_catalog_keeps_selected_primary_services_and_their_subservices():
    catalog, _ = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, OFFERINGS)
    rebuilt = [dict(SITE_CATALOG[0])]
    out = merge_saved_catalog(rebuilt, catalog, selected_service_ids=["seo", "content marketing"])
    assert [s["name"] for s in out] == ["SEO", "Content Marketing"]
    assert {"Local SEO", "Technical SEO", "Link Building"} <= {s["name"] for s in out[0]["subservices"]}


def test_rebuilt_catalog_does_not_resurrect_unselected_suggestions():
    catalog, _ = build_catalog_from_taxonomy(TAXONOMY, SITE_CATALOG, OFFERINGS)
    out = merge_saved_catalog([dict(SITE_CATALOG[0])], catalog, selected_service_ids=["seo"])
    assert [s["name"] for s in out] == ["SEO"]


def test_merge_saved_catalog_with_nothing_saved_is_a_noop():
    rebuilt = [dict(SITE_CATALOG[0])]
    assert merge_saved_catalog(rebuilt, None, selected_service_ids=["seo"]) == rebuilt


# --- the LLM call --------------------------------------------------------------


MODEL_REPLY = {
    "services": [
        {
            "name": "SEO",
            "covers": ["SEO", "Made Up Offering"],
            "subservices": [{"name": "Local SEO", "covers": ["Local SEO"]}, {"name": "Technical SEO", "covers": []}],
        },
        {"name": "Social Media Marketing", "covers": ["Meta Ads"], "subservices": ["Meta Ads", "Facebook Ads"]},
    ]
}


async def test_generate_tells_the_model_to_use_primary_services_and_lists_the_clients_offerings():
    llm = AsyncMock(return_value=MODEL_REPLY)
    with (
        patch("app.services.industry_services.synthesize_json", new=llm),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        out = await generate_industry_taxonomy(
            "Digital marketing agency", existing_services=OFFERINGS, geography="Melbourne, Australia"
        )
    llm.assert_awaited_once()
    system, user = llm.await_args.args[:2]
    assert "PRIMARY" in system and "sub-services" in system
    assert "Digital marketing agency" in user and "Melbourne" in user
    for name in OFFERINGS:
        assert f"- {name}" in user
    # grounded: the invented cover is dropped, the real ones survive
    assert out[0]["covers"] == ["SEO"]
    assert out[0]["subservices"][0]["covers"] == ["Local SEO"]
    assert out[1]["covers"] == ["Meta Ads"]


async def test_generate_uses_the_cache_and_skips_the_model():
    llm = AsyncMock(return_value=MODEL_REPLY)
    cached = [{"name": "Cached Service", "covers": [], "subservices": [{"name": "Audit", "covers": []}]}]
    with (
        patch("app.services.industry_services.synthesize_json", new=llm),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=cached)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        out = await generate_industry_taxonomy("Plumbing")
    assert out == cached
    llm.assert_not_awaited()


async def test_generate_stores_the_result_in_the_cache():
    setter = AsyncMock()
    with (
        patch("app.services.industry_services.synthesize_json", new=AsyncMock(return_value=MODEL_REPLY)),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=setter),
    ):
        await generate_industry_taxonomy("Plumbing")
    setter.assert_awaited_once()
    assert setter.await_args.args[1][0]["name"] == "SEO"


async def test_cache_is_per_set_of_offerings():
    keys = []

    async def getter(key):
        keys.append(key)
        return None

    with (
        patch("app.services.industry_services.synthesize_json", new=AsyncMock(return_value=MODEL_REPLY)),
        patch("app.services.industry_services.cache_get", new=getter),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        await generate_industry_taxonomy("Plumbing", existing_services=["Drain Cleaning"])
        await generate_industry_taxonomy("Plumbing", existing_services=["Drain Cleaning", "Hot Water"])
        await generate_industry_taxonomy("plumbing", existing_services=["drain cleaning"])
    assert keys[0] != keys[1]
    assert keys[0] == keys[2]  # same industry and offerings, case aside


@pytest.mark.parametrize("industry", ["", "   ", None])
async def test_generate_without_an_industry_never_calls_the_model(industry):
    llm = AsyncMock(return_value=MODEL_REPLY)
    with patch("app.services.industry_services.synthesize_json", new=llm):
        assert await generate_industry_taxonomy(industry) == []
    llm.assert_not_awaited()


async def test_generate_survives_model_failure_and_unusable_replies_without_caching():
    setter = AsyncMock()
    for reply in (None, {"services": []}, {"unexpected": True}):
        with (
            patch("app.services.industry_services.synthesize_json", new=AsyncMock(return_value=reply)),
            patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
            patch("app.services.industry_services.cache_set", new=setter),
        ):
            assert await generate_industry_taxonomy("Plumbing") == []
    setter.assert_not_awaited()
    with (
        patch("app.services.industry_services.synthesize_json", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=setter),
    ):
        assert await generate_industry_taxonomy("Plumbing") == []


async def test_taxonomy_for_client_times_out_to_empty_instead_of_blocking_the_card():
    import asyncio

    async def slow(*_a, **_k):
        await asyncio.sleep(5)
        return MODEL_REPLY

    with (
        patch("app.services.industry_services.synthesize_json", new=slow),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        out = await taxonomy_for_client("Plumbing", existing_services=[], timeout=0.05)
    assert out == []


# --- saying why the list is the old one ----------------------------------------


from app.services.industry_services import taxonomy_with_status  # noqa: E402


async def test_status_ok_when_the_model_answers():
    with (
        patch("app.services.industry_services.synthesize_json", new=AsyncMock(return_value=MODEL_REPLY)),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        taxonomy, status = await taxonomy_with_status("Plumbing")
    assert status == "ok" and taxonomy


async def test_status_no_industry_without_calling_the_model():
    llm = AsyncMock(return_value=MODEL_REPLY)
    with patch("app.services.industry_services.synthesize_json", new=llm):
        assert await taxonomy_with_status("  ") == ([], "no_industry")
    llm.assert_not_awaited()


async def test_status_timeout_when_the_model_is_too_slow():
    import asyncio

    async def slow(*_a, **_k):
        await asyncio.sleep(5)

    with (
        patch("app.services.industry_services.synthesize_json", new=slow),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        assert await taxonomy_with_status("Plumbing", timeout=0.05) == ([], "timeout")


async def test_status_failed_when_the_model_gives_nothing_usable():
    with (
        patch("app.services.industry_services.synthesize_json", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_get", new=AsyncMock(return_value=None)),
        patch("app.services.industry_services.cache_set", new=AsyncMock()),
    ):
        assert await taxonomy_with_status("Plumbing") == ([], "failed")


def test_the_prompt_tells_the_model_not_to_trust_a_vague_industry_label():
    from app.services.industry_services import SYSTEM_PROMPT

    assert "vague or wrong" in SYSTEM_PROMPT
    assert "offerings" in SYSTEM_PROMPT


def test_pack_reports_the_lookup_status():
    pack = build_prioritization_pack(
        client_name="Acme", services=[], website={}, page_seeds=[], competitors=[],
        competitor_trees=[], existing={}, industry_taxonomy=[], industry_status="timeout",
    )
    assert pack["industry_taxonomy_status"] == "timeout"
    ok = build_prioritization_pack(
        client_name="Acme", services=[], website={}, page_seeds=[], competitors=[],
        competitor_trees=[], existing={}, industry_taxonomy=TAXONOMY,
    )
    assert ok["industry_taxonomy_status"] == "ok"
