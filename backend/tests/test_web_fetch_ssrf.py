from app.integrations.web_fetch import _is_public_host


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
