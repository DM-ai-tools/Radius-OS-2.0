"""Normalize Ahrefs Site Audit and crawl payloads into TechnicalSEOPage."""

from __future__ import annotations

from typing import Any

from app.services.technical_seo_schemas import TechnicalSEOPage


def _first_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        for item in value:
            s = str(item).strip()
            if s:
                return s
        return None
    s = str(value).strip()
    return s or None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def normalize_ahrefs_page(row: dict[str, Any]) -> TechnicalSEOPage | None:
    url = str(row.get("url") or "").strip()
    if not url:
        return None
    internal = row.get("internal_links")
    internal_count: int | None = None
    if isinstance(internal, list):
        internal_count = len(internal)
    elif internal is not None:
        internal_count = _int_or_none(internal)

    return TechnicalSEOPage(
        url=url,
        status_code=_int_or_none(row.get("http_code")),
        indexable=row.get("compliant") if isinstance(row.get("compliant"), bool) else None,
        canonical_url=_first_str(row.get("canonical")),
        canonical_status_code=_int_or_none(row.get("canonical_code")),
        title=_first_str(row.get("title")),
        meta_description=_first_str(row.get("meta_description")),
        h1=_first_str(row.get("h1")),
        depth=_int_or_none(row.get("depth")),
        internal_link_count=internal_count,
        word_count=_int_or_none(row.get("content_length") or row.get("page_raw_text_length")),
        is_redirect_loop=row.get("is_redirect_loop")
        if isinstance(row.get("is_redirect_loop"), bool)
        else None,
        final_redirect_url=_first_str(row.get("final_redirect")),
        duplicate_title_count=_int_or_none(row.get("duplicate_title")),
        source="ahrefs",
        raw=row,
    )


def normalize_crawl_page(row: dict[str, Any]) -> TechnicalSEOPage | None:
    url = str(row.get("url") or row.get("page_url") or "").strip()
    if not url:
        return None
    return TechnicalSEOPage(
        url=url,
        status_code=_int_or_none(row.get("status_code") or row.get("status")),
        title=_first_str(row.get("title")),
        meta_description=_first_str(row.get("meta_description")),
        h1=_first_str(row.get("h1")),
        depth=_int_or_none(row.get("depth")),
        source="crawl",
        raw=row,
    )
