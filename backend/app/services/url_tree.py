"""L1 to L4 tree for the URL map (2026-10-27).

The client's service catalog is the skeleton: each service is an L1 node, its
sub-services L2, and deeper catalog levels L3 and L4. Sub-topics the keywords show
add levels under it ("shopify" under SEO makes Shopify SEO). A level exists only
when keywords back it, so depth follows the keywords: a row stops at L1, L2 or L3
when nothing narrower was searched.

How a row is placed:

1. Its primary keyword, with the modifier phrases removed (``MODIFIER_WORDS``:
   best, cost, agency, near me, how to...), is reduced to a concept key
   (``service_taxonomy.concept_key``: stems, spelling synonyms, no stop words or
   provider words). A node fits when one of its names (catalog name or alias) has
   a key inside the row's key. The deepest fitting node wins.
2. The qualifier is what is left of the row's key once the words of the node and
   of every node above it are removed. A row with no qualifier sits on the node.
3. A qualifier becomes a child node one level deeper (never deeper than 4) when at
   least ``min_subtopic_keywords`` clusters on that node share it, or one of them
   has a measured search volume (an estimate is not a measurement).
4. A qualifier goes under a deeper node than the one the primary names only when
   the row's own cluster says so: "shopify seo" goes under Ecommerce SEO when its
   cluster holds a keyword with both, such as "shopify ecommerce seo". Another
   cluster's keywords never move it. Without such a keyword Shopify SEO is its own
   branch of SEO; the tree does not guess.
5. Informational rows, and supporting rows that have no page of their own
   (``dedicated_url`` False), attach to the deepest node they fit and never make a
   node. A supporting row that fits none sits on its parent row's node.
6. The home row (``is_home``) sits above the tree and gets no levels. A row that
   fits no node goes to ``needs_review`` with a reason, keeps the levels it had,
   and is flagged for review. It is never dropped and never placed twice.

``violations`` lists broken invariants (a level below an empty one, a cluster on
more than one row, a cluster neither placed nor listed). They are warnings in the
Phase 6 validation, never a block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.service_taxonomy import concept_key
from app.services.shared_keyword_pool.location import MODIFIER_WORDS
from app.services.url_mapping import _first_folder, _named_city, _set_priority, _sheet_page_type

MAX_DEPTH = 4
LEVEL_KEYS = ("l1_category", "l2_subcategory", "l3_subsubcategory", "l4_attribution")

# Phrases first ("near me" before "me" could ever matter), each matched as a whole phrase.
_MODIFIER_PATTERNS = tuple(
    re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])")
    for phrase in sorted(MODIFIER_WORDS, key=lambda p: (-len(p), p))
)


def _without_modifiers(text: Any) -> str:
    low = " ".join(str(text or "").lower().split())
    for pattern in _MODIFIER_PATTERNS:
        low = pattern.sub(" ", low)
    return " ".join(low.split())


def _key(text: Any) -> frozenset[str]:
    return concept_key(_without_modifiers(text))


@dataclass(eq=False)
class _Node:
    label: str
    keys: tuple[frozenset[str], ...]
    level: int
    parent: _Node | None
    order: int
    reason: str = ""

    def path(self) -> list[_Node]:
        chain: list[_Node] = []
        node: _Node | None = self
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain[::-1]

    def fit(self, key: frozenset[str]) -> frozenset[str] | None:
        """The largest of this node's names that the key contains."""
        inside = [k for k in self.keys if k <= key]
        return max(inside, key=len) if inside else None

    def words(self) -> frozenset[str]:
        out: set[str] = set()
        for node in self.path():
            for k in node.keys:
                out |= k
        return frozenset(out)

    def is_below(self, other: _Node) -> bool:
        return other is not self and other in self.path()


