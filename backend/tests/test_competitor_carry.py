from app.agents.competitor import (
    MIN_COMPETITORS_TARGET,
    _is_placeholder_competitor,
    _merge_competitor_candidates,
    _usable_competitor_url,
)


def test_category_labels_without_url_are_not_usable():
    item = {"url": None, "name": "Digital marketing agencies in Melbourne"}
    assert _usable_competitor_url(item) == ""
    assert _is_placeholder_competitor(item) is True


def test_real_competitor_url_is_usable():
    item = {"url": "https://wearecapalaba.com.au", "name": "We Are Capalaba"}
    assert _usable_competitor_url(item) == "https://wearecapalaba.com.au"
    assert _is_placeholder_competitor(item) is False


def test_bare_domain_is_normalized():
    assert _usable_competitor_url({"url": "rival.com.au", "name": "Rival"}) == "https://rival.com.au"


def test_merge_competitor_candidates_seeds_first_then_discovers():
    seeds = [
        {"name": "A", "url": "https://a.com"},
        {"name": "B", "url": "https://b.com"},
        {"name": "C", "url": "https://c.com"},
    ]
    discovered = [
        {"name": "D", "url": "https://d.com"},
        {"name": "A duplicate", "url": "https://a.com"},
        {"name": "E", "url": "e.com"},
    ]
    merged = _merge_competitor_candidates(seeds, discovered)
    assert [c["url"] for c in merged] == [
        "https://a.com",
        "https://b.com",
        "https://c.com",
        "https://d.com",
        "https://e.com",
    ]
    assert merged[0]["source"] == "discovery_override"
    assert merged[3]["source"] == "search_visibility"


def test_thin_discovery_seed_triggers_supplement_threshold():
    assert MIN_COMPETITORS_TARGET == 6
    seeds = [{"name": f"C{i}", "url": f"https://c{i}.com"} for i in range(3)]
    discovered = [{"name": f"D{i}", "url": f"https://d{i}.com"} for i in range(8)]
    merged = _merge_competitor_candidates(seeds, discovered)
    assert len(merged) == 11
    assert len([c for c in merged if c["source"] == "discovery_override"]) == 3
