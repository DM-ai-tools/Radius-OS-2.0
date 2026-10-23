"""Close variants: different wordings of the same search, folded into one keyword.

"seo agency", "agency seo", "seo agencies", "agency for seo" and "seo
optimisation agency" are one search. Google Ads reports a single grouped volume
for them, which is why a keyword table showed the same 4,400 five times. They
count once: the clearest wording is kept, and the others are listed on it as
``close_variants`` so nothing disappears without a record.

Two keywords are close variants when, after these steps, they have the same
set of words:

- lowercase; drop filler words ("for", "in", "and", "of", "the", ...);
- singular form ("agencies" -> "agency");
- UK and US spelling treated alike ("optimisation" -> "optimization");
- acronyms and their expansions treated alike ("search engine optimization"
  -> "seo", "pay per click" -> "ppc"), and "optimization" next to "seo" is
  redundant ("seo optimization agency" = "seo agency").

Word order is ignored. Nothing else is folded: "seo agency melbourne" and
"seo agency" stay apart, as do "seo audit" and "seo audit tool".

One more case is Google's own grouping. Google Ads reports one volume for a
whole bucket of close variants, and DataForSEO passes it on: "perth seo", "seo
marketing perth" and "search engine optimisation perth wa" all came back as
2,900/mo, $39.30 CPC, KD 3. Identical volume, CPC (to the cent) and difficulty
is that bucket, not three searches. Such rows fold too, but only when one
wording's words contain the other's, so the folded wording adds detail and no
new meaning. Rows that share the numbers without that overlap ("googlebot seo"
and "google seo marketing") are not folded; each kept row gets
``volume_shared_with`` so the shared figure is not read as separate demand.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

FILLER = frozenset({
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "with", "at", "by", "from", "into",
})
_PROVIDER_WORDS = frozenset({"agency", "company", "service", "consultant", "expert", "specialist", "firm"})
_ACRONYMS = (
    ("search engine optimization", "seo"),
    ("search engine optimisation", "seo"),
    ("search engine marketing", "sem"),
    ("pay per click", "ppc"),
    ("pay-per-click", "ppc"),
    ("conversion rate optimization", "cro"),
    ("conversion rate optimisation", "cro"),
)
# Spellings that differ between UK/AU and US English but mean the same.
_SPELLING = {
    "colour": "color", "behaviour": "behavior", "favour": "favor", "flavour": "flavor",
    "honour": "honor", "labour": "labor", "centre": "center", "metre": "meter",
    "catalogue": "catalog", "programme": "program", "licence": "license", "defence": "defense",
}
# Once "seo" is in the keyword, these add no meaning ("seo optimization").
_REDUNDANT_WITH = {"seo": frozenset({"optimization", "optimize"}), "ppc": frozenset({"advertising"})}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _spelling(word: str) -> str:
    if word in _SPELLING:
        return _SPELLING[word]
    for uk, us in (("isation", "ization"), ("ising", "izing"), ("ised", "ized"), ("yse", "yze")):
        if word.endswith(uk) and len(word) > len(uk) + 2:
            return word[: -len(uk)] + us
    if word.endswith("ise") and len(word) > 6:
        return word[:-3] + "ize"
    return word


def variant_tokens(keyword: str) -> frozenset[str]:
    """The keyword's words after the folding rules above."""
    text = _norm(keyword).replace("-", " ")
    for phrase, acronym in _ACRONYMS:
        text = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", acronym, text)
    words = [_spelling(_singular(w)) for w in re.findall(r"[a-z0-9]+", text) if w not in FILLER]
    tokens = set(words)
    for anchor, redundant in _REDUNDANT_WITH.items():
        if anchor in tokens:
            tokens -= redundant
    return frozenset(tokens)


def variant_key(keyword: str) -> str:
    """Equal keys mean the same search worded differently."""
    return " ".join(sorted(variant_tokens(keyword)))


