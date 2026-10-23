"""Competitor discovery must accept the replies models actually send."""

from unittest.mock import AsyncMock, patch

from app.agents.competitor import _discovery_competitor_inputs
from app.integrations import providers
from app.integrations.providers import extract_competitor_rows


def test_extracts_json_object_wrapped_in_prose():
    text = (
        "Here is the set.\n"
        '{"competitors":[{"name":"King Kong","url":"https://kingkong.co","rationale":"peer"}]}\n'
        "Note: scores use {similarity}."
    )
    rows = extract_competitor_rows(text)
    assert rows[0]["name"] == "King Kong"
    assert rows[0]["url"] == "https://kingkong.co"


def test_extracts_website_key_and_markdown_links():
    rows = extract_competitor_rows(
        '{"items":[{"company":"WebFX","website":"webfx.com"}]}'
    )
    assert rows == [{"name": "WebFX", "url": "webfx.com"}]

    linked = extract_competitor_rows(
        "Peers include [Mindshare](https://www.mindshareworld.com) and others."
    )
    assert linked[0]["url"] == "https://www.mindshareworld.com"


def test_directories_and_social_hosts_are_not_competitors():
    from app.integrations.web_fetch import is_non_business_host, site_is_dead

    assert is_non_business_host("www.clutch.co")
    assert is_non_business_host("https://www.linkedin.com/company/acme")
    assert is_non_business_host("g2.com")
    assert not is_non_business_host("kingkong.com.au")
    assert site_is_dead({"status_code": 0, "error": "dns_resolution_failed"})
    assert site_is_dead({"status_code": 200, "error": "parked_or_placeholder"})
    assert not site_is_dead({"status_code": 403, "error": "bot_challenge_blocked"})
    assert not site_is_dead({"status_code": 200, "error": None, "text": "<html>ok</html>"})


def test_unrelated_industry_is_excluded_from_the_competitor_set():
    from app.ml.tier_competitors import keep_relevant_competitors

    peer = {
        "name": "King Kong",
        "url": "https://kingkong.com.au",
        "tier": 2,
        "tier_name": "Strong Direct Competitors",
        "parameters": {"industry_specialization": {"score": 8}},
    }
    directory = {
        "name": "Clutch",
        "url": "https://clutch.co",
        "tier": 2,
        "tier_name": "Strong Direct Competitors",
        "parameters": {"industry_specialization": {"score": 3}},
    }
    relevant, excluded = keep_relevant_competitors([peer, directory])
    assert [row["name"] for row in relevant] == ["King Kong"]
    assert excluded[0]["tier"] == 5
    assert "keyword" in excluded[0]["recommendation"]


def test_directory_domains_do_not_seed_keyword_research():
    from app.services.competitive_context import competitor_domains

    domains = competitor_domains(
        [
            {"name": "King Kong", "url": "https://kingkong.com.au"},
            {"name": "Clutch", "domain": "clutch.co"},
            {"name": "LinkedIn", "url": "https://www.linkedin.com/company/acme"},
        ]
    )
    assert domains == ["kingkong.com.au"]


def test_discovery_field_splits_urls_and_names():
    seeds, hints = _discovery_competitor_inputs(
        "King Kong https://kingkong.co\nWebFX\nhttps://www.webfx.com"
    )
    assert [s["url"] for s in seeds] == ["https://kingkong.co", "https://www.webfx.com"]
    assert hints == ["WebFX"]


async def test_a_short_first_pass_asks_for_competitors_it_does_not_already_have():
    """The follow-up used to be written before anyone was found, so it said
    'already have 0' and the model repeated the same three. Deduping that
    reply left the research stage stuck at three competitors."""
    prompts: list[str] = []

    async def _chat(*, system, user, model, max_tokens):
        prompts.append(user)
        if len(prompts) == 1:
            return (
                '{"competitors":['
                '{"name":"Alpha","url":"https://alpha-peer.com"},'
                '{"name":"Beta","url":"https://beta-peer.com"},'
                '{"name":"Gamma","url":"https://gamma-peer.com"}'
                "]}"
            )
        return (
            '{"competitors":['
            '{"name":"Delta","url":"https://delta-peer.com"},'
            '{"name":"Echo","url":"https://echo-peer.com"},'
            '{"name":"Foxtrot","url":"https://foxtrot-peer.com"}'
            "]}"
        )

    settings = providers.get_settings()
    previous = settings.use_mock_providers
    settings.use_mock_providers = False
    try:
        with (
            patch("app.integrations.llm._openrouter_chat", _chat),
            patch(
                "app.integrations.web_fetch.fetch_url",
                AsyncMock(return_value={"status_code": 200, "text": "<html></html>", "url": "https://x"}),
            ),
        ):
            rows = await providers.discover_competitors(
                "Click Trends", "clicktrends.example", industry="SEO"
            )
    finally:
        settings.use_mock_providers = previous

    assert len(prompts) >= 2
    assert "https://alpha-peer.com" in prompts[1]
    assert "already have (0)" not in prompts[1].lower()
    assert {row["url"] for row in rows} == {
        "https://alpha-peer.com",
        "https://beta-peer.com",
        "https://gamma-peer.com",
        "https://delta-peer.com",
        "https://echo-peer.com",
        "https://foxtrot-peer.com",
    }
