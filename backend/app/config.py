import os
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
    # Main reasoning for skills (Architecture v1.9 Step 06/08 — Claude Sonnet)
    skill_model: str = "claude-sonnet-5"
    # Competitor Research is forced to Gemini (Architecture v1.9 common skill)
    competitor_model: str = "google/gemini-2.5-pro"
    # OpenRouter Perplexity Pro for URL research / CDD extraction
    research_model: str = "perplexity/sonar-pro"
    # Nano Banana (Gemini 2.5 Flash Image) via OpenRouter Images API
    image_model: str = "google/gemini-2.5-flash-image"
    # Article drafts (Phase 10 create-content) via OpenRouter
    write_model: str = "openai/gpt-5.6-sol"
    # Lightweight OpenRouter model for post-cleaning keyword cluster + intent/funnel
    keyword_cluster_model: str = "google/gemini-2.5-flash"
    keyword_relevance_max_per_seed: int = 60
    keyword_relevance_max_input_per_seed: int = 80
    keyword_max_seeds: int = 50
    keyword_pool_target: int = 4500
    keyword_relevance_max_tokens: int = 2048
    keyword_cluster_use_llm: bool = False
    use_mock_llm: bool = True
    use_mock_providers: bool = True

    # Pre-clustering live site scan (Phase 5, before clustering finalizes and
    # before Phase 6b URL mapping runs): HTTP crawl first, Playwright only as
    # a fallback for pages that come back thin/JS-shell, Perplexity (via
    # OpenRouter) only if the crawl is still thin overall after that.
    enable_playwright_rendering: bool = True
    live_site_scan_max_pages: int = 40

    ahrefs_api_key: str = ""
    semrush_api_key: str = ""
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    moz_api_key: str = ""
    # Ahrefs Site Audit (Phase 7) — project resolved by URL when unset
    ahrefs_site_audit_project_id: int | None = None
    # Max pages from page-explorer per audit (1000 per API call @ 50 units each).
    # 0 = issues-only (fastest — skip bulk page inventory).
    ahrefs_site_audit_page_limit: int = 0
    # Sample affected URLs for top N issues only (50 units per issue call)
    ahrefs_site_audit_issue_sample_limit: int = 3
    # Optional — Google PageSpeed Insights works unauthenticated at a low quota.
    # Set this to raise the rate limit for Phase 7's cwv-measurement skill.
    pagespeed_api_key: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    oauth_redirect_uri: str = "http://localhost:8000/api/v1/oauth/callback"
    frontend_url: str = "http://localhost:5173"

    # --- Phase 12: design fetch, preview, and CMS publish -------------------------
    brandfetch_api_key: str = ""
    firecrawl_api_key: str = ""
    # Context.dev computed styles + screenshots for the publish preview.
    # Blank means the local CSS walk still runs; paid readers are skipped.
    context_dev_api_key: str = ""

    # Each client connects their own WordPress site + Application Password from the
    # app (stored encrypted per-client, see app.api.integrations) — there is no
    # deployment-wide WordPress site. These two remain global because they are safety
    # policy, not a credential: they cap what ANY client connection is allowed to do.
    # Posts are created as drafts unless a caller explicitly asks to go live AND the
    # deployment opts in below. Publishing to a client's live site is not reversible
    # from here, so "draft" is the only safe default.
    wordpress_default_status: str = "draft"
    wordpress_allow_live_publish: bool = False

    competitor_cache_days: int = 14
    client_memory_retention_days: int = 30
    client_memory_retention_enabled: bool = True
    readiness_threshold: float = 90.0
    # 0 = no soft cap — take every page discovered (hard safety ceiling still applies)
    seo_audit_max_pages: int = 0
    # Phase 7 composite fallback when Ahrefs Site Audit is unavailable
    technical_seo_seo_audit_max_pages: int = 40
    # Default chat-turn budget; heavy phases override below
    agent_timeout_seconds: int = 480
    technical_seo_agent_timeout_seconds: int = 900
    search_demand_agent_timeout_seconds: int = 720
    # Phase 3 is intentionally bounded for interactive chat. A larger crawl can
    # be requested later through the dedicated crawl/audit tools.
    website_situation_max_pages: int = 40
    website_operation_timeout_seconds: int = 75

    feature_discovery_agent: bool = True
    feature_tracking_agent: bool = True
    feature_website_agent: bool = True
    feature_competitor_agent: bool = True
    feature_search_demand_agent: bool = True
    feature_content_strategy_agent: bool = True
    feature_site_architecture_agent: bool = True
    feature_technical_seo_agent: bool = True
    feature_content_audit_agent: bool = True
    feature_content_planning_agent: bool = True
    feature_content_production_agent: bool = True
    feature_on_page_seo_agent: bool = True
    feature_publishing_agent: bool = True
    # Post-phase AI + deterministic validation (company-aware QC)
    feature_phase_validation: bool = True
    # Max generate→validate cycles before escalating needs_revision to reject
    validation_max_attempts: int = 2

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # When true, skip login/JWT and open the app as a single demo operator
    auth_disabled: bool = False

    # "development" (default) seeds demo users/client; "production" skips them
    environment: str = "development"

    # Directory of built SPA assets (set in production Docker image)
    static_dir: str = ""

    # --- Operational dead man's switch (maintenance lock if check-ins stop) ---
    # Off by default. When enabled, POST /api/v1/ops/dead-man-switch/check-in
    # must be called with DEAD_MAN_SWITCH_TOKEN within max_days or API locks.
    dead_man_switch_enabled: bool = False
    dead_man_switch_token: str = ""
    dead_man_switch_max_days: int = 14

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

    @model_validator(mode="after")
    def _use_railway_public_origin_when_urls_are_local(self) -> "Settings":
        """Railway injects RAILWAY_PUBLIC_DOMAIN. Use it only when public URLs are still localhost.

        Local development has no Railway domain, so this does not change local URLs.
        An explicitly set production FRONTEND_URL / CORS / OAuth redirect is left alone.
        """
        if self.environment != "production":
            return self
        domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip().rstrip("/")
        if not domain or "://" in domain:
            return self
        origin = f"https://{domain}"

        def _local(url: str) -> bool:
            return "localhost" in url or "127.0.0.1" in url

        if _local(self.frontend_url):
            self.frontend_url = origin
        if _local(self.oauth_redirect_uri):
            self.oauth_redirect_uri = f"{origin}/api/v1/oauth/callback"
        if _local(self.cors_origins):
            kept = [
                item.strip()
                for item in self.cors_origins.split(",")
                if item.strip() and not _local(item)
            ]
            self.cors_origins = ",".join([origin, *kept])
        return self

    @model_validator(mode="after")
    def _warn_on_localhost_frontend_url_in_production(self) -> "Settings":
        # A misconfigured FRONTEND_URL doesn't break anything at runtime — it
        # only silently corrupts robots.txt/sitemap.xml's URLs — so this warns
        # instead of failing startup. Import kept local to avoid a module-level
        # logging_config <-> config import cycle at collection time.
        if self.environment == "production" and (
            "localhost" in self.frontend_url or "127.0.0.1" in self.frontend_url
        ):
            from app.logging_config import get_logger

            get_logger("config").warning(
                "frontend_url_looks_like_localhost_in_production",
                frontend_url=self.frontend_url,
                hint="robots.txt/sitemap.xml will emit this URL — set FRONTEND_URL to the real public domain",
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