def _volume(row: dict[str, Any]) -> int | None:
    v = row.get("volume")
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def representative_rank(row: dict[str, Any]) -> tuple:
    """Sort key for the wording to keep. Highest measured volume first (each
    wording keeps its own volume). On a tie: the seed's exact match, then the
    wording that follows the seed's word order ("seo audit for website" over
    "audit seo website"), then one not starting with "agency"/"company"...,
    then fewer filler words, singular, shortest."""
    kw = _norm(row.get("keyword"))
    words = re.findall(r"[a-z0-9]+", kw)
    volume = _volume(row)
    seed_words = re.findall(r"[a-z0-9]+", _norm(row.get("seed")))
    follows_seed = bool(seed_words) and words[: len(seed_words)] == seed_words
    return (
        -(volume if volume is not None else -1),
        0 if row.get("match_class") == "exact" else 1,
        # Within a Google-grouped bucket, the core wording ("perth seo") over a
        # padded one ("seo marketing perth"); close variants all tie here.
        len(variant_tokens(kw)),
        0 if follows_seed else 1,
        1 if words and _singular(words[0]) in _PROVIDER_WORDS else 0,
        sum(1 for w in words if w in FILLER),
        sum(1 for w in words if _singular(w) != w),
        len(kw),
        kw,
    )


def _grouped_volume_signature(row: dict[str, Any]) -> tuple[int, float, float] | None:
    """Google's grouped-bucket fingerprint: measured volume, CPC and KD together."""
    volume = _volume(row)
    cpc = row.get("cpc")
    kd = row.get("difficulty")
    if volume is None or volume < 20 or row.get("volume_unverified") or row.get("volume_estimated"):
        return None
    if not isinstance(cpc, (int, float)) or isinstance(cpc, bool) or cpc <= 0:
        return None
    if not isinstance(kd, (int, float)) or isinstance(kd, bool):
        return None
    return volume, round(float(cpc), 2), float(kd)


def _merge_grouped_volumes(
    groups: dict[str, list[dict[str, Any]]],
    order: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Join variant groups that Google reports as one volume bucket (module doc).

    Two groups join when they share a signature and one group's words contain
    the other's. Returns the surviving group order and, for groups that share a
    signature without that overlap, the other keywords sharing their figure.
    """
    by_signature: dict[tuple[int, float, float], list[str]] = {}
    for k in order:
        signature = _grouped_volume_signature(groups[k][0])
        if signature is not None:
            by_signature.setdefault(signature, []).append(k)
    parent = {k: k for k in order}

    def root(k: str) -> str:
        while parent[k] != k:
            k = parent[k]
        return k

    for keys in by_signature.values():
        if len(keys) < 2:
            continue
        tokens = {k: variant_tokens(str(groups[k][0].get("keyword") or "")) for k in keys}
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if tokens[a] and tokens[b] and (tokens[a] <= tokens[b] or tokens[b] <= tokens[a]):
                    ra, rb = root(a), root(b)
                    if ra != rb:
                        parent[rb] = ra
    survivors: list[str] = []
    for k in order:
        r = root(k)
        if r != k:
            groups[r].extend(groups.pop(k))
        else:
            survivors.append(k)
    shared: dict[str, list[str]] = {}
    for keys in by_signature.values():
        alive = list(dict.fromkeys(root(k) for k in keys))
        if len(alive) < 2:
            continue
        names = {k: [str(m.get("keyword")) for m in groups[k]] for k in alive}
        for k in alive:
            shared[k] = [n for other in alive if other != k for n in names[other]]
    return survivors, shared


def collapse_close_variants(
    rows: Iterable[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], str] | None = None,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Fold close variants. Returns (kept rows, [(folded row, kept row), ...]).

    Kept rows are copies carrying ``close_variants`` (the other wordings, best
    first) when anything folded into them. Order follows each group's first
    appearance, so callers' existing sort order survives.
    """
    group_key = key or (lambda r: variant_key(str(r.get("keyword") or "")))
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        k = group_key(row) or _norm(row.get("keyword"))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(row)
    shared: dict[str, list[str]] = {}
    if key is None:
        order, shared = _merge_grouped_volumes(groups, order)
    kept: list[dict[str, Any]] = []
    folded: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for k in order:
        members = sorted(groups[k], key=representative_rank)
        best = dict(members[0])
        others = [m for m in members[1:] if _norm(m.get("keyword")) != _norm(best.get("keyword"))]
        if others:
            prior = [v for v in best.get("close_variants") or [] if v]
            names = prior + [str(m.get("keyword")) for m in others]
            for m in others:
                names += [v for v in m.get("close_variants") or [] if v]
            seen: set[str] = {_norm(best.get("keyword"))}
            best["close_variants"] = [n for n in names if not (_norm(n) in seen or seen.add(_norm(n)))]
            folded.extend((m, best) for m in others)
        if shared.get(k):
            best["volume_shared_with"] = shared[k]
        kept.append(best)
    return kept, folded
