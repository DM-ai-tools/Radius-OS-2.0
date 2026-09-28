"""Signal 6 — copywriting framework.

Frameworks are markdown files in ``app/agents/frameworks/`` so more can be added
without touching code, matching how skills already work.

``auto`` picks from the page type and funnel stage and records WHY, because the
operator needs to see the reasoning to disagree with it. The mapping encodes
ordinary copywriting practice: a decision-stage service page wants PAS or AIDA;
an awareness-stage explainer wants E-E-A-T depth or a PAA structure; a
comparison wants Skyscraper.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.services.content_signals.base import SignalResult, unavailable

FRAMEWORKS_ROOT = Path(__file__).resolve().parents[2] / "agents" / "frameworks"

FRAMEWORKS: dict[str, str] = {
    "aida": "Attention → Interest → Desire → Action",
    "pas": "Problem → Agitate → Solve",
    "bab": "Before → After → Bridge",
    "four_ps": "Promise → Picture → Proof → Push",
    "storybrand": "Hero → Problem → Guide → Plan → Action → Stakes",
    "skyscraper": "Cover the topic more completely than the best existing page",
    "eeat": "Experience, Expertise, Authoritativeness, Trust signalling",
    "paa": "People-Also-Ask / question-led structure",
}

# (page_type, funnel_stage) → framework, with a page_type-only fallback.
_BY_TYPE_AND_STAGE: dict[tuple[str, str], str] = {
    ("service", "BOFU"): "pas",
    ("service", "MOFU"): "aida",
    ("sub_service", "BOFU"): "pas",
    ("landing", "BOFU"): "aida",
    ("location", "BOFU"): "aida",
    ("product", "BOFU"): "four_ps",
    ("comparison", "MOFU"): "skyscraper",
    ("guide", "TOFU"): "paa",
    ("guide", "MOFU"): "bab",
    ("blog", "TOFU"): "eeat",
    ("blog", "MOFU"): "bab",
    ("listicle", "TOFU"): "skyscraper",
    ("faq", "TOFU"): "paa",
}
_BY_TYPE: dict[str, str] = {
    "service": "pas",
    "sub_service": "pas",
    "hub": "eeat",
    "landing": "aida",
    "location": "aida",
    "product": "four_ps",
    "comparison": "skyscraper",
    "guide": "paa",
    "blog": "eeat",
    "article": "eeat",
    "listicle": "skyscraper",
    "spoke": "eeat",
    "faq": "paa",
    "email": "aida",
}
DEFAULT_FRAMEWORK = "eeat"


def available_frameworks() -> list[str]:
    if not FRAMEWORKS_ROOT.is_dir():
        return sorted(FRAMEWORKS)
    on_disk = sorted(p.stem for p in FRAMEWORKS_ROOT.glob("*.md"))
    return on_disk or sorted(FRAMEWORKS)


@lru_cache(maxsize=32)
def load_framework(name: str) -> str:
    path = FRAMEWORKS_ROOT / f"{name}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def pick_framework(
    *,
    page_type: str | None,
    funnel_stage: str | None,
    choice: str | None = None,
) -> tuple[str, str]:
    """(framework, why). `choice` of None/"auto" means decide automatically."""
    requested = str(choice or "auto").strip().lower()
    if requested and requested != "auto":
        if requested in FRAMEWORKS:
            return requested, "operator choice"
        return DEFAULT_FRAMEWORK, f"unknown framework {requested!r} — using {DEFAULT_FRAMEWORK}"

    ptype = str(page_type or "").strip().lower()
    stage = str(funnel_stage or "").strip().upper()

    hit = _BY_TYPE_AND_STAGE.get((ptype, stage))
    if hit:
        return hit, (
            f"auto: {FRAMEWORKS[hit]} suits a {stage} {ptype.replace('_', ' ')} — "
            f"{'lead with the reader\'s problem and close hard' if stage == 'BOFU' else 'build understanding before any ask'}"
        )
    hit = _BY_TYPE.get(ptype)
    if hit:
        return hit, f"auto: {FRAMEWORKS[hit]} is the default shape for a {ptype.replace('_', ' ')}"
    return DEFAULT_FRAMEWORK, (
        f"auto: no mapping for page type {ptype or 'unknown'!r}, defaulting to "
        f"{FRAMEWORKS[DEFAULT_FRAMEWORK]}"
    )


def collect_framework(
    *,
    page_type: str | None = None,
    funnel_stage: str | None = None,
    choice: str | None = None,
    weight: str = "high",
) -> SignalResult:
    name, why = pick_framework(page_type=page_type, funnel_stage=funnel_stage, choice=choice)
    body = load_framework(name)
    if not body and name not in FRAMEWORKS:
        return unavailable("framework", f"Unknown framework {name!r}.", weight=weight)
    return SignalResult(
        name="framework",
        available=True,
        weight=weight,
        data={
            "framework": name,
            "shape": FRAMEWORKS.get(name, ""),
            "reason": why,
            "instructions": body,
            "available_frameworks": available_frameworks(),
        },
        notes=[f"Framework: {name.upper()} — {FRAMEWORKS.get(name, '')}. {why}."],
    )
