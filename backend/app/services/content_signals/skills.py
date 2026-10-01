"""Signal 5 — skills-based generation.

Reuses the repo's existing convention: a skill is a markdown file on disk, not a
hardcoded string. The 26 phase skills live in ``app/agents/<name>/SKILL.md``;
content-type skills live in ``app/agents/content_skills/<name>.md`` so new ones
can be dropped in without a code change.

The skill is chosen from the keyword's resolved page type (``resolve_page_type``),
with an operator override. An unknown page type falls back to the generic
create-content skill and says so — it never silently writes without a contract.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.services.content_signals.base import SignalResult, unavailable

SKILLS_ROOT = Path(__file__).resolve().parents[2] / "agents" / "content_skills"

# page_type (from resolve_page_type) → content-skill file stem.
SKILL_FOR_PAGE_TYPE: dict[str, str] = {
    "service": "service-page",
    "sub_service": "service-page",
    "hub": "service-page",
    "landing": "local-landing-page",
    "location": "local-landing-page",
    "product": "product-description",
    "guide": "how-to-guide",
    "blog": "seo-blog-post",
    "article": "seo-blog-post",
    "listicle": "seo-blog-post",
    "spoke": "seo-blog-post",
    "comparison": "comparison-post",
    "faq": "faq-page",
    "email": "email",
}
# Claude SEO competitor-pages skill. Appended beside comparison-post; it does not replace it.
UPSTREAM_COMPARISON_SKILL = "competitor-pages"
DEFAULT_SKILL = "seo-blog-post"


def available_skills() -> list[str]:
    """Skill stems present on disk, so the UI can list what can be picked."""
    if not SKILLS_ROOT.is_dir():
        return []
    return sorted(p.stem for p in SKILLS_ROOT.glob("*.md"))


@lru_cache(maxsize=64)
def load_content_skill(stem: str) -> str:
    path = SKILLS_ROOT / f"{stem}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def skill_for(page_type: str | None, *, override: str | None = None) -> tuple[str, str]:
    """(skill_stem, why). Override wins; unknown page types fall back."""
    if override:
        stem = str(override).strip().lower()
        if load_content_skill(stem):
            return stem, "operator override"
    key = str(page_type or "").strip().lower()
    stem = SKILL_FOR_PAGE_TYPE.get(key)
    if stem and load_content_skill(stem):
        return stem, f"selected from page type {key!r}"
    return DEFAULT_SKILL, (
        f"no skill mapped for page type {key or 'unknown'!r} — using {DEFAULT_SKILL}"
    )


def collect_skill(
    *,
    page_type: str | None = None,
    override: str | None = None,
    weight: str = "high",
) -> SignalResult:
    stem, why = skill_for(page_type, override=override)
    body = load_content_skill(stem)
    if stem == "comparison-post":
        upstream = load_content_skill(UPSTREAM_COMPARISON_SKILL)
        if upstream:
            body = f"{body.rstrip()}\n\n{upstream}" if body else upstream
    if not body:
        return unavailable(
            "skill",
            f"No content skill file found for {stem!r} in {SKILLS_ROOT.name}/.",
            weight=weight,
        )
    return SignalResult(
        name="skill",
        available=True,
        weight=weight,
        data={
            "skill": stem,
            "reason": why,
            "instructions": body,
            "available_skills": available_skills(),
        },
        notes=[f"Skill: {stem} ({why})."],
    )