@dataclass(eq=False)
class _Row:
    entry: dict[str, Any]
    primary: str
    key: frozenset[str]
    informational: bool
    measured: bool
    # No page of its own: it folds into, or is held for, a parent page.
    supporting: bool = False
    # Keywords of this row's own cluster that contain its whole key and more.
    wider: list[tuple[frozenset[str], str]] = field(default_factory=list)

    @property
    def attaches_only(self) -> bool:
        """Joins the node it fits; never makes one or follows a linking keyword."""
        return self.informational or self.supporting


def _measured(entry: dict[str, Any]) -> bool:
    status = entry.get("search_volume_status")
    if status is not None:
        return status == "measured"
    volume = entry.get("search_volume")
    return isinstance(volume, (int, float)) and not isinstance(volume, bool) and volume > 0


def _shown_words(primary: str, qualifier: frozenset[str]) -> str:
    """The primary's own words behind a qualifier, for the decision trail (not the stems)."""
    words = [w for w in _without_modifiers(primary).split() if concept_key(w) & qualifier]
    return ", ".join(dict.fromkeys(words)) or ", ".join(sorted(qualifier))


def _catalog_nodes(service_catalog: list[dict[str, Any]] | None) -> list[_Node]:
    nodes: list[_Node] = []

    def add(item: Any, parent: _Node | None, level: int) -> None:
        if level > MAX_DEPTH or not isinstance(item, dict):
            return
        name = " ".join(str(item.get("name") or "").split())
        taxonomy = item.get("taxonomy") if isinstance(item.get("taxonomy"), dict) else {}
        names = [name, *(a for a in taxonomy.get("aliases") or [] if isinstance(a, str))]
        keys = tuple(dict.fromkeys(k for k in (_key(n) for n in names if n) if k))
        if not name or not keys:
            return
        node = _Node(label=name, keys=keys, level=level, parent=parent, order=len(nodes))
        nodes.append(node)
        for sub in item.get("subservices") or []:
            add(sub, node, level + 1)

    for service in service_catalog or []:
        add(service, None, 1)
    return nodes


def _anchor(
    row: _Row, nodes: list[_Node], *, follow_keywords: bool
) -> tuple[_Node | None, frozenset[str], str | None, list[_Node]]:
    """The node a row sits on or under, its qualifier, the keyword that linked a deeper
    node (if any), and other nodes that fit equally well."""
    fits = [(node, matched) for node in nodes if (matched := node.fit(row.key))]
    if not fits:
        return None, frozenset(), None, []

    def rank(pair: tuple[_Node, frozenset[str]]) -> tuple[int, int, int]:
        return (pair[0].level, len(pair[1]), -pair[0].order)

    best = max(fits, key=rank)
    ties = [n for n, m in fits if n is not best[0] and rank((n, m))[:2] == rank(best)[:2]]
    node = best[0]
    qualifier = row.key - node.words()
    via = None
    if qualifier and follow_keywords and node.level < MAX_DEPTH and row.wider:
        linked = None
        for deeper in nodes:
            # The row must stay narrower than the linked node: "shopify seo" goes under
            # Ecommerce SEO, never onto a node such as Shopify SEO Audit.
            if deeper.level >= MAX_DEPTH or not deeper.is_below(node) or not row.key - deeper.words():
                continue
            hits = [text for key, text in row.wider if any(k <= key for k in deeper.keys)]
            if hits:
                score = (deeper.level, len(hits), -deeper.order)
                if linked is None or score > linked[0]:
                    linked = (score, deeper, hits[0])
        if linked is not None:
            node, via, ties = linked[1], linked[2], []
            qualifier = row.key - node.words()
    return node, qualifier, via, ties


