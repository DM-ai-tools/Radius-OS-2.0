"""HTML signals shared by the technical audit and the live on-page fetch.

Image checks read tags already in the HTML. They do not fetch extra URLs.
AI-crawler notes read a robots.txt body the technical audit already downloaded.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_IMG_TAG = re.compile(r"<img\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(
    r"""([:\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""",
    re.IGNORECASE,
)
_RASTER_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
_AI_CRAWLERS = ("GPTBot", "Google-Extended", "PerplexityBot", "ClaudeBot")


def _attrs(raw: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in _ATTR.finditer(raw or ""):
        key = match.group(1).lower()
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if value is None:
            value = match.group(4) or ""
        found[key] = value
    return found


def _raster_path(src: str) -> bool:
    path = urlparse(src or "").path.lower()
    return any(path.endswith(suffix) for suffix in _RASTER_SUFFIXES)


def inspect_images(html: str | None) -> dict[str, Any]:
    """Alt, dimensions, raster format, and hero srcset from one HTML document."""
    if html is None:
        return {
            "fetched": False,
            "findings": [],
            "images": [],
            "note": "Page was not fetched — image checks were not run.",
        }

    findings: list[dict[str, Any]] = []
    lines: list[str] = []
    tags = list(_IMG_TAG.finditer(html))
    for index, match in enumerate(tags):
        attrs = _attrs(match.group(1))
        src = attrs.get("src") or ""
        issues: list[str] = []
        alt = attrs.get("alt")
        if alt is None or not str(alt).strip():
            issues.append("missing alt")
        if not str(attrs.get("width") or "").strip() or not str(attrs.get("height") or "").strip():
            issues.append("missing width and height")
        if _raster_path(src):
            issues.append("raster format is not WebP or AVIF")
        if index == 0 and not str(attrs.get("srcset") or "").strip():
            issues.append("first image missing srcset")
        if not issues:
            continue
        label = src or "(no src)"
        findings.append({"index": index, "src": src, "issues": issues})
        lines.append(f"{label}: {', '.join(issues)}")

    if not tags:
        note = "Fetched HTML; no images found."
    elif not findings:
        note = "Fetched HTML; no image issues."
    else:
        note = f"Fetched HTML; {len(findings)} image issue(s)."
    return {"fetched": True, "findings": findings, "images": lines, "note": note}


def ai_crawler_notes(robots_text: str) -> list[str]:
    """Informational robots.txt lines for AI crawlers. This is not a GEO audit."""
    text = robots_text or ""
    if not text.strip():
        return []
    blocks = re.split(r"(?im)^user-agent:\s*", text)
    by_agent: dict[str, str] = {}
    for block in blocks[1:]:
        lines = block.splitlines()
        agent = (lines[0] if lines else "").strip().lower()
        if not agent:
            continue
        by_agent[agent] = "\n".join(lines[1:])
    notes: list[str] = []
    for name in _AI_CRAWLERS:
        body = by_agent.get(name.lower())
        if body is None:
            notes.append(
                f"{name}: not mentioned in robots.txt (informational; full GEO is Phase 17)"
            )
        elif re.search(r"(?im)^disallow:\s*/\s*$", body):
            notes.append(f"{name}: Disallow: / (informational; full GEO is Phase 17)")
        else:
            notes.append(
                f"{name}: present without a blanket disallow (informational; full GEO is Phase 17)"
            )
    return notes
