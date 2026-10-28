"""Removed report sections stay removed; automatic sections are labelled and logged;
clustering can be switched off; the report says which clustering engine ran and why."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import patch

import docx
import pytest

from app.services import report_pdf_document as doc_mod
from app.services.report_docx import build_reports_docx
from app.services.report_pdf import build_reports_pdf
from app.services.report_pdf_document import (
    AUTO_SECTION_LABEL,
    REPORT_BLUEPRINTS,
    REPORT_SUPPRESSED_KEYS,
    auto_section_specs,
    suppressed_keys,
)

CLUSTER_ROWS = [
    {"name": "Local SEO", "primary_keyword": "local seo", "keyword_count": 3, "volume": 720},
    {"name": "SEO Agency", "primary_keyword": "seo agency", "keyword_count": 5, "volume": 4400},
]


def _search_demand_payload(**extra):
    return {
        "card_type": "search_demand_report",
        "keyword_count": 10,
        "clusters": CLUSTER_ROWS,
        "opportunities": [{"keyword": "seo agency melbourne", "volume": 1300}],
        **extra,
    }


def _bundle(payload):
    return {
        "client": {"name": "Acme"},
        "exported_at": "2026-10-27T10:00:00+00:00",
        "phase_statuses": {},
        "reports": [{"card_type": payload["card_type"], "payload": payload}],
    }


def _docx_headings(data: bytes) -> list[str]:
    d = docx.Document(io.BytesIO(data))
    return [p.text.strip() for p in d.paragraphs if p.text.strip()[:1].isdigit()]


# --------------------------------------------------------------------- suppression


def test_clusters_are_suppressed_for_search_demand_and_the_reason_is_recorded():
    assert "clusters" in suppressed_keys("search_demand_report")
    assert REPORT_SUPPRESSED_KEYS["search_demand_report"]["clusters"]
    blueprint = REPORT_BLUEPRINTS["search_demand_report"]
    specs = auto_section_specs("search_demand_report", _search_demand_payload(), blueprint)
    assert not any(s.table_source == "clusters" for s in specs)


def test_a_removed_section_does_not_come_back_in_either_export():
    data = _bundle(_search_demand_payload())
    pdf_text = build_reports_pdf(data, single=True)
    assert b"Automatic section" not in pdf_text
    headings = _docx_headings(build_reports_docx(data, single=True))
    assert not any("Clusters" == h.split("   ", 1)[-1] for h in headings)
    assert not any(AUTO_SECTION_LABEL in h for h in headings)


def test_suppression_does_not_delete_the_payload_key():
    payload = _search_demand_payload()
    build_reports_pdf(_bundle(payload), single=True)
    assert payload["clusters"] == CLUSTER_ROWS


def test_every_suppressed_key_names_a_real_report_and_a_reason():
    for card_type, keys in REPORT_SUPPRESSED_KEYS.items():
        # A fixed blueprint, a generic phase blueprint, or (phase validation) one
        # built from the payload; suppression applies to all three.
        known = card_type in REPORT_BLUEPRINTS or doc_mod._generic_phase_sections(card_type)
        assert known or card_type == "phase_validation_report", card_type
        assert all(isinstance(why, str) and why for why in keys.values()), card_type


# ------------------------------------------------------------- automatic sections


def test_genuinely_new_data_is_still_shown_labelled_in_both_exports_and_logged():
    payload = _search_demand_payload(
        brand_new_findings=[{"finding": "New thing", "detail": "Shown automatically"}]
    )
    blueprint = REPORT_BLUEPRINTS["search_demand_report"]
    specs = auto_section_specs("search_demand_report", payload, blueprint)
    assert [s.table_source for s in specs] == ["brand_new_findings"]
    assert specs[0].title == f"{AUTO_SECTION_LABEL}: Brand New Findings"
    assert specs[0].intro

    with patch.object(doc_mod.log, "warning") as warn:
        build_reports_pdf(_bundle(payload), single=True)
    warn.assert_any_call(
        "report_auto_sections_rendered", card_type="search_demand_report", keys=["brand_new_findings"]
    )
    headings = _docx_headings(build_reports_docx(_bundle(payload), single=True))
    assert any(f"{AUTO_SECTION_LABEL}: Brand New Findings" in h for h in headings)


def test_a_dotted_table_source_covers_its_top_level_key():
    blueprint = [doc_mod.SectionSpec(title="Map", table_source="url_map_report.final_url_map")]
    payload = {"url_map_report": {"summary": {"a": 1, "b": 2}, "x": 1, "y": 2}}
    assert auto_section_specs("anything", payload, blueprint) == []


# ------------------------------------------------------------------- clustering

ROWS = [
    {"keyword": "seo agency melbourne", "volume": 1300, "intent": "commercial", "seed": "SEO"},
    {"keyword": "seo company melbourne", "volume": 880, "intent": "commercial", "seed": "SEO"},
    {"keyword": "local seo melbourne", "volume": 720, "intent": "commercial", "seed": "SEO"},
    {"keyword": "seo services melbourne", "volume": 590, "intent": "commercial", "seed": "SEO"},
]


def _settings(**over):
    base = {"keyword_clustering_enabled": True, "keyword_clustering_use_model": False}
    base.update(over)
    return SimpleNamespace(**base)


async def test_switching_clustering_off_skips_it_entirely_and_leaves_no_clusters_key():
    from app.services import keyword_clustering as kc

    with (
        patch("app.config.get_settings", return_value=_settings(keyword_clustering_enabled=False)),
        patch.object(kc, "build_clusters_deterministic", side_effect=AssertionError("must not run")),
    ):
        report = await kc.run_keyword_clustering([dict(r) for r in ROWS])
    assert "clusters" not in report
    assert report["clustering"]["ran"] is False
    assert "KEYWORD_CLUSTERING_ENABLED" in report["clustering"]["reason"]
    assert report["total_keywords"] == len(ROWS)
    assert "keyword_pipeline" in report


async def test_rules_chosen_in_settings_are_recorded_as_a_choice_not_a_fallback():
    from app.services.keyword_clustering import run_keyword_clustering

    with patch("app.config.get_settings", return_value=_settings(keyword_clustering_use_model=False)):
        report = await run_keyword_clustering([dict(r) for r in ROWS])
    info = report["clustering"]
    assert info == {
        "ran": True,
        "engine": "rules",
        "requested_engine": "rules",
        "deadline_fallback": False,
        "reason": info["reason"],
    }
    assert "KEYWORD_CLUSTERING_USE_MODEL=false" in info["reason"]


async def test_a_deadline_forced_fallback_says_so_on_the_report():
    from app.services.keyword_clustering import run_keyword_clustering

    with (
        patch("app.config.get_settings", return_value=_settings(keyword_clustering_use_model=True)),
        patch(
            "app.services.keyword_llm_clustering.llm_cluster_keywords",
            side_effect=AssertionError("the model must not run past the deadline"),
        ),
    ):
        report = await run_keyword_clustering([dict(r) for r in ROWS], within_deadline=False)
    info = report["clustering"]
    assert info["engine"] == "rules" and info["requested_engine"] == "model"
    assert info["deadline_fallback"] is True
    provenance = report["clustering_provenance"]
    assert provenance["deadline_fallback"] is True
    assert any("ran out of time" in line for line in provenance["inferred"])


async def test_the_model_engine_is_recorded_when_it_runs():
    from app.services.keyword_clustering import run_keyword_clustering

    llm_reply = {
        "clusters": [
            {
                "name": "SEO Melbourne",
                "keywords": [{"keyword": r["keyword"], "role": "Secondary"} for r in ROWS],
            }
        ],
        "orphans": [],
        "llm_cluster_audit": {"model": "claude-test"},
    }
    with (
        patch("app.config.get_settings", return_value=_settings(keyword_clustering_use_model=True)),
        patch("app.services.keyword_llm_clustering.llm_cluster_keywords", return_value=llm_reply),
        patch(
            "app.services.keyword_serp_clustering.refine_clusters_with_serp",
            side_effect=lambda clusters, *a, **k: (clusters, {"skipped": True, "reason": "not_run"}),
        ),
        patch(
            "app.services.keyword_cluster_naming.name_clusters",
            side_effect=lambda clusters, **k: clusters,
        ),
    ):
        report = await run_keyword_clustering([dict(r) for r in ROWS])
    info = report["clustering"]
    assert info["engine"] == "model" and info["requested_engine"] == "model"
    assert info["deadline_fallback"] is False and info["reason"] is None


# ----------------------------------------------------------------------- settings


@pytest.mark.parametrize(
    "env,expected",
    [
        ({}, False),
        ({"KEYWORD_CLUSTER_USE_LLM": "true"}, True),
        ({"KEYWORD_CLUSTERING_USE_MODEL": "true"}, True),
        ({"KEYWORD_CLUSTER_USE_LLM": "true", "KEYWORD_CLUSTERING_USE_MODEL": "false"}, False),
    ],
)
def test_engine_setting_reads_the_new_name_and_falls_back_to_the_old_one(monkeypatch, env, expected):
    from app.config import Settings

    for name in ("KEYWORD_CLUSTER_USE_LLM", "KEYWORD_CLUSTERING_USE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert Settings(_env_file=None).keyword_clustering_use_model is expected
    assert Settings(_env_file=None).keyword_clustering_enabled is True
