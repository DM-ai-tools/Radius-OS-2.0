"""Draft images in the rendered draft HTML keep an absolute address."""

from __future__ import annotations

from app.services.publish_preview import markdown_to_content_html


def test_markdown_draft_images_use_the_media_base():
    html = markdown_to_content_html(
        "## Hello\n\n[FIGURE hero]\n",
        images=[{"role": "hero", "src": "/media/drafts/demo.png", "alt": "Demo"}],
        media_base="http://localhost:5173",
    )
    assert "http://localhost:5173/media/drafts/demo.png" in html