def _derived_node(
    parent: _Node,
    members: list[tuple[_Row, str | None, list[_Node]]],
    *,
    order: int,
    min_subtopic_keywords: int,
) -> _Node:
    from app.services.topic_naming import phrase_title

    rows = [row for row, _, _ in members]
    key = frozenset.intersection(*(row.key for row in rows))
    names = sorted({_without_modifiers(row.primary) for row in rows}, key=lambda t: (len(t.split()), t))
    label = phrase_title(names[0])
    if len(rows) >= min_subtopic_keywords:
        why = f"{len(rows)} clusters show it"
    else:
        why = "a measured search volume backs it"
    reason = f"'{label}' is a sub-topic of {parent.label}: {why}."
    linked = next((via for _, via, _ in members if via), None)
    if linked:
        reason += f" It sits under {parent.label} because its own cluster holds '{linked}'."
    ties = list(dict.fromkeys(t.label for _, _, found in members for t in found))
    if ties:
        reason += f" Its keywords also fit {', '.join(ties)}; the one listed first wins."
    return _Node(label=label, keys=(key,), level=parent.level + 1, parent=parent, order=order, reason=reason)


def _grow(rows: list[_Row], nodes: list[_Node], *, min_subtopic_keywords: int) -> None:
    """Add the sub-topic nodes the keywords back, narrowest qualifiers first."""
    made: set[tuple[int, frozenset[str]]] = set()
    for _ in range(len(rows) * MAX_DEPTH + 1):
        groups: dict[tuple[int, frozenset[str]], tuple[_Node, list[tuple[_Row, str | None, list[_Node]]]]] = {}
        for row in rows:
            if row.attaches_only:
                continue
            node, qualifier, via, ties = _anchor(row, nodes, follow_keywords=True)
            if node is None or not qualifier or node.level >= MAX_DEPTH:
                continue
            groups.setdefault((id(node), qualifier), (node, []))[1].append((row, via, ties))
        ready = [
            (gid, node, members)
            for gid, (node, members) in groups.items()
            if gid not in made
            and (len(members) >= min_subtopic_keywords or any(row.measured for row, _, _ in members))
        ]
        if not ready:
            return
        # "shopify seo" becomes a node before "shopify seo audit", which then nests under it.
        narrowest = min(len(gid[1]) for gid, _, _ in ready)
        for gid, node, members in ready:
            if len(gid[1]) != narrowest:
                continue
            made.add(gid)
            nodes.append(
                _derived_node(node, members, order=len(nodes), min_subtopic_keywords=min_subtopic_keywords)
            )


def _place(row: _Row, node: _Node, notes: list[str]) -> None:
    entry = row.entry
    path = [n.label for n in node.path()]
    for index, column in enumerate(LEVEL_KEYS):
        entry[column] = path[index] if index < len(path) else None
    entry["level"] = len(path)
    folder = _first_folder(entry)
    kind = None
    if folder in {"brands", "brand"}:
        kind = "brand"
    elif folder in {"locations", "location"} or _named_city(row.primary):
        kind = "landing"
    entry["page_type"] = _sheet_page_type({**{k: entry.get(k) for k in LEVEL_KEYS}, "sheet_kind": kind})
    _set_priority(entry)
    line = f"URL tree: {' > '.join(path)}."
    if node.reason:
        line += f" {node.reason}"
    entry.setdefault("decision_trail", []).append(" ".join([line, *notes]))


def _violations(entries: list[dict[str, Any]], placed: set[int], review: set[int]) -> list[str]:
    out: list[str] = []
    counts: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("primary_keyword") or entry.get("cluster")
        if entry.get("is_home"):
            ids = {str(x) for x in [entry.get("cluster_id"), *(entry.get("merged_cluster_ids") or [])] if x}
            if any(entry.get(k) for k in LEVEL_KEYS):
                out.append(f"The home row '{name}' carries levels; it sits above the tree.")
        else:
            ids = {str(entry["cluster_id"])} if entry.get("cluster_id") else set()
            for upper, lower in zip(LEVEL_KEYS, LEVEL_KEYS[1:]):
                if entry.get(lower) and not entry.get(upper):
                    deep, shallow = LEVEL_KEYS.index(lower) + 1, LEVEL_KEYS.index(upper) + 1
                    out.append(f"Row '{name}' ({entry.get('cluster_id')}) has L{deep} without L{shallow}.")
            if id(entry) not in placed and id(entry) not in review:
                out.append(f"Cluster {entry.get('cluster_id')} ('{name}') is not placed and not listed for review.")
        for cid in ids:
            counts[cid] = counts.get(cid, 0) + 1
    for cid, n in counts.items():
        if n > 1:
            out.append(f"Cluster {cid} is placed more than once ({n} rows).")
    return out


