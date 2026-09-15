"""Prompt contract loader for Phase 1–12 agents.

Every agent's SKILL.md lives as a sibling directory right here in app/agents/ (e.g.
app/agents/discovery-agent/SKILL.md next to the orchestration code in
app/agents/discovery.py) — one folder holds both the agent that runs and the prompt it
runs with, rather than splitting them across two top-level packages.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPTS_ROOT = Path(__file__).resolve().parent

# Map agent_key → prompt directory name
AGENT_SKILL_DIRS = {
    "discovery_agent": "discovery-agent",
    "tracking_access_agent": "tracking-access-agent",
    "website_situation_agent": "website-situation-agent",
    "competitor_market_agent": "ads-category-competitors",
    "broken_link_checker": "broken-link-checker",
    "broken_links": "broken-link-checker",
    "rendering_audit": "rendering-audit",
    "cwv_measurement": "cwv-measurement",
    "hreflang_validator": "hreflang-validator",
    "on_page_seo": "on-page-seo",
    "technical_seo": "technical-seo-audit",
    "technical_seo_audit": "technical-seo-audit",
    "seo_audit": "seo-audit",
    "search_demand": "search-demand",
    "content_strategy": "content-strategy",
    "seo_strategy": "content-strategy",
    "site_architecture": "site-architecture",
    "create_topic": "create-topic",
    "keyword_clustering": "keyword-clustering",
    "content_audit": "existing-content-audit",
    "content_planning": "content-planning",
    "content_production": "content-brief",
    "content_brief": "content-brief",
    "create_content": "create-content",
    "schema_markup": "schema-markup",
    # generate_schema is a declared P11 skill in role_skills with no directory of its
    # own; without this entry load_skill() silently returns "" and the agent runs with
    # no schema contract at all.
    "generate_schema": "schema-markup",
    "internal_linking": "internal-linking",
    "publishing": "platform-publish",
    "platform_publish": "platform-publish",
    "firecrawl": "firecrawl",
    "site_page_inventory": "site-page-inventory",
}


@lru_cache(maxsize=32)
def load_skill(agent_key: str) -> str:
    """Return SKILL.md body for an agent_key, or empty string if missing."""
    dirname = AGENT_SKILL_DIRS.get(agent_key)
    if not dirname:
        return ""
    path = PROMPTS_ROOT / dirname / "SKILL.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_skill_file(dirname: str) -> str:
    """Load a skill by directory name (sub-skills)."""
    path = PROMPTS_ROOT / dirname / "SKILL.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_shared_reference(name: str = "google-helpful-content.md", *, max_chars: int | None = None) -> str:
    """Load a file from agents/references/ (canonical shared evidence)."""
    path = PROMPTS_ROOT / "references" / name
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if max_chars is not None:
        return text[:max_chars]
    return text


def skill_system_preamble(agent_key: str, *, max_chars: int | None = None) -> str:
    """Preamble agents prepend when calling an LLM. Competitor skill keeps fuller text."""
    body = load_skill(agent_key)
    if not body:
        return f"You are the Radius OS {agent_key}."
    if max_chars is None:
        max_chars = (
            24000
            if agent_key
            in (
                "competitor_market_agent",
                "search_demand",
                "content_strategy",
                "site_architecture",
                "technical_seo",
                "content_audit",
                "content_planning",
                "content_production",
                "on_page_seo",
                "publishing",
            )
            else 6000
        )
    return body[:max_chars]
