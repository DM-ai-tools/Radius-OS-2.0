"""Per-service competitor selection.

Competitors are **reusable and client-scoped**: one ``CompetitorProfile`` row per
domain per client, which Phase 4 already populates and the content-signal layer
already reads. This module adds the many-to-many link between those competitors
and the services in ``commercial_scope.service_prioritization.service_catalog``.

The link is stored as JSON rather than a join table because services themselves
are not rows — they are catalog entries keyed by a stable slug inside that same
blob, so a table with a foreign key to a JSON slug would be half-normalised and
would drift whenever the catalog is rebuilt. Shape:

    commercial_scope.service_prioritization.service_competitors = {
        "<service_id>": ["<competitor_uuid>", ...],
    }

A service with no entry has no competitors, which is the default and must always
generate content unchanged.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.logging_config import get_logger

log = get_logger("service_competitors")

LINK_KEY = "service_competitors"

# Deliberately permissive on TLD (new gTLDs keep appearing) but strict on shape:
# at least one dot, no spaces, no scheme-only or path-only input.
_DOMAIN_RE = re.compile(
    r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$"
)


class InvalidCompetitorUrl(ValueError):
    """The supplied website is not a usable domain or URL."""


def normalize_domain(raw: Any) -> str:
    """Bare registrable host from a domain or URL. Raises on anything unusable.

    Accepts "example.com", "https://www.example.com/services/", "EXAMPLE.com.".
    Rejects "", "not a domain", "localhost", "http://", "/services".
    """
    text = str(raw or "").strip().lower()
    if not text:
        raise InvalidCompetitorUrl("A competitor website is required.")
    if "://" in text:
        host = urlparse(text).hostname or ""
    else:
        # Strip a path or query the user pasted without a scheme.
        host = text.split("/")[0].split("?")[0]
    host = host.strip().strip(".").removeprefix("www.")
    if not host or " " in host or not _DOMAIN_RE.match(host):
        raise InvalidCompetitorUrl(
            f"{raw!r} is not a valid website. Use a domain like example.com "
            "or a full URL."
        )
    return host


def competitor_domain(row: Any) -> str:
    """Domain for a stored competitor record, without raising on bad legacy data."""
    for field in ("domain", "url", "website"):
        value = (row or {}).get(field) if isinstance(row, dict) else getattr(row, field, None)
        if not value:
            continue
        try:
            return normalize_domain(value)
        except InvalidCompetitorUrl:
            continue
    return ""


def read_links(commercial: dict[str, Any] | None) -> dict[str, list[str]]:
    """The whole service → competitor-id map, tolerant of missing/old shapes."""
    prio = dict((commercial or {}).get("service_prioritization") or {})
    raw = prio.get(LINK_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for service_id, ids in raw.items():
        key = str(service_id or "").strip()
        if not key or not isinstance(ids, list):
            continue
        # Preserve order, drop blanks and duplicates.
        out[key] = list(dict.fromkeys(str(i).strip() for i in ids if str(i).strip()))
    return out


def competitor_ids_for_service(
    commercial: dict[str, Any] | None, service_id: str
) -> list[str]:
    return read_links(commercial).get(str(service_id or "").strip(), [])


def write_links(
    commercial: dict[str, Any], links: dict[str, list[str]]
) -> dict[str, Any]:
    """Return a copy of commercial_scope with the link map replaced.

    Writes through ``service_prioritization`` without disturbing its other keys —
    that blob also holds the selection the operator confirmed before Phase 5.
    """
    updated = dict(commercial or {})
    prio = dict(updated.get("service_prioritization") or {})
    prio[LINK_KEY] = {
        str(k): list(dict.fromkeys(str(i) for i in v))
        for k, v in (links or {}).items()
        if str(k).strip()
    }
    updated["service_prioritization"] = prio
    return updated


def set_service_competitors(
    commercial: dict[str, Any],
    *,
    service_id: str,
    competitor_ids: list[str],
    known_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Replace one service's competitor selection.

    Returns ``(commercial, warnings)``. Duplicates are collapsed rather than
    rejected — the UI can send the same id twice from a double click and that is
    not an error. Ids the client does not own are dropped with a warning, so a
    stale browser tab cannot attach another client's competitor.
    """
    key = str(service_id or "").strip()
    if not key:
        raise ValueError("service_id is required.")

    warnings: list[str] = []
    wanted = list(dict.fromkeys(str(i).strip() for i in (competitor_ids or []) if str(i).strip()))
    if len(wanted) != len([i for i in (competitor_ids or []) if str(i).strip()]):
        warnings.append("Duplicate competitors were collapsed.")

    if known_ids is not None:
        unknown = [i for i in wanted if i not in known_ids]
        if unknown:
            warnings.append(
                f"{len(unknown)} competitor(s) are not on this client and were ignored."
            )
            wanted = [i for i in wanted if i in known_ids]

    links = read_links(commercial)
    if wanted:
        links[key] = wanted
    else:
        links.pop(key, None)
    return write_links(commercial, links), warnings


def detach_competitor_everywhere(
    commercial: dict[str, Any], competitor_id: str
) -> dict[str, Any]:
    """Drop a competitor from every service — used when the row itself is deleted."""
    target = str(competitor_id or "").strip()
    if not target:
        return dict(commercial or {})
    links = {
        service_id: [i for i in ids if i != target]
        for service_id, ids in read_links(commercial).items()
    }
    return write_links(commercial, {k: v for k, v in links.items() if v})


def service_ids_in_catalog(commercial: dict[str, Any] | None) -> set[str]:
    """Every service id the catalog knows, including subservices."""
    prio = dict((commercial or {}).get("service_prioritization") or {})
    ids: set[str] = set()
    for entry in prio.get("service_catalog") or []:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or "").strip()
        if sid:
            ids.add(sid)
        for sub in entry.get("subservices") or []:
            if isinstance(sub, dict):
                sub_id = str(sub.get("id") or "").strip()
                if sub_id:
                    ids.add(sub_id)
    return ids


def resolve_service_competitors(
    commercial: dict[str, Any] | None,
    *,
    service_id: str,
    competitor_rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """The competitor records selected for one service, in the operator's order.

    Rows are shaped for the competitors content-signal, with ``confirmed`` forced
    True: an explicit per-service pick IS the operator confirming it, whatever
    the row's Phase 4 state was.
    """
    wanted = competitor_ids_for_service(commercial, service_id)
    if not wanted:
        return []
    by_id = {
        str(row.get("id")): row
        for row in (competitor_rows or [])
        if isinstance(row, dict) and row.get("id")
    }
    out: list[dict[str, Any]] = []
    for cid in wanted:
        row = by_id.get(cid)
        if not row:
            continue
        domain = competitor_domain(row)
        if not domain:
            continue
        out.append(
            {
                "id": cid,
                "name": str(row.get("name") or domain).strip(),
                "domain": domain,
                "url": str(row.get("url") or f"https://{domain}"),
                "notes": str(row.get("notes") or row.get("positioning_cluster") or "").strip(),
                "source": str(row.get("source") or "").strip() or "manual",
                "confirmed": True,
                "selected_for_service": service_id,
            }
        )
    return out
