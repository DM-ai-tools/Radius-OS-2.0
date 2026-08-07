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


def test_accepts_a_real_looking_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "a-fine-secret-key-for-this-test-only")
    monkeypatch.setenv("ENCRYPTION_KEY", "a-fine-encryption-key-for-this-test-only")
    Settings(_env_file=None)  # must not raise
