"""Skill contract loader for Phase 1–4 agents."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parent

# Map agent_key → skill directory name
AGENT_SKILL_DIRS = {
    "discovery_agent": "discovery-agent",
    "tracking_access_agent": "tracking-access-agent",
    "website_situation_agent": "website-situation-agent",
    "competitor_market_agent": "ads-category-competitors",
    "broken_link_checker": "broken-link-checker",
    "on_page_seo": "on-page-seo",
    "technical_seo_audit": "technical-seo-audit",
    "seo_audit": "seo-audit",
}


@lru_cache(maxsize=16)
def load_skill(agent_key: str) -> str:
    """Return SKILL.md body for an agent_key, or empty string if missing."""
    dirname = AGENT_SKILL_DIRS.get(agent_key)
    if not dirname:
        return ""
    path = SKILLS_ROOT / dirname / "SKILL.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_skill_file(dirname: str) -> str:
    """Load a skill by directory name (sub-skills)."""
    path = SKILLS_ROOT / dirname / "SKILL.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def skill_system_preamble(agent_key: str, *, max_chars: int | None = None) -> str:
    """Preamble agents prepend when calling an LLM. Competitor skill keeps fuller text."""
    body = load_skill(agent_key)
    if not body:
        return f"You are the Radius OS {agent_key}."
    if max_chars is None:
        # Competitor skill is long — keep enough for scoring framework + rules
        max_chars = 24000 if agent_key == "competitor_market_agent" else 6000
    return body[:max_chars]
