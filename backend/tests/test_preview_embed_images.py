"""Downloaded site preview HTML must embed images as data URIs."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.services.publish_preview import (
    _draft_media_filename,
    embed_preview_images,
    markdown_to_content_html,
)


def test_draft_media_filename_extracts_basename():
    assert _draft_media_filename("/media/drafts/hero.png") == "hero.png"
    assert _draft_media_filename("http://localhost:5173/media/drafts/hero.png") == "hero.png"
    assert _draft_media_filename("data:image/png;base64,abc") is None
    assert _draft_media_filename("/media/drafts/../secret.png") is None


@pytest.mark.asyncio
async def test_embed_preview_images_inlines_local_draft_files(tmp_path, monkeypatch):
    raw = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"
    (tmp_path / "hero.png").write_bytes(raw)
    monkeypatch.setattr("app.integrations.llm.draft_images_dir", lambda: Path(tmp_path))

    html = '<html><body><img src="/media/drafts/hero.png" alt="Hero"></body></html>'
    out = await embed_preview_images(html)
    assert "data:image/png;base64," in out
    assert "/media/drafts/hero.png" not in out
    encoded = base64.b64encode(raw).decode("ascii")
    assert encoded in out


@pytest.mark.asyncio
async def test_markdown_then_embed_keeps_images_offline(tmp_path, monkeypatch):
    raw = b"png-bytes"
    (tmp_path / "demo.png").write_bytes(raw)
    monkeypatch.setattr("app.integrations.llm.draft_images_dir", lambda: Path(tmp_path))

    html = markdown_to_content_html(
        "## Hello\n\n[FIGURE hero]\n",
        images=[{"role": "hero", "src": "/media/drafts/demo.png", "alt": "Demo"}],
        media_base="http://localhost:5173",
    )
    assert "http://localhost:5173/media/drafts/demo.png" in html
    embedded = await embed_preview_images(html)
    assert "data:image/png;base64," in embedded
    assert "/media/drafts/demo.png" not in embedded
