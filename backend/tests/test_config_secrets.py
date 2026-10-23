import pytest
from pydantic import ValidationError

from app.config import Settings

KNOWN_BAD_ENCRYPTION_KEYS = [
    "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVzIQ==",  # config.py hardcoded default
    "change-me-fernet-key-base64-32bytes==",  # .env.example placeholder
]
KNOWN_BAD_SECRET_KEYS = [
    "dev-secret",  # config.py hardcoded default
    "change-me-in-production-use-a-long-random-string",  # .env.example placeholder
]


@pytest.mark.parametrize("bad_key", KNOWN_BAD_ENCRYPTION_KEYS)
def test_rejects_known_bad_encryption_key(bad_key, monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", bad_key)
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize("bad_key", KNOWN_BAD_SECRET_KEYS)
def test_rejects_known_bad_secret_key(bad_key, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", bad_key)
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_production_uses_railway_public_domain_when_urls_are_localhost(monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "radius-os.up.railway.app")
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    settings = Settings(
        _env_file=None,
        environment="production",
        frontend_url="http://localhost:5173",
        oauth_redirect_uri="http://localhost:8000/api/v1/oauth/callback",
        cors_origins="http://localhost:5173,http://127.0.0.1:5173",
    )
    assert settings.frontend_url == "https://radius-os.up.railway.app"
    assert settings.oauth_redirect_uri == "https://radius-os.up.railway.app/api/v1/oauth/callback"
    assert settings.cors_origins == "https://radius-os.up.railway.app"


def test_explicit_production_url_is_not_replaced_by_railway_domain(monkeypatch):
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "radius-os.up.railway.app")
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    settings = Settings(
        _env_file=None,
        environment="production",
        frontend_url="https://app.example.com",
        oauth_redirect_uri="https://app.example.com/api/v1/oauth/callback",
        cors_origins="https://app.example.com",
    )
    assert settings.frontend_url == "https://app.example.com"
    assert settings.oauth_redirect_uri == "https://app.example.com/api/v1/oauth/callback"


def test_local_postgres_url_is_unchanged_for_asyncpg():
    from app.config import asyncpg_engine_settings

    url = "postgresql+asyncpg://searchfit:searchfit@127.0.0.1:5432/searchfit"
    cleaned, connect_args = asyncpg_engine_settings(url)
    assert cleaned == url
    assert connect_args == {}


def test_railway_public_postgres_url_is_accepted_by_asyncpg():
    from app.config import asyncpg_engine_settings

    cleaned, connect_args = asyncpg_engine_settings(
        "postgresql+asyncpg://u:p@example.proxy.rlwy.net:12345/railway?sslmode=require&channel_binding=require"
    )
    assert "sslmode" not in cleaned
    assert "channel_binding" not in cleaned
    assert cleaned.startswith("postgresql+asyncpg://u:p@example.proxy.rlwy.net:12345/railway")
    assert "ssl" in connect_args


def test_accepts_a_real_looking_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    Settings(_env_file=None)  # must not raise
