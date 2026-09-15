"""Elementor data model — read, merge, and build Elementor documents.

Why this module exists
----------------------
When Elementor manages a page (``_elementor_edit_mode == "builder"``), the front end
renders from the ``_elementor_data`` JSON tree. ``post_content`` is kept only as a
stripped copy for search/SEO and is **not** what the visitor sees. So writing the
``content`` field of an Elementor page through the REST API returns HTTP 200 and
changes nothing on the live page — the exact "HTTP success is not publishing success"
trap this pipeline has to avoid.

The data model
--------------
``_elementor_data`` is a JSON array of top-level elements. Every element is::

    {"id": "a1b2c3d", "elType": "section"|"container"|"column"|"widget",
     "settings": {...}, "elements": [...], "widgetType": "text-editor"|...}

Classic (pre-3.6) layouts nest ``section > column > widget``. Flexbox containers
(3.6+) nest ``container > widget``. Both are still rendered by current Elementor, so
we build whichever the existing document already uses and default to the classic
section/column form, which every version understands.

Preservation contract
---------------------
We never rewrite a document we did not create. Managed content goes into a single
element carrying a deterministic id derived from a namespace, so a re-run finds and
updates *that* element and leaves every other widget, column, style and setting byte
for byte as it was.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

EDIT_MODE_KEY = "_elementor_edit_mode"
DATA_KEY = "_elementor_data"
VERSION_KEY = "_elementor_version"
TEMPLATE_TYPE_KEY = "_elementor_template_type"
CSS_KEY = "_elementor_css"
PAGE_ASSETS_KEY = "_elementor_page_assets"

BUILDER_MODE = "builder"

# Marker written into the managed widget's settings. Elementor ignores setting keys
# it does not know, and it round-trips through its own save, so this survives a human
# editing the page in the builder afterwards.
MANAGED_FLAG = "_radius_managed"

_MANAGED_NAMESPACE = "radius-os.managed-content"


def managed_id(key: str) -> str:
    """Deterministic 7-hex Elementor element id for a managed slot.

    Elementor ids are 7 lowercase hex characters. Deriving ours from a stable key
    means the second publish updates the element the first publish created instead
    of appending a duplicate section.
    """
    digest = hashlib.sha1(f"{_MANAGED_NAMESPACE}:{key}".encode()).hexdigest()
    return digest[:7]


def is_elementor_post(post: dict[str, Any]) -> bool:
    """True when Elementor renders this post, so post_content will not be shown."""
    meta = post.get("meta") if isinstance(post.get("meta"), dict) else {}
    if str(meta.get(EDIT_MODE_KEY) or "").lower() == BUILDER_MODE:
        return True
    # Some sites expose meta as a list of {key, value} rows.
    if isinstance(post.get("meta"), list):
        for row in post["meta"]:
            if isinstance(row, dict) and row.get("key") == EDIT_MODE_KEY:
                return str(row.get("value") or "").lower() == BUILDER_MODE
    # Fall back to the rendered markup: Elementor stamps its wrapper classes.
    rendered = post.get("content")
    if isinstance(rendered, dict):
        body = str(rendered.get("rendered") or "")
        if 'class="elementor' in body or "elementor-widget-container" in body:
            return True
    return False


def parse_elementor_data(raw: Any) -> list[dict[str, Any]]:
    """Parse ``_elementor_data`` into a list of elements. Never raises.

    Returns ``[]`` for absent data, and also for malformed data — the caller must
    treat "empty" as "do not know the layout" and refuse to overwrite rather than
    assume the page was blank.
    """
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, list):
        return [e for e in parsed if isinstance(e, dict)]
    return []


def dump_elementor_data(tree: list[dict[str, Any]]) -> str:
    """Serialize back to the compact JSON string Elementor stores."""
    return json.dumps(tree, separators=(",", ":"), ensure_ascii=False)


def iter_elements(tree: list[dict[str, Any]]):
    """Depth-first walk over every element in the document."""
    for element in tree:
        if not isinstance(element, dict):
            continue
        yield element
        children = element.get("elements")
        if isinstance(children, list):
            yield from iter_elements(children)


def find_element(tree: list[dict[str, Any]], element_id: str) -> dict[str, Any] | None:
    for element in iter_elements(tree):
        if str(element.get("id") or "") == element_id:
            return element
    return None


def document_uses_containers(tree: list[dict[str, Any]]) -> bool:
    """True when the document is built with flexbox containers rather than sections."""
    for element in tree:
        if str(element.get("elType") or "") == "container":
            return True
    return False


def count_widgets(tree: list[dict[str, Any]]) -> int:
    return sum(1 for e in iter_elements(tree) if str(e.get("elType") or "") == "widget")


# --- builders ---------------------------------------------------------------


def text_widget(html_content: str, *, element_id: str, managed: bool = True) -> dict[str, Any]:
    """An Elementor ``text-editor`` widget holding HTML.

    text-editor is the widget whose settings take raw HTML, which is what makes it
    the correct carrier for generated article markup — as opposed to stuffing HTML
    into a heading or an unrelated widget's fields.
    """
    settings: dict[str, Any] = {"editor": html_content}
    if managed:
        settings[MANAGED_FLAG] = True
    return {
        "id": element_id,
        "elType": "widget",
        "widgetType": "text-editor",
        "settings": settings,
        "elements": [],
    }


def heading_widget(text: str, *, element_id: str, size: str = "h2") -> dict[str, Any]:
    return {
        "id": element_id,
        "elType": "widget",
        "widgetType": "heading",
        "settings": {"title": text, "header_size": size},
        "elements": [],
    }


def wrap_in_section(widgets: list[dict[str, Any]], *, section_id: str, column_id: str) -> dict[str, Any]:
    """Classic ``section > column > widgets``. Understood by every Elementor version."""
    return {
        "id": section_id,
        "elType": "section",
        "settings": {},
        "elements": [
            {
                "id": column_id,
                "elType": "column",
                "settings": {"_column_size": 100, "_inline_size": None},
                "elements": widgets,
            }
        ],
    }


def wrap_in_container(widgets: list[dict[str, Any]], *, container_id: str) -> dict[str, Any]:
    """Flexbox container (Elementor 3.6+), used when the page already uses them."""
    return {
        "id": container_id,
        "elType": "container",
        "settings": {},
        "elements": widgets,
    }


def build_document(
    content_html: str,
    *,
    slot_key: str,
    use_containers: bool = False,
) -> list[dict[str, Any]]:
    """A minimal, valid Elementor document carrying ``content_html``.

    Used for Scenario B — creating a new Elementor page from scratch. Deliberately
    one section with one text widget: the page's design comes from the theme and the
    site's Elementor global styles, and inventing extra structure here would only
    fight them.
    """
    widget_id = managed_id(slot_key)
    widget = text_widget(content_html, element_id=widget_id)
    if use_containers:
        return [wrap_in_container([widget], container_id=managed_id(f"{slot_key}:container"))]
    return [
        wrap_in_section(
            [widget],
            section_id=managed_id(f"{slot_key}:section"),
            column_id=managed_id(f"{slot_key}:column"),
        )
    ]


def merge_managed_content(
    tree: list[dict[str, Any]],
    content_html: str,
    *,
    slot_key: str,
) -> tuple[list[dict[str, Any]], str]:
    """Insert or update managed content inside an existing document.

    Returns ``(new_tree, action)`` where action is ``updated_in_place`` or
    ``appended_section``.

    The existing tree is deep-copied, so a caller that decides not to write leaves
    the original untouched. Only the managed widget is ever modified — every other
    widget, column, section, style and setting is preserved exactly.
    """
    import copy

    new_tree = copy.deepcopy([e for e in tree if isinstance(e, dict)])
    widget_id = managed_id(slot_key)

    existing = find_element(new_tree, widget_id)
    if existing is not None:
        settings = existing.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            existing["settings"] = settings
        settings["editor"] = content_html
        settings[MANAGED_FLAG] = True
        # Keep whatever styling a human applied to this widget; only the copy changes.
        existing["widgetType"] = "text-editor"
        existing["elType"] = "widget"
        return new_tree, "updated_in_place"

    widget = text_widget(content_html, element_id=widget_id)
    if document_uses_containers(new_tree):
        new_tree.append(wrap_in_container([widget], container_id=managed_id(f"{slot_key}:container")))
    else:
        new_tree.append(
            wrap_in_section(
                [widget],
                section_id=managed_id(f"{slot_key}:section"),
                column_id=managed_id(f"{slot_key}:column"),
            )
        )
    return new_tree, "appended_section"


def extract_managed_html(tree: list[dict[str, Any]], *, slot_key: str) -> str | None:
    """The HTML currently stored in the managed widget, if it exists."""
    element = find_element(tree, managed_id(slot_key))
    if element is None:
        return None
    settings = element.get("settings")
    if not isinstance(settings, dict):
        return None
    return str(settings.get("editor") or "")


def validate_document(tree: Any) -> list[str]:
    """Structural problems that would make Elementor fail to render the document."""
    issues: list[str] = []
    if not isinstance(tree, list):
        return ["elementor data is not a list of elements"]
    if not tree:
        return ["elementor data is empty"]

    valid_types = {"section", "column", "container", "widget"}
    for element in iter_elements(tree):
        el_type = str(element.get("elType") or "")
        if el_type not in valid_types:
            issues.append(f"unknown elType '{el_type or '(missing)'}'")
        if not str(element.get("id") or "").strip():
            issues.append(f"element of type '{el_type}' has no id")
        if el_type == "widget" and not str(element.get("widgetType") or "").strip():
            issues.append("widget element has no widgetType")
        if not isinstance(element.get("settings", {}), dict):
            issues.append(f"element '{element.get('id')}' has non-object settings")

    ids = [str(e.get("id") or "") for e in iter_elements(tree) if e.get("id")]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        issues.append(f"duplicate element ids: {', '.join(sorted(duplicates)[:4])}")
    return issues[:8]


def build_meta_payload(
    tree: list[dict[str, Any]],
    *,
    elementor_version: str | None = None,
    template_type: str = "wp-page",
) -> dict[str, Any]:
    """The meta keys that must be written together for Elementor to render the tree.

    ``_elementor_css`` is deliberately **not** set: it is a generated cache, and the
    correct way to invalidate it is to leave it for Elementor to regenerate. Writing
    a stale value there is how pages end up rendering with the previous layout's CSS.
    """
    payload: dict[str, Any] = {
        DATA_KEY: dump_elementor_data(tree),
        EDIT_MODE_KEY: BUILDER_MODE,
        TEMPLATE_TYPE_KEY: template_type,
    }
    if elementor_version:
        payload[VERSION_KEY] = elementor_version
    return payload
