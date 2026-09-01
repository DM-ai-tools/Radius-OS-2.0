from app.integrations.web_fetch import _normalize_page_url, is_indexable_html_url


def test_keeps_real_pages():
    assert is_indexable_html_url("https://example.com/")
    assert is_indexable_html_url("https://example.com/about")
    assert is_indexable_html_url("https://example.com/blog/how-we-work")
    assert is_indexable_html_url("https://example.com/2024/01/15/real-post")
    assert is_indexable_html_url("https://example.com/services/seo")


def test_drops_utility_and_archives():
    assert not is_indexable_html_url("https://example.com/tag/seo")
    assert not is_indexable_html_url("https://example.com/author/kushal")
    assert not is_indexable_html_url("https://example.com/feed")
    assert not is_indexable_html_url("https://example.com/blog/page/2")
    assert not is_indexable_html_url("https://example.com/cart")
    assert not is_indexable_html_url("https://example.com/wp-json/wp/v2/posts")
    assert not is_indexable_html_url("https://example.com/logo.png")


def test_normalize_collapses_slash_www_and_query():
    a = _normalize_page_url(
        "https://www.example.com/about/?utm_source=x",
        prefer_netloc="example.com",
        prefer_scheme="https",
    )
    b = _normalize_page_url(
        "http://example.com/about/",
        prefer_netloc="example.com",
        prefer_scheme="https",
    )
    assert a == b == "https://example.com/about"


def test_keeps_query_permalinks_distinct():
    a = _normalize_page_url("https://example.com/?p=12")
    b = _normalize_page_url("https://example.com/?p=99")
    c = _normalize_page_url("https://example.com/?utm_source=x")
    assert a != b
    assert c == "https://example.com/"
