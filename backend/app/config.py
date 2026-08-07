from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_KNOWN_BAD_ENCRYPTION_KEYS = {
    "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVzIQ==",  # this file's own old default
    "change-me-fernet-key-base64-32bytes==",  # .env.example placeholder
}
_KNOWN_BAD_SECRET_KEYS = {
    "dev-secret",  # this file's own old default
    "change-me-in-production-use-a-long-random-string",  # .env.example placeholder
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://searchfit:searchfit@127.0.0.1:5432/searchfit"
    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str = "redis://127.0.0.1:6379/1"

    secret_key: str = "dev-secret"
    encryption_key: str = "dGVzdC1lbmNyeXB0aW9uLWtleS0zMmJ5dGVzIQ=="
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    # anthropic | openrouter — skill synthesis (competitor analysis, discovery research, etc.)
    llm_provider: str = "anthropic"
    router_model: str = "claude-haiku-4-5"
    skill_model: str = "claude-sonnet-5"
    # OpenRouter Perplexity Pro for URL research / CDD extraction
    research_model: str = "perplexity/sonar-pro"
    use_mock_llm: bool = True
    use_mock_providers: bool = True

    ahrefs_api_key: str = ""
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    moz_api_key: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8000/api/v1/oauth/callback"
    frontend_url: str = "http://localhost:5173"

    competitor_cache_days: int = 14
    readiness_threshold: float = 90.0
    # 0 = no soft cap — take every page discovered (hard safety ceiling still applies)
    seo_audit_max_pages: int = 0

    feature_discovery_agent: bool = True
    feature_tracking_agent: bool = True
    feature_website_agent: bool = True
    feature_competitor_agent: bool = True

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # When true, skip login/JWT and open the app as a single demo operator
    auth_disabled: bool = False

    # "development" (default) seeds demo users/client; "production" skips them
    environment: str = "development"

    # Directory of built SPA assets (set in production Docker image)
    static_dir: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def effective_seo_audit_max_pages(self) -> int:
        """Pages to collect for SEO audit. 0/unset = unlimited (safety ceiling only)."""
        # Absolute ceiling avoids runaway memory/timeouts on huge sites / LLM payloads
        ceiling = 2000
        n = int(self.seo_audit_max_pages or 0)
        if n <= 0:
            return ceiling
        return min(n, ceiling)

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: object) -> object:
        """Accept Railway/Heroku postgres:// URLs and force asyncpg driver."""
        if not isinstance(v, str) or not v:
            return v
        url = v.strip()
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+asyncpg" not in url.split("://", 1)[0]:
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @model_validator(mode="after")
    def _reject_known_bad_secrets(self) -> "Settings":
        if self.encryption_key in _KNOWN_BAD_ENCRYPTION_KEYS:
            raise ValueError(
                "ENCRYPTION_KEY is set to a known placeholder value. "
                "Generate a real one: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        if self.secret_key in _KNOWN_BAD_SECRET_KEYS:
            raise ValueError(
                "SECRET_KEY is set to a known placeholder value. "
                "Generate a real one: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