def apply_url_tree(
    entries: list[dict[str, Any]],
    *,
    service_catalog: list[dict[str, Any]],
    min_subtopic_keywords: int = 2,
) -> dict[str, Any]:
    """Set L1 to L4 and ``level`` on each row from the catalog and the keywords.

    Returns ``{"placed", "needs_review", "violations"}``. With no usable catalog the
    rows are left exactly as they are and the report is empty.
    """
    nodes = _catalog_nodes(service_catalog)
    if not nodes:
        return {"placed": 0, "needs_review": [], "violations": []}

    rows: list[_Row] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("is_home"):
            continue
        primary = " ".join(str(entry.get("primary_keyword") or "").split())
        rows.append(
            _Row(
                entry=entry,
                primary=primary,
                key=_key(primary),
                informational=str(entry.get("search_intent") or "").lower() == "informational",
                measured=_measured(entry),
                supporting=entry.get("dedicated_url") is False,
            )
        )
    for row in rows:
        own: dict[frozenset[str], str] = {}
        for text in [*(row.entry.get("secondary_keywords") or []), *(row.entry.get("secondary_keywords_sheet") or [])]:
            key = _key(text)
            if key and row.key and row.key < key:
                own.setdefault(key, " ".join(str(text).split()))
        row.wider = list(own.items())

    minimum = max(1, int(min_subtopic_keywords))
    _grow(rows, nodes, min_subtopic_keywords=minimum)

    placed: set[int] = set()
    review: set[int] = set()
    needs_review: list[dict[str, Any]] = []
    node_of: dict[str, _Node] = {}
    # Supporting rows last, so the parent row's node is known when one fits nothing.
    for row in sorted(rows, key=lambda r: r.supporting):
        node, qualifier, _via, ties = _anchor(row, nodes, follow_keywords=not row.attaches_only)
        notes: list[str] = []
        if node is None and row.supporting:
            parent_id = str(row.entry.get("parent_cluster_id") or row.entry.get("supporting_parent_id") or "")
            node = node_of.get(parent_id)
            if node is not None:
                notes.append("It supports another row and names no service itself, so it sits on that row's node.")
        if node is None:
            reason = (
                f"No service or sub-service in the catalog, and no sub-topic under one, "
                f"is named in the primary keyword '{row.primary}'."
            )
            needs_review.append(
                {"cluster_id": row.entry.get("cluster_id"), "primary_keyword": row.primary, "reason": reason}
            )
            row.entry["needs_human_review"] = True
            row.entry.setdefault("decision_trail", []).append(f"URL tree: needs review. {reason}")
            review.add(id(row.entry))
            continue
        if qualifier and not row.attaches_only:
            words = _shown_words(row.primary, qualifier)
            if node.level >= MAX_DEPTH:
                notes.append(f"Depth is capped at {MAX_DEPTH}; the narrower words ({words}) stay on this row.")
            else:
                notes.append(
                    f"The narrower words ({words}) are not a sub-topic yet: "
                    f"fewer than {minimum} clusters show them and none has a measured volume."
                )
        if ties:
            notes.append(f"It also fits {', '.join(t.label for t in ties)}; the one listed first wins.")
        _place(row, node, notes)
        placed.add(id(row.entry))
        if row.entry.get("cluster_id"):
            node_of.setdefault(str(row.entry["cluster_id"]), node)

    return {
        "placed": len(placed),
        "needs_review": needs_review,
        "violations": _violations(entries, placed, review),
    }
