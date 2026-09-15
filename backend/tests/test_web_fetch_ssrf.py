from app.integrations.web_fetch import _is_public_host, assert_safe_url


def test_rejects_loopback():
    assert _is_public_host("127.0.0.1") is False


def test_rejects_cloud_metadata_address():
    assert _is_public_host("169.254.169.254") is False


def test_rejects_private_range():
    assert _is_public_host("10.0.0.5") is False


def test_rejects_none_hostname():
    assert _is_public_host(None) is False


def test_accepts_public_ip_literal():
    assert _is_public_host("8.8.8.8") is True


# assert_safe_url() — the pre-check used by ad-hoc httpx callers that bypass
# fetch_url() (AUDIT-003/004: providers.py's broken-link checker and on-page
# optimizer, wordpress.py's request layer).


def test_assert_safe_url_blocks_loopback():
    ok, reason = assert_safe_url("http://127.0.0.1/admin")
    assert ok is False
    assert reason == "blocked_unsafe_host"


def test_assert_safe_url_blocks_cloud_metadata():
    ok, reason = assert_safe_url("http://169.254.169.254/latest/meta-data/")
    assert ok is False
    assert reason == "blocked_unsafe_host"


def test_assert_safe_url_rejects_non_http_scheme():
    ok, reason = assert_safe_url("file:///etc/passwd")
    assert ok is False
    assert reason == "unsupported_scheme"


def test_assert_safe_url_accepts_public_ip_literal():
    ok, target = assert_safe_url("http://8.8.8.8/status")
    assert ok is True
    assert target == "http://8.8.8.8/status"


def test_assert_safe_url_adds_scheme_when_missing():
    ok, target = assert_safe_url("8.8.8.8/status")
    assert ok is True
    assert target == "https://8.8.8.8/status"
