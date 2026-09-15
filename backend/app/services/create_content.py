"""Phase 10 create-content — one page from an approved brief (scaled-content-safe)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.config import get_settings
from app.logging_config import get_logger
from app.services.publish_preview import sanitize_meta_description
from app.integrations.llm import generate_openrouter_image, synthesize_json
from app.agents.prompts import load_shared_reference, load_skill_file

log = get_logger("create_content")

_WEAK_DIFF = re.compile(
    r"^(lead with|ranking pages typically|n/?a|none|tbd|todo)\b",
    re.I,
)
# Writer-instruction / outline notes must never ship as article body.
_INSTRUCTIONAL = re.compile(
    r"(cover this section|briefed outcome|expand this heading|for the writer|"
    r"this section should|write \d+ paragraphs|reach(?:es)? the briefed|"
    r"approved brief is missing|missing the required author|"
    r"please provide the named human|named human author or reviewer)",
    re.I,
)
_IMPERATIVE_OPEN = re.compile(
    r"^(cover|write|expand|include|add|ensure|make sure|this section)\b",
    re.I,
)


def _preflight(brief: dict[str, Any]) -> dict[str, Any]:
    pre = brief.get("preflight") if isinstance(brief.get("preflight"), dict) else {}
    return pre


def _differentiation(brief: dict[str, Any]) -> str:
    raw = brief.get("differentiation") or brief.get("differentiation_angle") or ""
    if isinstance(raw, list):
        raw = "; ".join(str(x) for x in raw if x)
    return str(raw or "").strip()


def _author(brief: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    pre = _preflight(brief)
    author = pre.get("author") or brief.get("author")
    info = brief.get("author_info")
    if not author and isinstance(info, dict):
        author = info.get("name")
    standing = pre.get("author_standing") or brief.get("author_standing")
    if not standing and isinstance(info, dict):
        standing = info.get("standing")
    ymyl = bool(pre.get("ymyl") or brief.get("ymyl"))
    name = str(author).strip() if author else None
    if name and name.lower() in ("none", "n/a", "tbd", "unknown", "—", "-"):
        name = None
    return name, (str(standing).strip() if standing else None), ymyl


def draft_gate(brief: dict[str, Any] | None) -> dict[str, Any]:
    """Refuse to write when the brief is missing what scaled-content abuse care about."""
    if not brief or not isinstance(brief, dict):
        return {
            "ok": False,
            "route_to": "content_brief",
            "reason": "No approved brief — run content-brief first (not a bare keyword).",
        }
    if brief.get("writer_ready") is False:
        blockers = (_preflight(brief).get("blockers") or []) + ["Brief is not writer_ready"]
        return {
            "ok": False,
            "route_to": "content_brief",
            "reason": "; ".join(str(b) for b in blockers if b) or "Brief not writer-ready.",
        }
    action = str(brief.get("action") or "create").lower()
    if action not in ("create", "refresh"):
        return {
            "ok": False,
            "route_to": "content_audit",
            "reason": f"Action {action} is not a write — follow content-audit disposition.",
        }
    diff = _differentiation(brief)
    if not diff or len(diff) < 24 or _WEAK_DIFF.search(diff):
        return {
            "ok": False,
            "route_to": "content_brief",
            "reason": (
                "Differentiation is empty or generic — this page would restate the SERP consensus. "
                "Refuse write; improve the brief angle or refresh an existing page."
            ),
        }
    author, standing, ymyl = _author(brief)
    if not author:
        return {
            "ok": False,
            "route_to": "content_brief",
            "reason": (
                "Named author missing. A draft without accountable standing is scaled output risk"
                + (" — especially YMYL." if ymyl else ".")
            ),
        }
    if ymyl and not standing:
        return {
            "ok": False,
            "route_to": "content_brief",
            "reason": "YMYL topic requires recorded author standing / credentials before drafting.",
        }
    pre = _preflight(brief)
    if not (brief.get("url") or pre.get("url") or brief.get("path")):
        return {
            "ok": False,
            "route_to": "site_architecture",
            "reason": "URL not assigned — get site-architecture before writing.",
        }
    if not (pre.get("parent") or brief.get("parent")):
        return {
            "ok": False,
            "route_to": "site_architecture",
            "reason": "Parent URL missing — get site-architecture before writing.",
        }
    return {"ok": True, "reason": None, "route_to": None}


def _outline_sections(brief: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in brief.get("outline") or []:
        if isinstance(item, str):
            title = item.split(":", 1)[-1].strip() if item.lower().startswith("h") else item
            out.append({"title": title, "notes": []})
        elif isinstance(item, dict):
            title = str(item.get("title") or item.get("heading") or "").strip()
            if title.upper().startswith("H1"):
                continue
            if re.match(r"^h\d\b", title, re.I):
                title = title.split(":", 1)[-1].strip()
            notes = item.get("notes") or []
            if isinstance(notes, str):
                notes = [notes]
            out.append({"title": title or "Section", "notes": [str(n) for n in notes if n]})
    return out


def _norm_topic(val: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(val or "").lower()).strip()


def _url_path(val: Any) -> str:
    raw = str(val or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return (urlparse(raw).path or "/").rstrip("/").lower()
    return raw.rstrip("/").lower() or "/"


def _kw_bits(raw: Any) -> list[str]:
    out: list[str] = []
    if isinstance(raw, dict):
        val = raw.get("keyword") or raw.get("primary_keyword") or raw.get("name") or raw.get("q")
        if val:
            out.append(str(val).strip())
        for nested in (raw.get("keywords"), raw.get("supporting_keywords"), raw.get("secondary_keywords")):
            out.extend(_kw_bits(nested))
        return out
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            out.extend(_kw_bits(item))
        return out
    text = str(raw or "").strip()
    return [text] if text else []


def related_keywords(
    brief: dict[str, Any],
    *,
    seo_strategy: dict[str, Any] | None = None,
    search_demand: dict[str, Any] | None = None,
    limit: int = 8,
) -> tuple[str, list[str]]:
    """Assigned keyword first; related cluster/brief terms after. Never invents terms."""
    primary = str(brief.get("keyword") or brief.get("primary_keyword") or "").strip()
    primary_n = _norm_topic(primary)
    seen: set[str] = {primary_n} if primary_n else set()
    related: list[str] = []

    def _add(raw: Any) -> None:
        for term in _kw_bits(raw):
            n = _norm_topic(term)
            if not n or n in seen or len(n) < 3:
                continue
            seen.add(n)
            related.append(term.strip())

    _add(brief.get("secondary_keywords"))
    _add(brief.get("supporting_keywords"))
    _add(brief.get("keywords"))
    for row in brief.get("keyword_placement") or []:
        if isinstance(row, dict) and _norm_topic(row.get("keyword")) != primary_n:
            _add(row.get("keyword"))

    packs = [dict(seo_strategy or {}), dict(search_demand or {})]
    clusters: list[dict[str, Any]] = []
    for pack in packs:
        for key in ("clusters", "core_topics", "pillars"):
            for row in pack.get(key) or []:
                if isinstance(row, dict):
                    clusters.append(row)
                    for child in row.get("clusters") or []:
                        if isinstance(child, dict):
                            clusters.append(child)
        report = pack.get("cluster_report")
        if isinstance(report, dict):
            for row in report.get("clusters") or []:
                if isinstance(row, dict):
                    clusters.append(row)

    for cluster in clusters:
        members = _kw_bits(cluster)
        member_n = {_norm_topic(m) for m in members}
        if primary_n and primary_n in member_n:
            for m in members:
                if _norm_topic(m) != primary_n:
                    _add(m)

    return primary, related[:limit]


_PAGE_ALIASES = {
    "service": "service",
    "services": "service",
    "commercial": "service",
    "product": "product",
    "landing": "landing",
    "comparison": "comparison",
    "listicle": "listicle",
    "list": "listicle",
    "hub": "hub",
    "pillar": "hub",
    "spoke": "spoke",
    "cluster": "spoke",
    "location": "location",
    "local": "location",
    "article": "article",
    "blog": "article",
    "post": "article",
    "guide": "guide",
    "tool": "tool",
    "home": "home",
    "utility": "utility",
}


def resolve_page_type(src: dict[str, Any] | None) -> str:
    """IA page_type wins over SERP 'article'. URL/intent fill gaps. Never invent a URL."""
    src = dict(src or {})
    pre = src.get("preflight") if isinstance(src.get("preflight"), dict) else {}
    url = str(src.get("url") or pre.get("url") or src.get("path") or src.get("suggested_url") or "").lower()
    intent = str(src.get("search_intent") or src.get("intent") or "").lower()
    kw = str(src.get("keyword") or src.get("title") or "").lower()

    def _alias(raw: Any) -> str | None:
        return _PAGE_ALIASES.get(str(raw or "").strip().lower())

    strong: list[str] = []
    weak: list[str] = []
    for raw in (
        pre.get("page_type"),
        src.get("page_type"),
        src.get("type"),
        src.get("content_type"),
    ):
        key = _alias(raw)
        if not key:
            continue
        (weak if key in ("article", "guide") else strong).append(key)
    if strong:
        return strong[0]

    if "/services/" in url or "/service/" in url:
        return "service"
    if "/pricing/" in url:
        return "landing"
    if "/compare/" in url or " vs " in kw or " versus " in kw:
        return "comparison"
    if "/locations/" in url or "/areas-we-service" in url:
        return "location"
    if kw.startswith("how to") or "/guides/" in url:
        return "guide"
    if "/blog/" in url:
        return "article"
    if intent == "transactional":
        return "landing"
    if intent == "commercial":
        return "service"
    if weak:
        return weak[0]
    return "article"


# Phase 5 (create_topic._ANGLE_CYCLE) assigns each topic one of these angles so a
# batch of "article" pages doesn't collapse into the same generic explainer. That
# signal used to reach the brief and stop there — the writer never read it, so a
# "pain-point" topic and a "trends" topic on the same page_type got an identical
# opening. This maps each angle to a concrete framing instruction for both the LLM
# prompt and the rule-based fallback.
ANGLE_GUIDANCE: dict[str, str] = {
    "pain-point": "Open by naming the reader's specific frustration with {kw} before offering the fix. Problem first, solution second — do not lead with a definition.",
    "how-to": "Skip throat-clearing and definitions. Deliver the method or steps for {kw} directly, in order, starting in the first paragraph.",
    "mistakes": "Frame the piece around what goes wrong with {kw} and why. Each section names one mistake and its fix — not a generic overview of the topic.",
    "what-why": "Answer what {kw} is and why it matters in plain terms before going deeper. This is the definitional entry point into the topic.",
    "why-failing": "Diagnose why {kw} isn't working for the reader first — symptoms, then likely causes — before the remedy.",
    "listicle": "Structure as a ranked or curated list of options/strategies for {kw}. Each H2 is one item with a clear recommendation, not a vague survey.",
    "comparison": "Give a verdict on {kw} options up front, then justify it with named criteria. Never end on 'it depends' without saying on what.",
    "never-again": "Frame around eliminating a recurring problem with {kw} for good — durability and prevention, not a one-time fix.",
    "case-study": "Ground the piece in a concrete, named scenario or result for {kw}. Do not invent statistics, clients, or outcomes not already in the brief.",
    "trends": "Frame around what's changing in {kw} right now and what the reader should do about it — forward-looking, not a static definition.",
    "beginner": "Assume zero prior knowledge of {kw}. Define terms as you introduce them; sequence from simplest to more advanced.",
    "advanced": "Assume the reader already knows the basics of {kw}. Skip definitions — go straight to nuance, edge cases, and depth.",
    "templates": "Deliver reusable frameworks, checklists, or templates for {kw} the reader can apply immediately — utility over narrative.",
}


def angle_guidance(angle: str | None, *, kw: str) -> str | None:
    """Concrete framing instruction for a Phase 5 angle, or None if unset/unrecognised."""
    key = str(angle or "").strip().lower()
    template = ANGLE_GUIDANCE.get(key)
    if not template:
        return None
    return template.format(kw=kw or "this topic")


# Rule-based fallback (used when the LLM path is unavailable) needs its own opening
# lead per angle — same intent as ANGLE_GUIDANCE, shaped as a first sentence rather
# than an instruction.
_ANGLE_OPENING_LEAD: dict[str, str] = {
    "pain-point": "If {kw} is frustrating you right now, you are not alone — and there is a fix.",
    "how-to": "Here is exactly how {kw} works, step by step.",
    "mistakes": "Most of what goes wrong with {kw} traces back to a handful of avoidable mistakes.",
    "what-why": "Here is a straight answer on {kw}: what it is, and why it matters.",
    "why-failing": "If {kw} isn't working the way it should, the cause is usually one of a few things.",
    "listicle": "Not every approach to {kw} is worth your time — here are the ones that are.",
    "never-again": "The goal here isn't a one-time fix for {kw} — it's making sure the problem doesn't come back.",
    "case-study": "Here is what actually happened when {kw} was put into practice.",
    "trends": "{kw} is changing — here is what's different and what to do about it.",
    "beginner": "New to {kw}? Here is what you need to know, from the start.",
    "advanced": "This goes past the basics of {kw} — straight into the detail that matters once the fundamentals are covered.",
    "templates": "Here are practical templates and frameworks for {kw} you can use right away.",
}


def page_playbook(page_type: str, *, keyword: str, client_name: str) -> dict[str, Any]:
    kw = keyword or "this topic"
    client = client_name or "the company"
    catalog: dict[str, dict[str, Any]] = {
        "service": {
            "job": "Convert a buyer researching this service into a qualified enquiry.",
            "not": "This is a service page on the company website — not a blog post and not an SEO explainer.",
            "voice": "Second person, commercial, specific. Name the client as the provider.",
            "length": "Opening 120–180 words. Each H2 180–320 words. CTA 80–120 words.",
            "outline": [
                {"title": f"Who {kw} is for", "notes": ["Buyer, situation, when this service is the right fit"]},
                {"title": "What is included", "notes": ["Deliverables, scope, what is not included"]},
                {"title": "How it works", "notes": ["Named steps from first call to handover"]},
                {"title": "What you can expect", "notes": ["Outcomes, timeline signals, how success is judged"]},
                {"title": f"Why {client}", "notes": ["Differentiation, standing, how this team works"]},
            ],
        },
        "landing": {
            "job": "Get a high-intent visitor to take one clear action.",
            "not": "Do not write a long educational article. Lead with the offer and the outcome.",
            "voice": "Punchy, scannable, benefit-led. Short paragraphs and concrete bullets.",
            "length": "Opening 80–140 words. Each H2 120–220 words. CTA 80–120 words.",
            "outline": [
                {"title": "What you get", "notes": ["Offer, outcome, who it is for"]},
                {"title": "How the process works", "notes": ["3–5 steps, time-to-start"]},
                {"title": "Is this right for you", "notes": ["Fit / not-a-fit, objections"]},
                {"title": f"Why {client}", "notes": ["Proof without invented case studies"]},
            ],
        },
        "comparison": {
            "job": "Help the reader pick an option using named criteria, then recommend a next step.",
            "not": "Do not write a vague 'it depends' essay. Give a verdict and a table-like comparison in prose.",
            "voice": "Fair, criteria-first, decisive. Name trade-offs.",
            "length": "Opening states the verdict. Each H2 150–280 words.",
            "outline": [
                {"title": "Quick verdict", "notes": ["Who should pick which option"]},
                {"title": "How to compare", "notes": ["Named criteria, not vibes"]},
                {"title": "Side-by-side", "notes": ["Option vs option on those criteria"]},
                {"title": "Which to choose", "notes": ["Recommendation by situation"]},
            ],
        },
        "guide": {
            "job": "Teach the reader how to do or evaluate this, with a worked path they can follow.",
            "not": "Do not pad with definitions. Lead with the method.",
            "voice": "Editorial, practical, second person. Examples over slogans.",
            "length": "Opening answers the query. Each H2 200–350 words.",
            "outline": [
                {"title": f"How {kw} actually works", "notes": ["Method, sequence, tools in play"]},
                {"title": "A practical walkthrough", "notes": ["Steps the reader can apply"]},
                {"title": "Mistakes that waste the work", "notes": ["What to skip, and why"]},
                {"title": "How to know it is working", "notes": ["Signals, not vanity metrics"]},
            ],
        },
        "hub": {
            "job": "Orient the reader across this topic and send them to the right child page.",
            "not": "Do not dump one thin article. This is a hub: map, then depth.",
            "voice": "Clear, navigational, authoritative.",
            "length": "Opening frames the map. Each H2 150–250 words.",
            "outline": [
                {"title": "The map", "notes": ["What lives under this hub and who each path is for"]},
                {"title": "Start here", "notes": ["First page to read or first service to consider"]},
                {"title": "Deeper pages", "notes": ["Child topics as links — do not duplicate them"]},
            ],
        },
        "spoke": {
            "job": "Cover one cluster in depth and point back to the hub.",
            "not": "Do not re-introduce the whole topic. Stay on this spoke.",
            "voice": "Focused, practical.",
            "length": "Opening 100–160 words. Each H2 180–300 words.",
            "outline": [
                {"title": f"What this part of {kw} covers", "notes": ["Scope of this spoke"]},
                {"title": "How to apply it", "notes": ["Practical steps"]},
                {"title": "How it connects", "notes": ["Parent hub / related spokes — link, do not copy"]},
            ],
        },
        "location": {
            "job": "Prove this service is available in this place and make it easy to enquire.",
            "not": "Do not write a city-name keyword dump.",
            "voice": "Local, concrete, commercial.",
            "length": "Opening names the place. Each H2 150–260 words.",
            "outline": [
                {"title": "Who we help here", "notes": ["Local situations, not generic national copy"]},
                {"title": "What we do in this area", "notes": ["Services actually offered"]},
                {"title": "How an engagement starts", "notes": ["First step for someone in this location"]},
            ],
        },
        "product": {
            "job": "Explain the product so a buyer can decide and enquire or buy.",
            "not": "Do not write a blog about the category.",
            "voice": "Product-led, specific features/outcomes, second person.",
            "length": "Opening 100–160 words. Each H2 160–280 words.",
            "outline": [
                {"title": "What it does", "notes": ["Capabilities, who it is for"]},
                {"title": "How you use it", "notes": ["Workflow"]},
                {"title": "What is included", "notes": ["Packaging, limits you can state without inventing prices"]},
            ],
        },
        "tool": {
            "job": (
                "Specify an interactive calculator, quiz, estimator, or template tool "
                "that helps the user reach a concrete result."
            ),
            "not": (
                "Do not pretend a prose article is the tool, invent formulas, or claim "
                "the interactive component has been implemented."
            ),
            "voice": (
                "Product-spec clarity with concise supporting copy. Separate confirmed "
                "requirements from implementation decisions."
            ),
            "length": (
                "Brief introduction, structured component specification, concise usage "
                "guidance, result interpretation, and implementation handoff."
            ),
            "outline": [
                {
                    "title": "Tool purpose and user outcome",
                    "notes": ["User problem, intended result, appropriate use"],
                },
                {
                    "title": "Interactive component specification",
                    "notes": [
                        "Embed placeholder",
                        "Component state and user flow",
                        "No invented implementation",
                    ],
                },
                {
                    "title": "Inputs and validation",
                    "notes": ["Required inputs, formats, validation, error states"],
                },
                {
                    "title": "Calculation or decision logic",
                    "notes": [
                        "Formula or branching logic requiring product-owner confirmation",
                        "Assumptions and limits",
                    ],
                },
                {
                    "title": "Results and next steps",
                    "notes": ["Output format, interpretation, useful next action"],
                },
                {
                    "title": "Accessibility, analytics, and implementation handoff",
                    "notes": [
                        "Keyboard and screen-reader behavior",
                        "Events to measure",
                        "Engineering acceptance checks",
                    ],
                },
            ],
        },
        "article": {
            "job": "Publish a finished article that answers the query and earns a human read-through.",
            "not": "Do not write an outline, a brief, or SEO commentary about the keyword.",
            "voice": "Editorial. Specific. No 'in today's landscape'.",
            "length": "Opening 120–180 words. Each H2 200–350 words.",
            "outline": [
                {"title": f"What {kw} means in practice", "notes": ["Usable definition, not a dictionary line"]},
                {"title": "How to approach it", "notes": ["Method or decision path"]},
                {"title": "What to look for", "notes": ["Quality signals, traps"]},
                {"title": "A worked example", "notes": ["Concrete scenario using client industry/geo — no fake stats"]},
            ],
        },
    }
    pack = catalog.get(page_type) or catalog["article"]
    return pack


def default_outline_for(
    page_type: str, *, keyword: str, client_name: str
) -> list[dict[str, Any]]:
    return list(page_playbook(page_type, keyword=keyword, client_name=client_name)["outline"])


def _outline_is_generic(sections: list[dict[str, Any]]) -> bool:
    if not sections:
        return True
    blob = " ".join(str(s.get("title") or "") for s in sections).lower()
    if "involves" in blob and "how to choose" in blob:
        return True
    return "what it involves" in blob or "how to choose / apply" in blob


def _effective_outline(brief: dict[str, Any], *, client_name: str) -> list[dict[str, Any]]:
    sections = _outline_sections(brief)
    ptype = resolve_page_type(brief)
    kw = str(brief.get("keyword") or brief.get("title") or "").strip()
    if not sections or _outline_is_generic(sections):
        return default_outline_for(ptype, keyword=kw, client_name=client_name)
    return sections


def _topic_context(
    brief: dict[str, Any],
    *,
    client_name: str,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    mkt = dict(marketing or {})
    intake = dict(mkt.get("client_intake") or {})
    geo = (
        location
        or mkt.get("geographic_focus")
        or mkt.get("location")
        or intake.get("geographic_focus")
        or ""
    )
    products = (
        mkt.get("products_services")
        or mkt.get("products")
        or intake.get("products_services")
        or []
    )
    if isinstance(products, str):
        products = [p.strip() for p in products.split(",") if p.strip()]
    audience = (
        brief.get("audience")
        or mkt.get("target_audience")
        or intake.get("target_demographic")
        or ""
    )
    return {
        "client": client_name,
        "keyword": str(brief.get("keyword") or brief.get("title") or "").strip(),
        "title": str(brief.get("title") or brief.get("keyword") or "").strip(),
        "audience": str(audience or "").strip(),
        "industry": str(industry or mkt.get("industry") or "").strip(),
        "geo": str(geo or "").strip(),
        "products": [str(p) for p in products if p][:6],
        "intent": str(brief.get("search_intent") or brief.get("intent") or "").strip(),
        "format": str(brief.get("content_type") or "").strip(),
        "page_type": resolve_page_type(brief),
        "positioning": str(
            mkt.get("positioning") or intake.get("positioning") or ""
        ).strip(),
    }


# Soft ceiling so a bad brief cannot spawn unbounded OpenRouter image calls.
MAX_DRAFT_IMAGES = 12


def _image_count_target(brief: dict[str, Any], collected: int) -> int:
    """How many frames this page should carry — driven by requirements / outline."""
    req = brief.get("image_requirements") if isinstance(brief.get("image_requirements"), dict) else {}
    for key in ("count", "image_count", "min_count"):
        raw = req.get(key) if key != "image_count" else (req.get(key) or brief.get(key))
        if isinstance(raw, int) and raw > 0:
            return max(1, min(raw, MAX_DRAFT_IMAGES))
        if isinstance(raw, str) and raw.strip().isdigit():
            return max(1, min(int(raw.strip()), MAX_DRAFT_IMAGES))

    outline = brief.get("outline") or brief.get("sections") or []
    body_n = 0
    if isinstance(outline, list):
        for row in outline:
            title = ""
            if isinstance(row, dict):
                title = str(row.get("title") or row.get("heading") or "")
            else:
                title = str(row or "")
            if title.strip() and title.strip().upper() not in ("FAQ", "CTA", "NEXT STEP", "CONCLUSION"):
                body_n += 1

    # Prefer whatever was already collected from strategy/brief; grow with outline when thin.
    if collected >= 3:
        return min(MAX_DRAFT_IMAGES, collected)
    if body_n:
        return min(MAX_DRAFT_IMAGES, max(collected, 1 + max(1, (body_n + 1) // 2)))
    return min(MAX_DRAFT_IMAGES, max(collected, 2))


def collect_image_specs(
    brief: dict[str, Any],
    *,
    seo_strategy: dict[str, Any] | None = None,
    client_name: str = "",
    industry: str | None = None,
    location: str | None = None,
) -> list[dict[str, Any]]:
    """Pick image prompts from strategy / brief requirements — count follows the page, not a fixed 2."""
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(raw: Any, role: str | None = None) -> None:
        prompt = ""
        r = role
        if isinstance(raw, dict):
            prompt = str(raw.get("prompt") or raw.get("description") or raw.get("alt") or "").strip()
            r = str(raw.get("role") or r or "supporting")
        else:
            prompt = str(raw or "").strip()
            r = r or "supporting"
        key = re.sub(r"\s+", " ", prompt.lower())
        if len(prompt) < 12 or key in seen:
            return
        seen.add(key)
        specs.append(
            {
                "role": r or "supporting",
                "prompt": prompt,
                "alt": f"{r or 'figure'}: {brief.get('keyword') or client_name}",
                "caption": prompt[:180],
            }
        )

    kw = _norm_topic(brief.get("keyword") or brief.get("title"))
    brief_url = _url_path(brief.get("url") or brief.get("path"))
    strategy = dict(seo_strategy or {})
    for key in ("priority_queue", "combined_priority_queue", "priority_pages"):
        for item in strategy.get(key) or []:
            if not isinstance(item, dict):
                continue
            same_kw = kw and _norm_topic(item.get("keyword") or item.get("title")) == kw
            same_url = brief_url and _url_path(
                item.get("suggested_url") or item.get("url") or item.get("path")
            ) == brief_url
            if not (same_kw or same_url):
                continue
            for sug in item.get("image_suggestions") or []:
                _add(sug)

    req = brief.get("image_requirements") if isinstance(brief.get("image_requirements"), dict) else {}
    for sug in req.get("suggestions") or brief.get("image_suggestions") or []:
        _add(sug)
    if req.get("hero"):
        _add(req.get("hero"), "hero")
    supporting = req.get("supporting")
    if isinstance(supporting, str):
        supporting = [supporting]
    for row in supporting or []:
        _add(row, "supporting")

    target = _image_count_target(brief, len(specs))
    if len(specs) < target:
        from app.services.content_strategy import image_suggestions_for

        ctx = _topic_context(brief, client_name=client_name, industry=industry, location=location)
        outline = brief.get("outline") or brief.get("sections") or []
        for sug in image_suggestions_for(
            ctx["keyword"],
            ctx["format"] or "blog",
            ctx["intent"] or "informational",
            industry=ctx["industry"] or industry,
            location=ctx["geo"] or location,
            outline=outline if isinstance(outline, list) else None,
            count=target,
        ):
            _add(sug)
            if len(specs) >= target:
                break

    # Hero first, then supporting — keep every distinct prompt up to the soft ceiling.
    heroes = [s for s in specs if s.get("role") == "hero"]
    rest = [s for s in specs if s.get("role") != "hero"]
    ordered = (heroes[:1] or []) + rest
    if not ordered:
        ordered = list(specs)

    deduped: list[dict[str, Any]] = []
    seen_p: set[str] = set()
    for s in ordered:
        k = s["prompt"].lower()
        if k in seen_p:
            continue
        seen_p.add(k)
        deduped.append(s)
        if len(deduped) >= min(target, MAX_DRAFT_IMAGES):
            break
    return deduped


def _figure_markdown(img: dict[str, Any]) -> str:
    alt = str(img.get("alt") or img.get("role") or "Figure")
    src = str(img.get("src") or "").strip()
    cap = str(img.get("caption") or img.get("prompt") or alt).strip()
    if src:
        return f"![{alt}]({src})\n*{cap[:220]}*"
    return f"[FIGURE {img.get('role') or 'image'}] {cap[:220]}"


def _inject_supporting_into_markdown(md: str, figures: list[dict[str, Any]]) -> str:
    """Spread supporting figures after body H2 sections; append any leftovers."""
    if not figures:
        return md
    parts = re.split(r"(?m)(^##\s+.+)$", md)
    if len(parts) < 3:
        extra = "\n\n".join(_figure_markdown(f) for f in figures)
        return (md.rstrip() + "\n\n" + extra + "\n") if extra else md

    out: list[str] = [parts[0]]
    fig_i = 0
    i = 1
    while i < len(parts):
        heading = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out.append(heading)
        out.append(body)
        skip = bool(re.match(r"(?i)^##\s+(faq|cta|next step)\b", heading or ""))
        if fig_i < len(figures) and body.strip() and not skip:
            out.append("\n\n" + _figure_markdown(figures[fig_i]) + "\n")
            fig_i += 1
        i += 2
    while fig_i < len(figures):
        out.append("\n\n" + _figure_markdown(figures[fig_i]) + "\n")
        fig_i += 1
    return "".join(out)


async def generate_draft_images(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for spec in specs[:MAX_DRAFT_IMAGES]:
        prompt = str(spec.get("prompt") or "").strip()
        if not prompt:
            continue
        ratio = "16:9" if spec.get("role") == "hero" else "4:3"
        result = await generate_openrouter_image(prompt, aspect_ratio=ratio)
        src = (result or {}).get("src") if isinstance(result, dict) else None
        out.append(
            {
                **spec,
                "src": src,
                "status": "ready" if src else "failed",
                "error": None if src else (result or {}).get("error"),
            }
        )
    return out


def _coverage(brief: dict[str, Any]) -> dict[str, Any]:
    cov = brief.get("required_coverage") if isinstance(brief.get("required_coverage"), dict) else {}
    return {
        "outcomes": [str(x) for x in (cov.get("outcomes") or []) if x],
        "must_address": [str(x) for x in (cov.get("must_address") or []) if x],
        "must_name": [str(x) for x in (cov.get("must_name") or []) if x],
        "out_of_scope": [str(x) for x in (cov.get("out_of_scope") or []) if x],
    }


def _is_instructional(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if _INSTRUCTIONAL.search(t):
        return True
    return bool(_IMPERATIVE_OPEN.search(t) and len(t) < 280)


def _usable_prose(text: str) -> str:
    """Keep finished copy; drop outline notes and writer instructions."""
    t = str(text or "").strip()
    if not t or _is_instructional(t):
        return ""
    return t


def _count_placeholders(text: str) -> tuple[int, int]:
    blob = str(text or "")
    return blob.count("[AUTHOR INPUT REQUIRED"), blob.count("[VERIFY]")


_INVENTED_STAT = re.compile(
    r"\b(\d{1,3}(?:\.\d+)?%\s+(?:of|increase|decrease|growth|boost|more|higher|lower)"
    r"|studies show|research shows|according to (?:a |our )?study"
    r"|\$\d[\d,]*(?:\.\d+)?\s+(?:ROI|revenue|savings))\b",
    re.I,
)


def score_draft_accuracy(
    draft: dict[str, Any],
    brief: dict[str, Any],
    *,
    client_name: str,
    related: list[str] | None = None,
) -> dict[str, Any]:
    """Heuristic accuracy score — keyword/client grounding, scope, invention risk."""
    blob = " ".join(
        str(draft.get(k) or "")
        for k in ("title", "markdown", "meta_description", "opening")
    ).lower()
    grounding = brief.get("accuracy_grounding") if isinstance(brief.get("accuracy_grounding"), dict) else {}
    primary = str(
        grounding.get("primary_keyword") or brief.get("keyword") or brief.get("title") or ""
    ).strip()
    related_terms = [
        str(t).strip()
        for t in (related or grounding.get("related_keywords") or brief.get("secondary_keywords") or [])
        if str(t).strip()
    ][:8]
    must_name = [str(x) for x in (grounding.get("must_name") or [client_name]) if x]
    out_of_scope = [str(x) for x in (grounding.get("out_of_scope") or []) if x]
    geo = str(grounding.get("geo") or brief.get("location") or "").strip()
    checks: list[dict[str, Any]] = []

    def _check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    if primary:
        _check(
            "primary_keyword",
            primary.lower() in blob,
            f"Primary “{primary}” {'found' if primary.lower() in blob else 'missing'} in draft",
        )
    if client_name:
        _check(
            "client_named",
            client_name.lower() in blob,
            f"Client “{client_name}” {'named' if client_name.lower() in blob else 'missing'}",
        )
    if related_terms:
        hits = [t for t in related_terms if t.lower() in blob]
        _check(
            "related_keywords",
            len(hits) >= max(1, min(2, len(related_terms))),
            f"{len(hits)}/{len(related_terms)} related terms used",
        )
    for name in must_name[:3]:
        if name.lower() == (client_name or "").lower():
            continue
        _check("must_name", name.lower() in blob, f"Must-name “{name}” {'present' if name.lower() in blob else 'missing'}")
    scope_hits = [s for s in out_of_scope if len(s) >= 8 and s.lower() in blob]
    _check(
        "out_of_scope",
        not scope_hits,
        "No out-of-scope topics" if not scope_hits else f"Out-of-scope leaked: {', '.join(scope_hits[:2])}",
    )
    if geo and len(geo) >= 3:
        _check(
            "geo_grounding",
            geo.lower() in blob,
            f"Geo “{geo}” {'present' if geo.lower() in blob else 'missing'}",
        )
    invented = bool(_INVENTED_STAT.search(blob)) and "[verify]" not in blob
    _check(
        "no_unsourced_claims",
        not invented,
        "No unsourced stat patterns" if not invented else "Possible unsourced stat/claim — needs [VERIFY]",
    )
    passed = sum(1 for c in checks if c["ok"])
    total = len(checks) or 1
    score = round(100.0 * passed / total)
    return {
        "score": score,
        "passed": passed,
        "total": total,
        "checks": checks,
        "ok": score >= 70,
    }


def _accuracy_prompt_block(brief: dict[str, Any]) -> str:
    g = brief.get("accuracy_grounding") if isinstance(brief.get("accuracy_grounding"), dict) else {}
    if not g:
        return ""
    lines = ["ACCURACY GROUNDING (honour exactly — do not invent beyond this):"]
    for rule in g.get("rules") or []:
        lines.append(f"- {rule}")
    if g.get("geo"):
        lines.append(f"- Geography: {g['geo']}")
    if g.get("products"):
        lines.append(f"- Nameable products/services only: {g['products']}")
    if g.get("serp_competitors_to_beat"):
        lines.append(
            "- SERP titles to beat (do not paraphrase as your H1/H2): "
            + "; ".join(str(t) for t in g["serp_competitors_to_beat"][:5])
        )
    if g.get("competitive_notes"):
        lines.append("- Competitive gaps to cover: " + "; ".join(str(n) for n in g["competitive_notes"][:5]))
    return "\n".join(lines) + "\n"


def _section_prose(
    *,
    stitle: str,
    kw: str,
    client_name: str,
    related_terms: list[str],
    notes: list[Any],
    must_address: list[str],
    page_type: str = "article",
    audience: str = "",
    geo: str = "",
    products: list[str] | None = None,
    positioning: str = "",
) -> str:
    matched = next(
        (t for t in related_terms if _norm_topic(t) in _norm_topic(stitle)),
        None,
    )
    usable_notes = [str(n).strip() for n in notes if str(n).strip() and not _is_instructional(str(n))]
    who = audience or "the businesses you work with"
    place = f" in {geo}" if geo else ""
    offer = ", ".join((products or [])[:3])
    angle = positioning or f"how {client_name} actually delivers the work"
    note_bit = " ".join(usable_notes[:2])
    floor = must_address[0] if must_address else None
    related_line = (
        f" “{matched}” sits under “{kw}” on this page — it is not a second topic."
        if matched
        else ""
    )

    if page_type == "tool":
        section = stitle.lower()
        if "interactive component" in section:
            return (
                "**[INTERACTIVE COMPONENT REQUIRED — specification, not an implemented embed]**\n\n"
                f"The {kw} component should guide one user through one complete task and "
                "return a result they can understand without reading a separate article. "
                "Engineering must confirm the component framework, persistence, and embed "
                "location before this page is marked publish-ready.\n\n"
                "Required states: initial, valid input, invalid input, calculating, result, "
                "and recoverable error. Never substitute static prose for these states."
            )
        if "inputs and validation" in section:
            return (
                f"Define every input for {kw} with its label, data type, unit, required status, "
                "allowed range, default, and plain-language help text. Product owners must "
                "approve any defaults or ranges; this draft does not invent them.\n\n"
                "Validation must be inline, keyboard-accessible, and specific about how to "
                "correct the value. Empty, malformed, extreme, and conflicting inputs need "
                "documented behavior."
            )
        if "calculation" in section or "decision logic" in section:
            return (
                f"Document the formula or branch table for {kw} as an implementation contract. "
                "Each input must map to an output, with assumptions, rounding, units, and edge "
                "cases stated explicitly.\n\n"
                "**[PRODUCT CONFIRMATION REQUIRED]** — no formula, scoring weight, benchmark, "
                "or recommendation may be inferred from the keyword alone."
            )
        if "accessibility" in section or "handoff" in section:
            return (
                "Implementation acceptance: complete keyboard operation, programmatic labels, "
                "announced validation and results, sensible focus movement, mobile layout, and "
                "a non-JavaScript explanation of the tool's purpose.\n\n"
                "Analytics should distinguish tool start, validation failure, completion, result "
                "view, and next-step click without recording sensitive input values."
            )
        if "results" in section:
            return (
                f"The result for {kw} should name the outcome, show the inputs that produced it, "
                "explain assumptions, and give the user a useful next step. Include reset and "
                "edit-input actions; do not present an estimate as a guaranteed result."
            )
        return (
            f"{kw} should help {who}{place} complete a defined task, not merely read about it. "
            f"This specification states the user outcome and the boundary of what the tool can "
            f"claim on behalf of {client_name}.\n\n"
            "Success means a user can provide valid inputs, understand the result, and decide "
            "what to do next. The interactive component remains an engineering deliverable."
        )

    if page_type in ("service", "subservice", "sub_service", "landing", "product", "location"):
        p1 = (
            f"{client_name} offers {kw}{place} for {who}. "
            f"This part of the page — {stitle.lower()} — is the practical detail a buyer needs "
            f"before they enquire, not a definition of the keyword."
        )
        p2 = (
            f"{note_bit} " if note_bit else ""
        ) + (
            f"Expect a named process, a clear owner, and a way to tell whether {kw} is working. "
            + (f"Offerings that may be named here: {offer}. " if offer else "")
            + f"{angle.rstrip('.')}."
        )
        p3 = (
            f"Skip generic pitches. If a claim cannot be evidenced, it does not belong here."
            + (f" Ranking pages often lead with “{floor}” — answer that only as it applies to {kw}." if floor else "")
            + related_line
        )
        return f"{p1}\n\n{p2.strip()}\n\n{p3.strip()}"

    if page_type == "comparison":
        return (
            f"Use {stitle.lower()} to choose between options for {kw}, not to stall the decision. "
            f"{client_name} would score each option on named criteria — fit, effort, risk, and what you still have to do in-house."
            f"{related_line}\n\n"
            + (f"{note_bit}\n\n" if note_bit else "")
            + "Write the trade-off in plain language. A comparison page that says “it depends” without saying *on what* is unfinished."
        )

    p1 = (
        f"{stitle} is the part of “{kw}” most readers still get wrong. "
        f"{client_name} writes for {who}{place} and stays on that query — "
        f"what it is, what to do, and what to ignore."
    )
    p2 = (
        (f"{note_bit} " if note_bit else "")
        + (f"Ground this section in {angle.rstrip('.')}. " if positioning else "")
        + (f"Bring in “{floor}” only where it helps someone acting on {kw}." if floor else "Be specific enough that a practitioner could use the paragraph.")
        + related_line
    )
    p3 = (
        f"Do not wander into adjacent services. If {client_name} names a product or method, "
        f"it must belong to {kw}."
        + (f" Related terms that may appear in support: {', '.join(related_terms[:4])}." if related_terms else "")
    )
    return f"{p1}\n\n{p2.strip()}\n\n{p3.strip()}"


def _opening_prose(
    *,
    brief: dict[str, Any],
    client_name: str,
    kw: str,
    page_type: str,
    outcomes: list[str],
    must_address: list[str],
    related_terms: list[str],
    audience: str = "",
    geo: str = "",
    positioning: str = "",
    angle: str = "",
) -> str:
    who = audience or "the reader"
    place = f" in {geo}" if geo else ""
    outcome = outcomes[0].rstrip(".") if outcomes else None
    floor = ", ".join(must_address[:2]) if must_address else None
    angle_lead = _ANGLE_OPENING_LEAD.get(str(angle or "").strip().lower())
    related_clause = (
        f" Related terms used in support of that query (not as competing topics): {', '.join(related_terms[:6])}."
        if related_terms
        else ""
    )
    diff = _differentiation(brief)
    if page_type == "tool":
        return (
            f"{kw[0].upper() + kw[1:] if kw else 'This tool'} is an interactive product page "
            f"for {who}{place}, not a prose-only article. "
            + (f"Its intended outcome is to help the user {outcome[0].lower() + outcome[1:]}. " if outcome else "")
            + (f"{diff} " if diff else "")
            + "\n\n"
            "**[INTERACTIVE COMPONENT REQUIRED]** — this draft supplies the component contract "
            "and supporting copy. Product and engineering must confirm the calculation or "
            "decision logic before implementation or publication."
        )
    if page_type in ("service", "subservice", "sub_service", "landing", "product", "location"):
        lead = (
            f"{client_name} provides {kw}{place} for {who}. "
            + (f"{outcome}. " if outcome else "This page says what is included, how the work runs, and how to start. ")
            + (f"{diff} " if diff else "")
        )
        second = (
            "If you are comparing providers, you should leave this page knowing the scope, the sequence, "
            "and whether this team is a fit — not a definition of the keyword."
            + (f" We also cover {floor}." if floor else "")
            + related_clause
        )
        return f"{lead.strip()}\n\n{second.strip()}"
    if page_type == "comparison":
        return (
            f"If you searched “{kw}”, you want a verdict, not another roundup. "
            f"{client_name} compares the options on named criteria"
            + (f" for {who}{place}" if who else "")
            + ".\n\n"
            + (f"{diff}\n\n" if diff else "")
            + "Read the criteria first, then the recommendation for your situation."
        )
    lead_sentence = (
        angle_lead.format(kw=kw or "this topic")
        if angle_lead
        else f"{kw[0].upper() + kw[1:] if kw else 'This topic'} is worth a straight answer."
    )
    return (
        f"{lead_sentence} "
        f"{client_name} wrote this for {who}{place}"
        + (f" so they can {outcome[0].lower() + outcome[1:] if outcome else 'act on it'}." if outcome else ".")
        + (f" {diff}" if diff else "")
        + "\n\n"
        + (
            f"The article stays on that assigned query — {floor} — and does not wander into adjacent services."
            if floor
            else "The article stays on that assigned query and does not wander into adjacent services."
        )
        + related_clause
    )


def _cta_prose(
    *,
    client_name: str,
    kw: str,
    page_type: str,
    intent: str,
    geo: str = "",
) -> str:
    place = f" in {geo}" if geo else ""
    if page_type == "tool":
        return (
            f"After reviewing the {kw} result{place}, the user should be able to revise their "
            f"inputs, save or share the outcome where appropriate, or discuss the next step "
            f"with {client_name}.\n\n"
            "Do not enable a conversion claim or recommendation until the component logic, "
            "privacy handling, and result wording have been approved."
        )
    if page_type in ("service", "subservice", "sub_service", "landing", "product", "location") or intent in (
        "transactional",
        "commercial",
    ):
        return (
            f"If {kw} is what you need{place}, the next step with {client_name} is a conversation "
            f"about scope, timing, and whether this team is the right fit.\n\n"
            f"Bring the situation you are in — not a keyword. We will tell you what would be in scope, "
            f"what would not, and what happens after you say yes."
        )
    return (
        f"If this helped you act on {kw}, use it. If you want {client_name} to do the work with you"
        f"{place}, get in touch with the situation you are actually in — we will tell you whether "
        f"a service engagement is the right next step."
    )


def _build_markdown(
    *,
    brief: dict[str, Any],
    client_name: str,
    body_sections: list[dict[str, Any]] | None = None,
    existing_excerpt: str | None = None,
    opening: str | None = None,
    images: list[dict[str, Any]] | None = None,
    related: list[str] | None = None,
    faq_answers: list[dict[str, Any]] | None = None,
    page_markdown: str | None = None,
    cta: str | None = None,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    location: str | None = None,
) -> dict[str, Any]:
    pre = _preflight(brief)
    author, standing, _ymyl = _author(brief)
    diff = _differentiation(brief)
    title = str((brief.get("title_options") or [None])[0] or brief.get("title") or brief.get("keyword"))
    url = str(brief.get("url") or pre.get("url") or brief.get("path") or "/")
    parent = str(pre.get("parent") or brief.get("parent") or "/")
    action = str(brief.get("action") or "create")
    meta = sanitize_meta_description(brief.get("meta_description"))[:160]
    cov = _coverage(brief)
    ctx = _topic_context(
        brief, client_name=client_name, marketing=marketing, industry=industry, location=location
    )
    page_type = ctx["page_type"] or resolve_page_type(brief)
    sections = body_sections or _effective_outline(brief, client_name=client_name)
    kw_primary = str(brief.get("keyword") or title).strip()
    related_terms = [str(t).strip() for t in (related or []) if str(t).strip()]
    related_label = ", ".join(related_terms) if related_terms else "none in brief/cluster"

    author_placeholders: list[str] = []
    verify_placeholders: list[str] = []
    lines: list[str] = [f"# {title}", ""]

    # Review queue first
    lines.extend(
        [
            "## Review queue — resolve before publishing",
            "- [ ] [AUTHOR INPUT REQUIRED] — count filled after draft sections",
            "- [ ] [VERIFY] — facts, figures, citations",
            "- [ ] Named reviewer: ____________  Date: ________",
            "",
            "## Draft metadata",
            f"- **URL**: {url} | **Parent**: {parent}",
            f"- **Title tag**: {title[:60]}",
            f"- **Meta description**: {meta}",
            f"- **Primary keyword**: {kw_primary}",
            f"- **Related keywords**: {related_label}",
            f"- **Page type**: {page_type}"
            + (f" | **Angle**: {brief.get('angle')}" if brief.get("angle") else ""),
            f"- **Author**: {author}" + (f" — {standing}" if standing else ""),
            f"- **Action**: {action}",
            "",
            "## Differentiation delivered",
            f"{diff} (see opening and body sections below).",
            "",
            "---",
            "",
            f"# {title}",
            "",
        ]
    )

    # Opening — page-type-aware, publishable prose
    outcomes = cov["outcomes"]
    must_address = cov["must_address"]
    opening_text = _usable_prose(str(opening or "").strip())
    kw = kw_primary or title
    full_page = str(page_markdown or "").strip()
    if full_page and _is_instructional(full_page[:280]):
        full_page = ""
    if full_page.startswith("#"):
        # Drop a duplicate H1 if the model included one
        first, _, rest = full_page.partition("\n")
        if first.lstrip().startswith("#"):
            full_page = rest.strip()
    if not opening_text and not full_page:
        opening_text = _opening_prose(
            brief=brief,
            client_name=client_name,
            kw=str(kw),
            page_type=page_type,
            outcomes=outcomes,
            must_address=must_address,
            related_terms=related_terms,
            audience=ctx.get("audience") or "",
            geo=ctx.get("geo") or "",
            positioning=ctx.get("positioning") or "",
            angle=str(brief.get("angle") or ""),
        )
    if opening_text and not (full_page and opening_text[:80] in full_page):
        lines.extend([opening_text, ""])
    figures = [i for i in (images or []) if isinstance(i, dict)]
    hero = next((i for i in figures if i.get("role") == "hero"), figures[0] if figures else None)
    supporting = [i for i in figures if i is not hero]
    if hero:
        lines.extend([_figure_markdown(hero), ""])

    if action == "refresh" and existing_excerpt:
        lines.extend(
            [
                "## What we keep from the live page",
                existing_excerpt[:600],
                "",
                "[AUTHOR INPUT REQUIRED — first-hand: confirm which live sections still convert "
                "and which claims need updating. Do not discard ranking structure blindly.]",
                "",
            ]
        )
        author_placeholders.append("Refresh: confirm keep/cut on live page")

    has_full = len(full_page) >= 500 and "## " in full_page
    if has_full:
        body_md = _inject_supporting_into_markdown(full_page, supporting)
        supporting = []
        lines.extend([body_md, ""])
        a_n, v_n = _count_placeholders(full_page)
        if a_n:
            author_placeholders.append("body")
        if v_n:
            verify_placeholders.append("body")
    else:
        for sec in sections:
            stitle = sec.get("title") or "Section"
            if stitle.upper() in ("FAQ", "NEXT STEP", "CTA"):
                continue
            lines.extend([f"## {stitle}", ""])
            notes = sec.get("notes") or []
            if isinstance(notes, str):
                notes = [notes]
            body = _usable_prose(str(sec.get("body") or ""))
            if not body:
                usable_notes = [
                    str(n).strip() for n in notes if str(n).strip() and not _is_instructional(str(n))
                ]
                if usable_notes and all(len(n) > 80 for n in usable_notes[:2]):
                    body = "\n\n".join(usable_notes[:3])
                else:
                    body = _section_prose(
                        stitle=stitle,
                        kw=str(kw),
                        client_name=client_name,
                        related_terms=related_terms,
                        notes=notes,
                        must_address=must_address,
                        page_type=page_type,
                        audience=ctx.get("audience") or "",
                        geo=ctx.get("geo") or "",
                        products=list(ctx.get("products") or []),
                        positioning=ctx.get("positioning") or "",
                    )
            lines.extend([body, ""])
            if supporting and stitle not in ("FAQ",):
                lines.extend([_figure_markdown(supporting.pop(0)), ""])
            a_n, v_n = _count_placeholders(body)
            if a_n:
                author_placeholders.append(stitle)
            if v_n:
                verify_placeholders.append(stitle)
        # Any remaining frames (outline shorter than image count) go before FAQ/CTA.
        while supporting:
            lines.extend([_figure_markdown(supporting.pop(0)), ""])

    # FAQ from PAA only — skip if the full page already includes FAQ
    faq_in = faq_answers if faq_answers is not None else (brief.get("faq") or [])
    already_faq = has_full and re.search(r"^##\s+faq\b", full_page, re.I | re.M)
    if faq_in and not already_faq:
        lines.extend(["## FAQ", ""])
        for q in faq_in:
            if isinstance(q, dict):
                question = q.get("q") or q.get("question")
                answer = _usable_prose(str(q.get("a") or q.get("answer") or ""))
            else:
                question, answer = q, ""
            if not question:
                continue
            if not answer:
                answer = (
                    f"{question.rstrip('?')} depends on the situation behind “{kw}”. "
                    f"Use the sections above for the criteria {client_name} would apply, "
                    f"then decide whether you need a service conversation or you can act on this yourself."
                )
            lines.extend([f"### {question}", "", answer, ""])
            a_n, v_n = _count_placeholders(answer)
            if a_n:
                author_placeholders.append(f"FAQ: {question}")
            if v_n:
                verify_placeholders.append(f"FAQ: {question}")

    already_cta = has_full and re.search(r"^##\s+(next step|get started|contact)\b", full_page, re.I | re.M)
    cta_text = _usable_prose(str(cta or ""))
    if cta_text and len(cta_text) < 80:
        cta_text = ""
    cta_text = cta_text or _cta_prose(
        client_name=client_name,
        kw=str(kw),
        page_type=page_type,
        intent=str(brief.get("search_intent") or brief.get("funnel") or ""),
        geo=ctx.get("geo") or "",
    )
    if not already_cta:
        lines.extend(["## Next step", "", cta_text, "", "---", "", "## Coverage check", ""])
    else:
        lines.extend(["", "---", "", "## Coverage check", ""])
    lines.append("| Required outcome (from brief) | Addressed in |")
    lines.append("|---|---|")
    for o in outcomes or ["(no outcomes listed)"]:
        lines.append(f"| {o} | Opening / body |")
    lines.extend(["", "## Notes for the editor", ""])
    lines.append(f"- Client: {client_name}")
    lines.append(f"- Primary keyword (priority): {kw_primary}")
    if related_terms:
        lines.append(
            "- Related keywords (use in body/H2/H3; do not outrank primary): "
            + ", ".join(related_terms)
        )
    if author_placeholders:
        lines.append(f"- Author placeholders: {', '.join(author_placeholders)}")
    if verify_placeholders:
        lines.append(f"- VERIFY markers: {len(verify_placeholders)} sections")
    if cov["out_of_scope"]:
        lines.append(
            "- Out of scope (linked / excluded, not covered): " + "; ".join(cov["out_of_scope"][:8])
        )
    fit = brief.get("business_fit")
    if isinstance(fit, dict) and fit:
        lines.append(
            "- Business fit (why Phase 5/6 prioritised this topic): "
            + ", ".join(f"{k}: {v}" for k, v in fit.items() if v not in (None, "", [], {}))
        )
    lines.append("- Content translation is out of scope for this draft.")
    lines.append("- Do not publish until named reviewer signs the review queue.")

    # Patch review queue counts
    md = "\n".join(lines)
    author_line = (
        f"- [ ] [AUTHOR INPUT REQUIRED] × {len(author_placeholders)} — listed by section below"
        if author_placeholders
        else "- [ ] First-hand placeholders: none — named reviewer still signs before publish"
    )
    verify_line = (
        f"- [ ] [VERIFY] × {len(verify_placeholders)} — facts, figures, citations"
        if verify_placeholders
        else "- [ ] Unsourced facts flagged: none — named reviewer still signs before publish"
    )
    md = md.replace(
        "- [ ] [AUTHOR INPUT REQUIRED] — count filled after draft sections",
        author_line,
    )
    md = md.replace(
        "- [ ] [VERIFY] — facts, figures, citations",
        verify_line,
    )

    return {
        "url": url,
        "title": title,
        "meta_description": meta,
        "keyword": brief.get("keyword"),
        "primary_keyword": kw_primary,
        "related_keywords": related_terms,
        "page_type": page_type,
        "funnel": brief.get("funnel"),
        "angle": brief.get("angle"),
        "business_fit": brief.get("business_fit"),
        "action": action,
        "author": author,
        "author_standing": standing,
        "differentiation": diff,
        "markdown": md,
        "images": figures,
        "status": "draft_pending_review",
        "review_required": True,
        "author_input_count": len(author_placeholders),
        "verify_count": len(verify_placeholders),
        "from_brief_status": brief.get("status"),
        "writer_ready": True,
        "translation": "excluded",
        "evidence_base": "skills/references/google-helpful-content.md",
        "people_first": {
            "named_human_review_required": True,
            "no_word_count_target": True,
            "no_keyword_density_target": True,
            "ai_assisted": True,
            "purpose": "help the intended audience — not search-engine-first calendar fill",
        },
        "who_how_why": brief.get("who_how_why"),
    }


async def _llm_draft_article(
    brief: dict[str, Any],
    client_name: str,
    *,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    location: str | None = None,
    image_specs: list[dict[str, Any]] | None = None,
    related: list[str] | None = None,
    outline: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Full ready-to-publish page for the resolved page type."""
    skill = load_skill_file("create-content")
    evidence = load_shared_reference("google-helpful-content.md", max_chars=5500)
    ctx = _topic_context(
        brief,
        client_name=client_name,
        marketing=marketing,
        industry=industry,
        location=location,
    )
    page_type = ctx["page_type"] or resolve_page_type(brief)
    kw = ctx["keyword"] or ctx["title"]
    sections = outline or _effective_outline(brief, client_name=client_name)
    if not sections:
        sections = default_outline_for(page_type, keyword=kw, client_name=client_name)
    playbook = page_playbook(page_type, keyword=kw, client_name=client_name)
    cov = _coverage(brief)
    related_terms = [str(t).strip() for t in (related or []) if str(t).strip()]
    system = (
        (skill[:2800] if skill else "You are a senior on-staff copywriter.")
        + "\n\nGoogle helpful-content evidence (canonical — follow):\n"
        + (
            evidence
            or (
                "People-first. No preferred word count. No keyword density. "
                "AI OK if purpose is helping people; scaled unoriginal pages = spam. "
                "Who/How/Why: named author, disclose AI when expected, why = help audience."
            )
        )
        + "\nReturn JSON only: "
        '{"opening": str, "page_markdown": str, "sections": [{"title": str, "body": str}], '
        '"faq": [{"q": str, "a": str}], "cta": str}. '
        "You write FINISHED, ready-to-publish webpage copy. An editor should only fact-check "
        "and sign — not rewrite. "
        "page_markdown is the full page from the first H2 through the last body section "
        "(markdown headings + paragraphs + bullets). Do not include H1, review queue, or metadata. "
        "Each H2 body is 3–6 short paragraphs of real copy, not notes. "
        "Forbidden: outlines, 'cover this section', 'people who searched', 'treats X as a decision', "
        "'stay on that query', 'in today's digital landscape', 'when it comes to', keyword stuffing, "
        "invented statistics, fake case studies, fake quotes, padding to a word count, "
        "claiming Google requires pillar pages or a topical authority score. "
        "Primary keyword in title/H1/first paragraph/one H2/closing. Related terms only in support. "
        "Placeholders are rare: [AUTHOR INPUT REQUIRED] only for a named first-hand fact; "
        "[VERIFY] only beside a specific unsourced number/date/price/study. "
        "Honour out_of_scope. Deliver differentiation in the opening and again in the body. "
        "No word_count field. One page only. Pass the people-first test: original value, "
        "bookmark-worthy, leaves the reader able to act. "
        "Accuracy: only use supplied related keywords, products, geo, and competitive notes; "
        "never invent markets, stats, or case studies."
    )
    image_note = ""
    if image_specs:
        image_note = (
            "Image captions to reference in the matching section using [FIGURE role] caption "
            f"({len(image_specs)} figures — place each where it helps the reader, hero under the title):\n"
            + "\n".join(
                f"- {s.get('role')}: {str(s.get('prompt') or '')[:180]}"
                for s in image_specs[:MAX_DRAFT_IMAGES]
            )
            + "\n"
        )
    related_block = (
        "RELATED KEYWORDS — use each at least once, supporting the primary (do not invent extras):\n"
        + "\n".join(f"- {t}" for t in related_terms)
        + "\n"
        if related_terms
        else "RELATED KEYWORDS: none supplied — do not invent extra target terms.\n"
    )
    outline_lines = "\n".join(
        f"- {s.get('title')}"
        + (f" — {'; '.join(str(n) for n in (s.get('notes') or [])[:2])}" if s.get("notes") else "")
        for s in sections[:12]
    )
    angle_line = angle_guidance(brief.get("angle"), kw=kw)
    user = (
        f"PAGE TYPE: {page_type}\n"
        f"JOB: {playbook['job']}\n"
        f"DO NOT: {playbook['not']}\n"
        f"VOICE: {playbook['voice']}\n"
        f"LENGTH: {playbook['length']}\n"
        + (f"ANGLE ({brief.get('angle')}): {angle_line}\n" if angle_line else "")
        + f"ASSIGNED PRIMARY KEYWORD: “{kw}”. Write the whole page for this query and this page type.\n"
        f"{related_block}"
        f"Client (the brand on the page): {ctx['client']}\n"
        f"Industry: {ctx['industry'] or 'n/a'}\n"
        f"Geography: {ctx['geo'] or 'n/a'}\n"
        f"Audience: {ctx['audience'] or 'n/a'}\n"
        f"Positioning: {ctx.get('positioning') or 'n/a'}\n"
        f"Products/services that may be named (do not invent others): {ctx['products'] or ['n/a']}\n"
        f"Title: {ctx['title']}\n"
        f"Intent: {ctx['intent']}\n"
        f"Differentiation (must appear in opening + body): {_differentiation(brief)}\n"
        f"Required outcomes: {cov['outcomes']}\n"
        f"Must address: {cov['must_address']}\n"
        f"Must name: {cov['must_name']}\n"
        f"Out of scope (do not cover): {cov['out_of_scope']}\n"
        f"H2 outline (use these titles, in order; write finished copy under each):\n{outline_lines}\n"
        f"FAQ (PAA only — answer in 3–5 sentences of real copy): {brief.get('faq') or []}\n"
        f"CTA: write a finished Next step section, not a label like Contact / Learn more.\n"
        f"{_accuracy_prompt_block(brief)}"
        f"{image_note}"
    )
    try:
        settings = get_settings()
        write_model = (
            settings.write_model
            if settings.llm_provider == "openrouter"
            else settings.skill_model
        )
        parsed = await synthesize_json(
            system,
            user,
            max_tokens=12288,
            model=write_model,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("create_content_draft_generation_failed", keyword=kw, error=str(exc))
        return None
    if not isinstance(parsed, dict):
        return None
    opening = _usable_prose(str(parsed.get("opening") or "")) or None
    page_md = str(parsed.get("page_markdown") or parsed.get("markdown") or "").strip()
    if page_md and _is_instructional(page_md[:280]):
        page_md = ""
    cta_text = _usable_prose(str(parsed.get("cta") or ""))
    rows = parsed.get("sections") if isinstance(parsed.get("sections"), list) else []
    clean: list[dict[str, Any]] = []
    for r in rows[:14]:
        if not isinstance(r, dict) or not r.get("title"):
            continue
        body = str(r.get("body") or "").strip()
        notes = r.get("notes") or []
        if isinstance(notes, str):
            notes = [notes]
        body = _usable_prose(body) or _usable_prose(
            "\n\n".join(str(n) for n in notes if n and not _is_instructional(str(n)))
        )
        clean.append({"title": str(r["title"]), "body": body, "notes": []})
    faq_rows: list[dict[str, Any]] = []
    for item in parsed.get("faq") or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q") or item.get("question") or "").strip()
        a = _usable_prose(str(item.get("a") or item.get("answer") or ""))
        if q:
            faq_rows.append({"q": q, "a": a})
    if not opening and not clean and len(page_md) < 400:
        return None
    return {
        "opening": opening,
        "sections": clean or None,
        "faq": faq_rows,
        "page_markdown": page_md or None,
        "cta": cta_text or None,
    }


def refuse_batch(requested: int) -> dict[str, Any]:
    return {
        "ok": False,
        "refused": True,
        "reason": (
            f"Refused batch of {requested} pages. create-content writes one page per invocation "
            "to avoid scaled content abuse (volume without value). Write one, human-review, then next."
        ),
        "route_to": None,
    }


async def write_one_page(
    *,
    brief: dict[str, Any],
    client_name: str,
    existing_excerpt: str | None = None,
    use_llm: bool = True,
    seo_strategy: dict[str, Any] | None = None,
    marketing: dict[str, Any] | None = None,
    industry: str | None = None,
    location: str | None = None,
    generate_images: bool | None = None,
    search_demand: dict[str, Any] | None = None,
    enhance_brief: bool = True,
) -> dict[str, Any]:
    """Write a single page. Never call in a loop to fill a calendar unattended."""
    enhancements: list[str] = []
    if enhance_brief and isinstance(brief, dict):
        from app.services.content_enhancement import enhance_brief_for_writing

        brief, enhancements = enhance_brief_for_writing(
            brief,
            client_name=client_name,
            marketing=marketing,
            industry=industry,
            location=location,
            seo_strategy=seo_strategy,
            search_demand=search_demand,
        )
    gate = draft_gate(brief)
    if not gate.get("ok"):
        return {
            "ok": False,
            "refused": True,
            "reason": gate.get("reason"),
            "route_to": gate.get("route_to"),
            "keyword": (brief or {}).get("keyword") if isinstance(brief, dict) else None,
            "url": (brief or {}).get("url") if isinstance(brief, dict) else None,
        }

    _primary, related = related_keywords(
        brief,
        seo_strategy=seo_strategy,
        search_demand=search_demand,
    )
    specs = collect_image_specs(
        brief,
        seo_strategy=seo_strategy,
        client_name=client_name,
        industry=industry,
        location=location,
    )
    do_images = use_llm if generate_images is None else generate_images
    images: list[dict[str, Any]]
    if do_images and specs:
        images = await generate_draft_images(specs)
    else:
        images = [{**s, "src": None, "status": "placeholder"} for s in specs]

    outline = _effective_outline(brief, client_name=client_name)
    drafted: dict[str, Any] | None = None
    if use_llm:
        drafted = await _llm_draft_article(
            brief,
            client_name,
            marketing=marketing,
            industry=industry,
            location=location,
            image_specs=specs,
            related=related,
            outline=outline,
        )
    draft = _build_markdown(
        brief=brief,
        client_name=client_name,
        body_sections=(drafted or {}).get("sections") or outline,
        existing_excerpt=existing_excerpt,
        opening=(drafted or {}).get("opening"),
        images=images,
        related=related,
        faq_answers=(drafted or {}).get("faq"),
        page_markdown=(drafted or {}).get("page_markdown"),
        cta=(drafted or {}).get("cta"),
        marketing=marketing,
        industry=industry,
        location=location,
    )
    draft["ok"] = True
    draft["refused"] = False
    if enhancements:
        draft["enhancements"] = enhancements
    draft["accuracy"] = score_draft_accuracy(
        draft,
        brief,
        client_name=client_name,
        related=related,
    )
    return draft


def select_one_brief(briefs: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Pick the first brief that passes the draft gate; others stay queued / held."""
    held: list[dict[str, Any]] = []
    chosen: dict[str, Any] | None = None
    for b in briefs:
        if not isinstance(b, dict):
            continue
        gate = draft_gate(b)
        if gate.get("ok") and chosen is None:
            chosen = b
        elif not gate.get("ok"):
            held.append(
                {
                    "keyword": b.get("keyword"),
                    "url": b.get("url"),
                    "reason": gate.get("reason"),
                    "route_to": gate.get("route_to"),
                }
            )
        else:
            held.append(
                {
                    "keyword": b.get("keyword"),
                    "url": b.get("url"),
                    "reason": "Queued — one page per create-content run (scaled content abuse).",
                    "route_to": None,
                }
            )
    return chosen, held
