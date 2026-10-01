"""Google Search content-policy gate for drafts.

Runs after a page is written and before it is published, submitted, or
exported. The loaded policy set is Organic Search / SEO: Google's published
spam policies and Search Essentials. Other Google surfaces (Ads, Shopping,
Play, YouTube, Business Profile) use different rules and are not assessed
here.

The gate never rewrites the draft. It returns PASS, FLAG, FAIL, or
NEEDS_REVIEW findings and an overall status. Finalization is allowed only
when the overall status is PASS, or when a human has explicitly approved a
NEEDS_REVIEW result and HUMAN_IN_LOOP is on. A FAIL cannot be approved away.

Policy pages checked against the live docs:
https://developers.google.com/search/docs/essentials/spam-policies
https://developers.google.com/search/docs/essentials
"""

from __future__ import annotations

import re
from typing import Any

SPAM_POLICIES_URL = "https://developers.google.com/search/docs/essentials/spam-policies"
SEARCH_ESSENTIALS_URL = "https://developers.google.com/search/docs/essentials"

TARGET_SURFACE = "organic_search"
SURFACE_LABEL = "Organic Search/SEO"
HUMAN_IN_LOOP = True

SURFACES = {
    "organic_search": "Organic Search/SEO",
    "google_ads": "Google Ads",
    "google_shopping": "Google Shopping/Merchant Center",
    "google_play": "Google Play",
    "youtube": "YouTube",
    "google_business_profile": "Google Business Profile",
}

_POLICY_AREAS = (
    "prohibited_content",
    "prohibited_practices",
    "restricted_content",
    "editorial_technical",
    "keyword_limits",
    "keyword_repetition",
    "keyword_stuffing",
    "keyword_content_match",
)

_STOP = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by",
    "with", "from", "your", "our", "you", "we", "is", "are", "was", "be", "this",
    "that", "it", "as", "if", "not", "can", "how", "what", "when", "who",
}

_PHONE = re.compile(r"(?:\+?\d[\d\s().-]{7,}\d)")
_HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
_MD_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_HOUSE_RULE = re.compile(
    r"no single keyword\s*>\s*(\d+)\s*[×x]\s*per\s*(\d+)\s*words",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

# Clear Search-spam violations. Wording is taken from the published examples
# (scam/fraud, malware, misleading functionality, counterfeit legal removals).
_FAIL_CONTENT = (
    (
        re.compile(r"\b(counterfeit|fake (?:id|passport|diploma)s?)\b", re.I),
        "prohibited_content",
        "Copy promotes counterfeit or fraudulent documents.",
        f"{SPAM_POLICIES_URL}#legal-removals",
        "Remove the counterfeit or fake-document offer. Google demotes sites tied to counterfeit removals.",
    ),
    (
        re.compile(
            r"\b(malware|keygen|crack(?:ed)? (?:software|serial)|install(?:s|ing)? (?:a )?virus)\b",
            re.I,
        ),
        "prohibited_content",
        "Copy offers malware or a crack.",
        f"{SPAM_POLICIES_URL}#malicious-practices",
        "Remove the download or crack. Malware is a malicious practice under Search spam policies.",
    ),
    (
        re.compile(
            r"\b((?:free |unlimited )?(?:app store |itunes )?credits? generator|fake generator)\b",
            re.I,
        ),
        "prohibited_content",
        "Copy describes a generator that claims a payout it does not deliver.",
        f"{SPAM_POLICIES_URL}#misleading-functionality",
        "Remove the fake-generator claim. Misleading functionality is a Search spam policy.",
    ),
    (
        re.compile(
            r"\b(impersonat(?:e|ing) (?:google|a bank|official support)|we are (?:the )?official google|wire (?:the )?money|send (?:gift cards|bitcoin) to unlock)\b",
            re.I,
        ),
        "prohibited_content",
        "Copy impersonates a business or asks the reader to pay the wrong party.",
        f"{SPAM_POLICIES_URL}#scam-and-fraud",
        "Remove the impersonation and the payment instruction.",
    ),
)

_FAIL_PRACTICE = (
    (
        re.compile(
            r"style\s*=\s*['\"][^'\"]*(?:font-size\s*:\s*0|opacity\s*:\s*0|display\s*:\s*none|text-indent\s*:\s*-)",
            re.I,
        ),
        "Hidden text or CSS is present.",
        f"{SPAM_POLICIES_URL}#hidden-text-and-link-abuse",
        "Delete hidden text. Visitors and Google should see the same words.",
    ),
    (
        re.compile(
            r"(only when the user[- ]agent|if the (?:user[- ]agent|googlebot)|serve different .{0,40}googlebot)",
            re.I,
        ),
        "Copy branches on a crawler user-agent.",
        f"{SPAM_POLICIES_URL}#cloaking",
        "Serve the same content to people and to Google. Cloaking is a spam policy.",
    ),
    (
        re.compile(
            r"(<meta[^>]+http-equiv\s*=\s*['\"]refresh|window\.location\s*=|location\.href\s*=)",
            re.I,
        ),
        "Copy contains a scripted or meta refresh redirect.",
        f"{SPAM_POLICIES_URL}#sneaky-redirects",
        "Remove the redirect. Send people to the page they asked for.",
    ),
)

_FLAG_MISREPRESENTATION = (
    (
        re.compile(
            r"\b(guaranteed (?:#1|number one|page one|rankings?)|100%\s+guaranteed|only \d+ left|act now\b.{0,40}\b(?:expires|limited))\b",
            re.I,
        ),
        "A guarantee, ranking promise, or false-urgency line cannot be verified from the draft.",
        f"{SPAM_POLICIES_URL}#scam-and-fraud",
        "Drop the promise or replace it with a sourced fact. Intentional false information is scam/fraud.",
    ),
)

_GIMMICK = re.compile(r"\${2,}|!{3,}|(?:\b[A-Z]{4,}\b(?:\s+|\s*[,/]\s*)){3,}")
_BROKEN_HREF = re.compile(r"^(?:#|javascript:|)$", re.I)
_CITY_LIST = re.compile(
    r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)(?:\s*,\s*(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)){4,}"
)


