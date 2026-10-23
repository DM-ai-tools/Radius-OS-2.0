"""Images in a preview: type sniffing for attached files, and repeated-photo checks.

The site's own images keep their absolute addresses in the delivered page; only the
draft's generated images (which have no public address) are embedded by the agent.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MAX_IMAGE_BYTES = 8 * 1024 * 1024
# Small data URIs are icons; a repeat of those is not a repeated photo.
ICON_DATA_URI_CHARS = 4000

_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
    "image/avif": (b"\x00\x00\x00",),
}


def sniff_image_type(content: bytes) -> str | None:
    """The image type the bytes really are, when it is an allowed raster type."""
    for mime, magics in _MAGIC.items():
        if any(content.startswith(m) for m in magics):
            if mime == "image/webp" and content[8:12] != b"WEBP":
                continue
            if mime == "image/avif" and b"ftypavif" not in content[:32]:
                continue
            return mime
    return None


# ------------------------------------------------------------- repeated photos

_LOOP_COPY_RE = re.compile(r"aria-hidden\s*=\s*[\"']true|clone|marquee|ticker|swiper-slide-duplicate|slick-cloned|splide__slide--clone", re.IGNORECASE)
_LOGO_RE = re.compile(r"logo", re.IGNORECASE)
_RESIZE_PARAMS = {"w", "h", "width", "height", "resize", "fit", "q", "quality", "auto", "format", "fm", "dpr", "crop"}


def _normalize_src(src: str) -> str:
    """Same picture at another size counts as the same picture."""
    if src.startswith("data:"):
        return "sha1:" + hashlib.sha1(src.encode()).hexdigest()
    parsed = urlparse(src)
    query = [(k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _RESIZE_PARAMS]
    path = re.sub(r"-\d{2,4}x\d{2,4}(?=\.\w+$)", "", parsed.path)  # WordPress size suffix
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", urlencode(query), ""))


def find_repeated_images(html: str) -> list[dict[str, Any]]:
    """Photos used more than once. Logos, SVGs, icons and carousel loop copies are ignored."""
    seen: dict[str, dict[str, Any]] = {}
    for tag in re.finditer(r"<img\b[^>]*>", html, re.IGNORECASE):
        attrs = tag.group(0)
        src_m = re.search(r"""\bsrc\s*=\s*["']([^"']+)["']""", attrs, re.IGNORECASE)
        if not src_m:
            continue
        src = src_m.group(1)
        if src.startswith("data:image/svg") or src.lower().split("?")[0].endswith(".svg"):
            continue
        if src.startswith("data:") and len(src) < ICON_DATA_URI_CHARS:
            continue
        if _LOGO_RE.search(attrs) or _LOOP_COPY_RE.search(attrs):
            continue
        key = _normalize_src(src)
        alt_m = re.search(r"""\balt\s*=\s*["']([^"']*)["']""", attrs, re.IGNORECASE)
        entry = seen.setdefault(key, {"src": src if not src.startswith("data:") else "embedded image", "count": 0, "alts": []})
        entry["count"] += 1
        if alt_m and alt_m.group(1) and alt_m.group(1) not in entry["alts"]:
            entry["alts"].append(alt_m.group(1))
    return [v for v in seen.values() if v["count"] > 1]
