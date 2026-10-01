from app.services.keyword_focus import apply_keyword_focus, drop_focused_items


def test_deselect_removes_keyword_and_manual_competitor_is_kept():
    summary = {
        "keyword_table": [
            {"keyword": "seo agency melbourne", "volume": 400},
            {"keyword": "cheap seo", "volume": 90},
        ],
        "clusters": [
            {
                "name": "SEO",
                "primary_keyword": "cheap seo",
                "keywords": [
                    {"keyword": "cheap seo"},
                    {"keyword": "seo agency melbourne"},
                ],
            }
        ],
        "seed_clusters": [
            {
                "seed": "seo",
                "phrase": [{"keyword": "cheap seo", "volume": 90}],
            }
        ],
    }
    updated = apply_keyword_focus(
        summary,
        {
            "deselected_keywords": ["Cheap SEO"],
            "manual_competitor_keywords": [
                {"keyword": "king kong seo", "competitor": "kingkong.co"},
            ],
        },
    )
    table = [row["keyword"] for row in updated["keyword_table"]]
    assert table[0] == "king kong seo"
    assert "cheap seo" not in table
    assert "seo agency melbourne" in table
    assert updated["clusters"][0]["keywords"] == [{"keyword": "seo agency melbourne"}]
    assert updated["clusters"][0]["primary_keyword"] is None
    assert updated["seed_clusters"][0]["phrase"] == []
    assert updated["manual_competitor_keywords"] == [
        {"keyword": "king kong seo", "competitor": "kingkong.co"}
    ]
    assert updated["deselected_keywords"] == ["cheap seo"]


def test_stage_deselect_drops_a_url_and_remembers_it():
    summary = {
        "pages": [
            {"url": "https://example.com/keep", "title": "Keep"},
            {"url": "https://example.com/drop", "title": "Drop me"},
        ],
        "notes": ["leave this"],
    }
    updated = drop_focused_items(summary, {"https://example.com/drop"})
    urls = [row["url"] for row in updated["pages"]]
    assert urls == ["https://example.com/keep"]
    assert "https://example.com/drop" in updated["deselected_items"]
    assert updated["notes"] == ["leave this"]
