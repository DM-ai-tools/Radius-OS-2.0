"""SEMrush Analytics client: CSV parsing and quota circuit breaker."""

import pytest

from app.integrations import semrush


@pytest.fixture(autouse=True)
def _reset_block():
    semrush.reset_keywords_explorer_block()
    yield
    semrush.reset_keywords_explorer_block()


def test_parse_keyword_csv_and_country_map():
    text = "Keyword;Search Volume;CPC;Keyword Difficulty\nseo services;1200;4.50;42\n"
    rows = semrush._parse_csv(text)
    parsed = semrush._keyword_row(rows[0], endpoint="phrase_these")
    assert parsed["keyword"] == "seo services"
    assert parsed["volume"] == 1200
    assert parsed["cpc"] == 4.5
    assert parsed["difficulty"] == 42
    assert parsed["source"] == "semrush"
    assert semrush.database_for("gb") == "uk"


@pytest.mark.asyncio
async def test_units_exhausted_blocks_later_calls(monkeypatch):
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "use_mock_providers", False)
    monkeypatch.setattr(settings, "semrush_api_key", "test-key", raising=False)

    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = "ERROR 132 :: API UNITS BALANCE IS ZERO"

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            calls["n"] += 1
            assert "key" not in (params or {})
            assert headers.get("key") == "test-key"
            return _Resp()

    monkeypatch.setattr(semrush.httpx, "AsyncClient", lambda timeout=None: _Client())

    rows, errs = await semrush.keyword_overview(["seo services"], country="au")
    assert rows == []
    assert "semrush_units_exhausted" in errs
    assert semrush.keywords_explorer_blocked() == "semrush_units_exhausted"

    rows2, errs2 = await semrush.organic_keywords("example.com", country="au")
    assert rows2 == []
    assert "semrush_units_exhausted" in errs2
    assert calls["n"] == 1
