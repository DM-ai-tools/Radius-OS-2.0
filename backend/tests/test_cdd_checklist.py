"""Pure-Python CDD checklist — no LLM."""

from app.services.cdd_checklist import (
    analyse_cdd,
    is_filled,
    normalize_cdd,
    parse_cdd_xlsx,
)


def test_is_filled_rejects_placeholders():
    assert not is_filled(None)
    assert not is_filled("")
    assert not is_filled("TBD")
    assert not is_filled("to confirm")
    assert not is_filled({"value": ""})
    assert is_filled("SEO agency")
    assert is_filled({"value": "Melbourne"})
    assert is_filled("pending", access_field=True)
    assert not is_filled("pending", access_field=False)


def test_normalize_nested_commercial_scope():
    fields = normalize_cdd(
        {
            "commercial_scope": {
                "inferred_industry": "Digital marketing",
                "business_keywords": "seo, ppc",
                "geographic_focus": "Melbourne",
            },
            "competitors": "A\nB\nC",
        }
    )
    assert fields["inferred_industry"] == "Digital marketing"
    assert fields["geographic_focus"] == "Melbourne"
    assert fields["competitors"] == "A\nB\nC"


def test_analyse_blocked_on_missing_p0():
    report = analyse_cdd({"inferred_industry": "SEO"})
    assert report["verdict"] == "blocked"
    p0 = next(t for t in report["tiers"] if t["tier"] == "P0")
    assert "business_keywords" in p0["missing"]
    assert "search_demand" in report["blocked_pipeline_phases"]


def test_analyse_ready_when_p0_and_p1_filled():
    cdd = {
        "inferred_industry": "Digital marketing",
        "business_keywords": "local seo, google ads",
        "products": "SEO, PPC, Web design",
        "products_for_promotion": "Local SEO",
        "geographic_focus": "Melbourne",
        "competitors": "Rival A\nRival B\nRival C",
        "b2b_b2c": "B2B",
        "business_model": "Agency",
        "positioning": "Hands-on technical SEO",
        "target_demographic": "SMB owners",
        "business_goal": "More qualified leads",
        "objectives": ["Generate more qualified leads", "Increase organic traffic"],
        "brand_guidelines": "No medical claims",
    }
    # Fill remaining DISCOVERY_FIELDS lightly so score can pass
    for key in (
        "average_ticket_size",
        "lifetime_value",
        "lead_modes",
        "strengths",
        "weaknesses",
        "analytics_access",
        "search_console_access",
        "gtm_access",
        "google_ads_access",
        "merchant_center_access",
        "cms_access",
        "hosting_access",
        "access_level",
        "sales_promises",
    ):
        cdd[key] = "provided"
    report = analyse_cdd(cdd)
    assert report["verdict"] in ("ready", "partial")
    assert report["priorities"]["promote_first"][0] == "Local SEO"
    assert report["priorities"]["objectives_ranked"][0] == "Generate more qualified leads"
    assert len(report["sub_phases"]) == 5
    assert report["sub_phases"][0]["id"] == "D1"
    assert sum(sp["load_weight"] for sp in report["sub_phases"]) == 100


def test_competitors_need_three():
    report = analyse_cdd(
        {
            "inferred_industry": "x",
            "business_keywords": "y",
            "products": "z",
            "products_for_promotion": "z",
            "geographic_focus": "AU",
            "competitors": "Only One",
            "b2b_b2c": "B2C",
        }
    )
    assert any("at least 3" in i for i in report["issues"])


def test_parse_xlsx_roundtrip(tmp_path):
    openpyxl = pytest_import_openpyxl()
    path = tmp_path / "cdd.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Industry", "SEO Agency"])
    ws.append(["Business Keywords", "local seo, ppc"])
    ws.append(["Targets Geographic Location", "Melbourne"])
    ws.append(["Competitors [3 Minimum]", "A\nB\nC"])
    ws.append(["GTM? (pending)", ""])
    wb.save(path)
    fields = parse_cdd_xlsx(path)
    assert fields["inferred_industry"] == "SEO Agency"
    assert fields["geographic_focus"] == "Melbourne"
    assert fields["gtm_access"] == "pending"


def pytest_import_openpyxl():
    import openpyxl

    return openpyxl