def policy_config_from_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    """Operator settings. Missing values use the Organic Search defaults."""
    settings = dict(settings or {})
    surface = str(settings.get("google_policy_surface") or TARGET_SURFACE).strip().lower()
    human = settings.get("google_policy_human_in_loop", HUMAN_IN_LOOP)
    rule = settings.get("google_policy_house_keyword_rule")
    rule_text = str(rule).strip() if rule else ""
    return {
        "target_surface": surface or TARGET_SURFACE,
        "human_in_loop": True if human is None else bool(human),
        "house_keyword_rule": rule_text or None,
    }


def _spam(fragment: str) -> str:
    return f"{SPAM_POLICIES_URL}#{fragment}"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _snippet(text: str, limit: int = 180) -> str:
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"


def _finding(
    area: str,
    status: str,
    reason: str,
    *,
    offending: str | None = None,
    character_count: Any = None,
    suggested_fix: str | None = None,
    policy_reference: str,
    rule_source: str = "google_spam_policy",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "policy_area": area,
        "status": status,
        "reason": reason,
        "offending_text_or_element": offending,
        "character_count": character_count,
        "suggested_fix": suggested_fix,
        "policy_reference": policy_reference,
        "rule_source": rule_source,
    }
    if extra:
        row.update(extra)
    return row


def _item_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("title"),
        item.get("meta_description"),
        item.get("markdown"),
        item.get("page_markdown"),
        item.get("opening"),
        item.get("cta"),
        item.get("html"),
    ]
    alts = []
    for image in item.get("images") or []:
        if isinstance(image, dict):
            alts.append(str(image.get("alt") or image.get("prompt") or ""))
    return "\n".join(str(p) for p in [*parts, *alts] if p)


def _visible_copy(item: dict[str, Any]) -> str:
    """Reader-facing fields. HTML is included only for cloaking and redirect checks."""
    parts = [
        item.get("title"),
        item.get("meta_description"),
        item.get("markdown") or item.get("page_markdown"),
        item.get("opening"),
        item.get("cta"),
    ]
    return "\n".join(str(p) for p in parts if p)


