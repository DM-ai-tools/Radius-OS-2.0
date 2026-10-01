"""Downloaded Claude SEO sitemap and competitor-pages skills load through the house loaders."""

from app.agents.prompts import PROMPTS_ROOT, load_skill, load_skill_file
from app.agents.technical_seo import _load_phase7_contracts
from app.services.content_signals.skills import collect_skill, load_content_skill
from app.services.technical_seo import PHASE7_SKILL_DIRS, phase7_skill_contracts

SITEMAP_URL = (
    "https://github.com/AgriciDaniel/claude-seo/blob/main/skills/seo-sitemap/SKILL.md"
)
COMPARISON_URL = (
    "https://github.com/AgriciDaniel/claude-seo/blob/main/skills/seo-competitor-pages/SKILL.md"
)


def test_sitemap_skill_file_is_what_the_loader_returns():
    path = PROMPTS_ROOT / "seo-sitemap" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert path.is_file()
    assert text.startswith(f"<!-- Source: {SITEMAP_URL} — MIT -->")
    assert "name: seo-sitemap" in text
    assert "# Sitemap Analysis & Generation" in text
    assert load_skill_file("seo-sitemap") == text
    assert load_skill("seo_sitemap") == text
    assert "seo-sitemap" in PHASE7_SKILL_DIRS
    assert _load_phase7_contracts()["seo-sitemap"] == len(text)
    assert phase7_skill_contracts()["seo-sitemap"] == len(text)


def test_comparison_page_type_loads_downloaded_skill_beside_house_contract():
    skill_path = PROMPTS_ROOT / "content_skills" / "competitor-pages.md"
    assert skill_path.is_file()
    text = skill_path.read_text(encoding="utf-8")
    assert text.startswith(f"<!-- Source: {COMPARISON_URL} — MIT -->")
    assert "name: seo-competitor-pages" in text
    assert "# Competitor Comparison & Alternatives Pages" in text
    assert load_content_skill("competitor-pages") == text

    loaded = collect_skill(page_type="comparison")
    instructions = loaded.data["instructions"]
    assert loaded.data["skill"] == "comparison-post"
    assert "Never invent pricing" in instructions
    assert text in instructions

    service = collect_skill(page_type="service")
    assert "name: seo-competitor-pages" not in service.data["instructions"]