def _keyword(item: dict[str, Any]) -> str:
    return " ".join(str(item.get("keyword") or "").lower().split())


def _unsupported_surface(item: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    label = SURFACES.get(config["target_surface"], config["target_surface"])
    finding = _finding(
        "policy_set",
        "NEEDS_REVIEW",
        (
            f"This gate is loaded with Google Search spam policies for {SURFACE_LABEL}. "
            f"The requested surface is {label}. Those policy sets differ, so this item "
            "was not checked against the other surface and cannot pass."
        ),
        policy_reference=SPAM_POLICIES_URL,
        suggested_fix=f"Set the target surface to {SURFACE_LABEL}, or review this item against {label}'s own published policies.",
    )
    return _assemble(item, config, [finding])


def _assemble(
    item: dict[str, Any],
    config: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    statuses = {f["status"] for f in findings}
    if "FAIL" in statuses:
        overall = "BLOCKED"
    elif "FLAG" in statuses or "NEEDS_REVIEW" in statuses:
        overall = "NEEDS_REVIEW" if config["human_in_loop"] else "BLOCKED"
    else:
        overall = "PASS"
    text = _visible_copy(item) or _item_text(item)
    return {
        "item_id": str(item.get("item_id") or item.get("url") or item.get("keyword") or "draft"),
        "content_snippet": _snippet(str(item.get("title") or "") + " " + text),
        "target_surface": TARGET_SURFACE if config["target_surface"] == TARGET_SURFACE else config["target_surface"],
        "surface_label": SURFACE_LABEL if config["target_surface"] == TARGET_SURFACE else SURFACES.get(config["target_surface"], config["target_surface"]),
        "human_in_loop": bool(config["human_in_loop"]),
        "house_keyword_rule": config.get("house_keyword_rule"),
        "policy_set": "Google Search spam policies",
        "policy_set_url": SPAM_POLICIES_URL,
        "overall_status": overall,
        "allows_finalization": overall == "PASS",
        "findings": findings,
    }


def finalization_allowed(report: dict[str, Any] | None, *, human_approved: bool = False) -> bool:
    """PASS may be finalized. A recorded human approval covers NEEDS_REVIEW only."""
    report = report or {}
    status = report.get("overall_status")
    if status == "PASS":
        return True
    if (
        status == "NEEDS_REVIEW"
        and human_approved
        and report.get("human_in_loop")
        and not any(f.get("status") == "FAIL" for f in (report.get("findings") or []))
    ):
        return True
    return False


def _pass_for_missing(area: str, reason: str, ref: str, source: str = "google_spam_policy") -> dict[str, Any]:
    return _finding(area, "PASS", reason, policy_reference=ref, rule_source=source, suggested_fix=None)


def _stuffing_and_repetition(text: str, keyword: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    words = _words(text)
    word_count = len(words)
    headings = _HEADING.findall(text)

    if keyword:
        window = 40
        phrase_tokens = keyword.split()
        n = len(phrase_tokens)

        def _exact(tokens: list[str]) -> int:
            if n == 0 or len(tokens) < n:
                return 0
            return sum(1 for i in range(len(tokens) - n + 1) if tokens[i : i + n] == phrase_tokens)

        hits_in_window = 0
        if n and words:
            step = 1
            for i in range(0, len(words), step):
                hits_in_window = max(hits_in_window, _exact(words[i : i + window]))
                if i + window >= len(words):
                    break
        if hits_in_window >= 3:
            findings.append(
                _finding(
                    "keyword_repetition",
                    "FLAG",
                    (
                        f"“{keyword}” appears {hits_in_window} times inside a {window}-word span. "
                        "That is unnecessary repetition in a short block. Google publishes no density cap; "
                        "the judgment is that the repetition is unnatural."
                    ),
                    offending=keyword,
                    policy_reference=_spam("keyword-stuffing"),
                    suggested_fix="Keep the phrase where it helps the reader, and rewrite the repeated span in plain sentences.",
                    extra={
                        "keyword": keyword,
                        "occurrence_count": hits_in_window,
                        "word_count": word_count,
                        "judgment": "excessive_repetition",
                    },
                )
            )

    for i, left in enumerate(headings):
        for right in headings[i + 1 :]:
            lt, rt = set(_words(left)) - _STOP, set(_words(right)) - _STOP
            if len(lt & rt) >= 2 and len(lt.symmetric_difference(rt)) <= 2 and left.strip().lower() != right.strip().lower():
                findings.append(
                    _finding(
                        "keyword_repetition",
                        "FLAG",
                        "Two headings are the same line with a small swap.",
                        offending=f"{left.strip()} / {right.strip()}",
                        policy_reference=_spam("keyword-stuffing"),
                        suggested_fix="Give each heading its own point. Near-duplicate headlines are an editorial spam signal.",
                        extra={"judgment": "near_duplicate_headings"},
                    )
                )
                break
        else:
            continue
        break

    phones = _PHONE.findall(text)
    if len(phones) >= 3:
        findings.append(
            _finding(
                "keyword_stuffing",
                "FLAG",
                "The page lists phone numbers without a reason a reader would need each one.",
                offending=", ".join(phones[:4]),
                policy_reference=_spam("keyword-stuffing"),
                suggested_fix="Keep one real contact path. A list of numbers is the published keyword-stuffing example.",
                extra={
                    "keyword": "phone numbers",
                    "occurrence_count": len(phones),
                    "word_count": word_count,
                    "judgment": "stuffed",
                },
            )
        )

    city = _CITY_LIST.search(text)
    if city:
        block = city.group(0)
        places = [p.strip() for p in block.split(",")]
        findings.append(
            _finding(
                "keyword_stuffing",
                "FLAG",
                "A block lists cities or regions the page is trying to rank for.",
                offending=block,
                policy_reference=_spam("keyword-stuffing"),
                suggested_fix="Describe the area you actually serve in sentences. A city list is a published stuffing example.",
                extra={
                    "keyword": places[0] if places else "locations",
                    "occurrence_count": len(places),
                    "word_count": word_count,
                    "judgment": "stuffed",
                },
            )
        )

    for sentence in _SENTENCE.findall(text):
        tokens = [w for w in _words(sentence) if w not in _STOP and len(w) > 3]
        if not tokens:
            continue
        top = max(set(tokens), key=tokens.count)
        count = tokens.count(top)
        if count >= 4 and len(tokens) <= count * 2:
            findings.append(
                _finding(
                    "keyword_stuffing",
                    "FLAG",
                    (
                        f"“{top}” is repeated {count} times in one sentence, and the sentence has little else in it. "
                        "Google publishes no density number; this matches the unnatural-repetition example."
                    ),
                    offending=_snippet(sentence, 220),
                    policy_reference=_spam("keyword-stuffing"),
                    suggested_fix="Rewrite the sentence so the word appears because the reader needs it.",
                    extra={
                        "keyword": top,
                        "occurrence_count": count,
                        "word_count": word_count,
                        "judgment": "stuffed",
                    },
                )
            )
            break

    return findings


def _house_rule(text: str, rule: str) -> dict[str, Any]:
    parsed = _HOUSE_RULE.search(rule or "")
    words = _words(text)
    content_words = [w for w in words if w not in _STOP and len(w) > 2]
    if not parsed:
        return _finding(
            "house_keyword_rule",
            "NEEDS_REVIEW",
            (
                "Internal house rule (not a Google requirement): "
                f"“{rule}” is set, and this gate only evaluates the form "
                "“no single keyword >N× per M words”. The rule was not applied."
            ),
            offending=rule,
            policy_reference="internal",
            rule_source="internal_house_rule",
            suggested_fix="Restate the house rule as “no single keyword >N× per M words”, or review it by hand.",
        )
    per_block = int(parsed.group(1))
    block = int(parsed.group(2))
    if not content_words or block <= 0:
        return _finding(
            "house_keyword_rule",
            "PASS",
            "Internal house rule (not a Google requirement): there is not enough copy to apply it.",
            policy_reference="internal",
            rule_source="internal_house_rule",
            extra={"word_count": len(words), "judgment": "not_applicable"},
        )
    allowed = per_block * (len(words) / block)
    top = max(set(content_words), key=content_words.count)
    count = content_words.count(top)
    if count > allowed:
        return _finding(
            "house_keyword_rule",
            "FLAG",
            (
                f"Internal house rule (not a Google requirement): “{rule}”. "
                f"“{top}” appears {count} times in {len(words)} words, above the internal cap of {allowed:.1f}."
            ),
            offending=top,
            policy_reference="internal",
            rule_source="internal_house_rule",
            suggested_fix="This limit is the house rule, not a Google cap. Edit the repetition if you want the rule to pass.",
            extra={
                "keyword": top,
                "occurrence_count": count,
                "word_count": len(words),
                "judgment": "house_rule_exceeded",
            },
        )
    return _finding(
        "house_keyword_rule",
        "PASS",
        f"Internal house rule (not a Google requirement): “{rule}” holds. Highest word “{top}” appears {count} times in {len(words)} words.",
        offending=top,
        policy_reference="internal",
        rule_source="internal_house_rule",
        extra={
            "keyword": top,
            "occurrence_count": count,
            "word_count": len(words),
            "judgment": "natural",
        },
    )


def evaluate_content_policy(
    item: dict[str, Any] | None,
    *,
    target_surface: str = TARGET_SURFACE,
    human_in_loop: bool = HUMAN_IN_LOOP,
    house_keyword_rule: str | None = None,
) -> dict[str, Any]:
    """Classify one created item. Does not modify ``item``."""
    item = dict(item or {})
    config = {
        "target_surface": (target_surface or TARGET_SURFACE).strip().lower(),
        "human_in_loop": bool(human_in_loop),
        "house_keyword_rule": (house_keyword_rule or "").strip() or None,
    }
    if config["target_surface"] != TARGET_SURFACE:
        return _unsupported_surface(item, config)

    text = _visible_copy(item)
    raw = _item_text(item)
    keyword = _keyword(item)
    by_area: dict[str, list[dict[str, Any]]] = {area: [] for area in _POLICY_AREAS}

    if not text.strip():
        by_area["editorial_technical"].append(
            _finding(
                "editorial_technical",
                "NEEDS_REVIEW",
                "The draft has no body, so editorial quality and the landing-page claims cannot be assessed.",
                policy_reference=SEARCH_ESSENTIALS_URL,
                rule_source="google_search_essentials",
                suggested_fix="Write the page, then run this gate again. An empty draft is not treated as a pass.",
            )
        )

    for pattern, area, reason, ref, fix in _FAIL_CONTENT:
        hit = pattern.search(raw)
        if hit:
            by_area[area].append(
                _finding(
                    area,
                    "FAIL",
                    reason,
                    offending=hit.group(0),
                    policy_reference=ref,
                    suggested_fix=fix,
                )
            )

    for pattern, reason, ref, fix in _FAIL_PRACTICE:
        hit = pattern.search(raw)
        if hit:
            by_area["prohibited_practices"].append(
                _finding(
                    "prohibited_practices",
                    "FAIL",
                    reason,
                    offending=hit.group(0),
                    policy_reference=ref,
                    suggested_fix=fix,
                )
            )

    for pattern, reason, ref, fix in _FLAG_MISREPRESENTATION:
        hit = pattern.search(text)
        if hit:
            by_area["prohibited_practices"].append(
                _finding(
                    "prohibited_practices",
                    "FLAG",
                    reason,
                    offending=hit.group(0),
                    policy_reference=ref,
                    suggested_fix=fix,
                )
            )

    if re.search(r"\b(enter your password|social security number|ssn)\b", text, re.I):
        by_area["prohibited_practices"].append(
            _finding(
                "prohibited_practices",
                "FLAG",
                "The page asks for a password or a social security number.",
                offending="password or SSN request",
                policy_reference=_spam("malicious-practices"),
                suggested_fix="Do not collect secrets in the article. Unwanted disclosure of private data is a malicious practice.",
            )
        )

    affiliate_hits = len(re.findall(r"\baffiliate\b", text, re.I))
    if affiliate_hits >= 2 and len(_words(text)) < 120:
        by_area["restricted_content"].append(
            _finding(
                "restricted_content",
                "FLAG",
                "Short affiliate copy with no original review or comparison. Search does not ban affiliate pages; thin affiliation does violate the spam policy.",
                offending="affiliate",
                policy_reference=_spam("thin-affiliation"),
                suggested_fix="Add original reporting: price, testing, or a comparison that is not the merchant's description.",
            )
        )
    if re.search(r"\b(reprinted from|copied from|scraped from)\b", text, re.I):
        by_area["restricted_content"].append(
            _finding(
                "restricted_content",
                "FLAG",
                "The draft says the copy was taken from somewhere else.",
                offending="reprinted or copied",
                policy_reference=_spam("scraping"),
                suggested_fix="Replace scraped text with original writing, or cite it and add something the source does not.",
            )
        )

    gimmick = _GIMMICK.search(text)
    if gimmick:
        by_area["editorial_technical"].append(
            _finding(
                "editorial_technical",
                "FLAG",
                "Gimmicky capitalization, dollar signs, or punctuation. Search Essentials asks for people-first wording; this is not a Google Ads character rule.",
                offending=gimmick.group(0),
                policy_reference=SEARCH_ESSENTIALS_URL,
                rule_source="google_search_essentials",
                suggested_fix="Write the line in normal sentence case.",
            )
        )

    hrefs = _MD_LINK.findall(text) + _MD_LINK.findall(str(item.get("html") or ""))
    cta = str(item.get("cta") or "").strip()
    if cta:
        hrefs.append(cta)
    broken = [h for h in hrefs if _BROKEN_HREF.match(h.strip())]
    if broken:
        by_area["editorial_technical"].append(
            _finding(
                "editorial_technical",
                "FLAG",
                "A link or CTA does not go anywhere.",
                offending=broken[0],
                policy_reference=SEARCH_ESSENTIALS_URL,
                rule_source="google_search_essentials",
                suggested_fix="Point the CTA at a real page. A control that does nothing is a quality failure, not a silent edit.",
            )
        )
    if item.get("landing_reachable") is False:
        by_area["editorial_technical"].append(
            _finding(
                "editorial_technical",
                "NEEDS_REVIEW",
                "The landing page could not be reached, so its claims were not confirmed.",
                offending=str(item.get("url") or ""),
                policy_reference=SEARCH_ESSENTIALS_URL,
                rule_source="google_search_essentials",
                suggested_fix="Open the URL and review the page. An unreachable page is not a pass.",
            )
        )

    title = str(item.get("title") or "")
    meta = str(item.get("meta_description") or "")
    by_area["keyword_limits"].append(
        _finding(
            "keyword_limits",
            "PASS",
            (
                "Organic Search/SEO publishes no headline, description, or display-path character cap. "
                "Counts below are informational. Google Ads Responsive Search Ad limits "
                "(headlines 30, descriptions 90, paths 15) are a different surface and were not applied."
            ),
            character_count={"title": len(title), "meta_description": len(meta)},
            policy_reference=SEARCH_ESSENTIALS_URL,
            rule_source="google_search_essentials",
        )
    )

    body = str(item.get("markdown") or item.get("page_markdown") or "")
    for row in _stuffing_and_repetition(body or text, keyword):
        by_area[row["policy_area"]].append(row)

    if not keyword:
        by_area["keyword_content_match"].append(
            _finding(
                "keyword_content_match",
                "NEEDS_REVIEW",
                "No target keyword was supplied, so a bait check cannot be completed.",
                policy_reference=_spam("keyword-stuffing"),
                suggested_fix="Attach the keyword this page is meant to rank for and run the gate again.",
            )
        )
    else:
        hay = text.lower()
        phrase_in_body = keyword in hay
        title_has = keyword in title.lower()
        body_only = (item.get("markdown") or item.get("page_markdown") or "")
        phrase_in_article = keyword in str(body_only).lower()
        if not phrase_in_body:
            by_area["keyword_content_match"].append(
                _finding(
                    "keyword_content_match",
                    "FLAG",
                    f"The target keyword “{keyword}” does not appear in the page content.",
                    offending=keyword,
                    policy_reference=SEARCH_ESSENTIALS_URL,
                    rule_source="google_search_essentials",
                    suggested_fix="Use the words a searcher would type, in the title or the body, only where they describe the page.",
                    extra={"keyword": keyword, "occurrence_count": 0, "word_count": len(_words(text)), "judgment": "mismatch"},
                )
            )
        elif title_has and body_only and not phrase_in_article:
            by_area["keyword_content_match"].append(
                _finding(
                    "keyword_content_match",
                    "FLAG",
                    f"“{keyword}” is in the title and not in the article body.",
                    offending=keyword,
                    policy_reference=SEARCH_ESSENTIALS_URL,
                    rule_source="google_search_essentials",
                    suggested_fix="Make the body answer the query in the title.",
                    extra={"keyword": keyword, "judgment": "title_bait"},
                )
            )

    if config["house_keyword_rule"]:
        house = _house_rule(text, config["house_keyword_rule"])
        by_area.setdefault("house_keyword_rule", []).append(house)

    findings: list[dict[str, Any]] = []
    for area in _POLICY_AREAS:
        rows = by_area[area]
        if rows:
            findings.extend(rows)
        elif area == "keyword_limits":
            continue
        elif area == "prohibited_content":
            findings.append(
                _pass_for_missing(
                    area,
                    "No counterfeit, malware, misleading-generator, or impersonation language from the Search spam policies.",
                    _spam("scam-and-fraud"),
                )
            )
        elif area == "prohibited_practices":
            findings.append(
                _pass_for_missing(
                    area,
                    "No cloaking, hidden text, sneaky redirect, or unverifiable guarantee in the draft.",
                    _spam("cloaking"),
                )
            )
        elif area == "restricted_content":
            findings.append(
                _pass_for_missing(
                    area,
                    (
                        "Google Search spam policies do not require a certification for adult, alcohol, "
                        "gambling, healthcare, financial, or political topics. No thin-affiliation or scraping signal was found."
                    ),
                    _spam("thin-affiliation"),
                )
            )
        elif area == "editorial_technical":
            findings.append(
                _pass_for_missing(
                    area,
                    "No gimmicky punctuation and no dead CTA. The draft itself is the page under review.",
                    SEARCH_ESSENTIALS_URL,
                    "google_search_essentials",
                )
            )
        elif area == "keyword_repetition":
            findings.append(
                _pass_for_missing(
                    area,
                    "No short-block repetition and no near-duplicate headings. Google publishes no repetition cap; none of the published patterns matched.",
                    _spam("keyword-stuffing"),
                )
            )
        elif area == "keyword_stuffing":
            findings.append(
                _pass_for_missing(
                    area,
                    "No city list, phone-number list, or sentence that exists only to repeat a word. Count is evidence and was not used as a density threshold.",
                    _spam("keyword-stuffing"),
                )
            )
        elif area == "keyword_content_match":
            findings.append(
                _pass_for_missing(
                    area,
                    f"“{keyword}” appears in the page content.",
                    SEARCH_ESSENTIALS_URL,
                    "google_search_essentials",
                )
            )
    if config["house_keyword_rule"]:
        findings.extend(by_area.get("house_keyword_rule") or [])

    return _assemble(item, config, findings)


def attach_google_policy(
    draft: dict[str, Any],
    *,
    brief: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store the report on the draft. The written fields are left as they are."""
    brief = brief or {}
    cfg = policy_config_from_settings(config)
    before = {
        "markdown": draft.get("markdown"),
        "title": draft.get("title"),
        "cta": draft.get("cta"),
    }
    item = {
        "item_id": draft.get("url") or brief.get("url") or brief.get("keyword"),
        "url": draft.get("url") or brief.get("url"),
        "keyword": draft.get("keyword") or brief.get("keyword"),
        "title": draft.get("title"),
        "meta_description": draft.get("meta_description"),
        "markdown": draft.get("markdown") or draft.get("page_markdown"),
        "cta": draft.get("cta"),
        "images": draft.get("images"),
        "landing_reachable": draft.get("landing_reachable"),
    }
    draft["google_policy"] = evaluate_content_policy(item, **cfg)
    draft["markdown"] = before["markdown"]
    draft["title"] = before["title"]
    draft["cta"] = before["cta"]
    return draft
