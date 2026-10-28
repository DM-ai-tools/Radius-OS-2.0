import logging
import os
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    # Skills always use Anthropic directly (see llm._skill_chat).
    # OpenRouter is for research (Perplexity), image generation, and the site preview layout.
    llm_provider: str = "anthropic"
    router_model: str = "claude-haiku-4-5"
    # Main reasoning for every skill — Claude Sonnet via ANTHROPIC_API_KEY
    skill_model: str = "claude-sonnet-5"
    # Keyword analysis retries on this OpenRouter model when the direct Anthropic
    # call fails or returns nothing. A different provider, so an Anthropic
    # billing failure does not take the fallback with it.
    keyword_analysis_fallback_model: str = "google/gemini-2.5-pro"
    # Cheap first pass on the relevance gate. It may drop only an obvious miss.
    # Everything else is still judged by skill_model.
    keyword_screen_enabled: bool = True
    keyword_screen_model: str = "claude-haiku-4-5"
    # Optional alias for SKILL_MODEL. When CLAUDE_MODEL is set it wins.
    claude_model: str = ""
    # Phase 4 competitor discovery and scoring. OpenRouter chat, separate from the Anthropic skill model.
    competitor_model: str = "google/gemini-2.5-pro"
    # OpenRouter Perplexity Pro for URL research / CDD extraction
    research_model: str = "perplexity/sonar-pro"
    # Nano Banana (Gemini 2.5 Flash Image) via OpenRouter Images API
    image_model: str = "google/gemini-2.5-flash-image"
    # Site preview section layout. OpenRouter chat, separate from the Anthropic skill model.
    preview_layout_model: str = "google/gemini-2.5-pro"
    # Legacy write-model id; Phase 10 drafts now use skill_model on Anthropic
    write_model: str = "claude-sonnet-5"
    # Legacy clustering model id; keyword LLM paths now use skill_model
    keyword_cluster_model: str = "claude-sonnet-5"
    keyword_relevance_max_per_seed: int = 60
    keyword_relevance_max_input_per_seed: int = 80
    keyword_max_seeds: int = 50
    keyword_pool_target: int = 4500
    # Rows requested from each DataForSEO Labs endpoint. Labs bills per row
    # returned, and 1,000 is the API ceiling for one page.
    keyword_labs_page_limit: int = 1000
    # A full page used to trigger up to three more calls so each funnel could
    # catch up. Those calls are off unless this is set; the first page stands.
    keyword_labs_followups: bool = False
    # Variant ideas and the informational top-up are extra keyword-ideas calls
    # on every seed. Off unless set.
    keyword_extra_idea_pulls: bool = False
    # Stop starting new Search Demand calls once this run has committed this
    # much. Each selected service is given an equal share before expansion
    # starts, so the first services cannot spend the rest of the run.
    search_demand_budget_usd: float = 15.0
    keyword_relevance_max_tokens: int = 8192
    # Keyword clustering (Phase 5). False skips clustering entirely: the report
    # records that it did not run and why, and carries no clusters.
    keyword_clustering_enabled: bool = True
    # Which engine clusters when clustering runs: the model (True) or the rules
    # (False). A phase deadline can still force the rules; the report says so.
    keyword_clustering_use_model: bool | None = None
    # Deprecated name of keyword_clustering_use_model (KEYWORD_CLUSTER_USE_LLM). Still
    # read when the new name is unset, with a warning, so existing deployments keep
    # their setting.
    keyword_cluster_use_llm: bool | None = None
    # Per-keyword LLM relevance gate (Phase 5). Judges every seeded keyword
    # against its own service + CDD + competitor context, and fails closed:
    # anything it could not judge is quarantined, never silently kept.
    keyword_validation_enabled: bool = True
    # Each verdict row costs ~40 output tokens. tokens_for_batch() raises the
    # reply budget with the batch so a larger list is not cut off mid-JSON.
    # 80 keywords is one call instead of three rounds of 25, and still fits
    # inside the 8192-token floor.
    keyword_validation_batch_size: int = 80
    keyword_validation_threshold: float = 0.6
    # Hard ceiling on LLM calls per run. Haiku's keep and drop are final, so
    # this counts screens plus the few batches Sonnet still has to see.
    keyword_validation_max_calls: int = 800
    # Batches judged at once. At 3, a 40-batch run took 12+ minutes and Phase 5 hit
    # its 20-minute kill. Measured on the live key (2026-10-09): 16 and 32 concurrent
    # 80-keyword calls both finished without a rate-limit error (32 in 28 s), so a
    # ~60-call run is two rounds. The Anthropic SDK retries a 429 on its own.
    keyword_validation_concurrency: int = 32
    keyword_validation_call_timeout: int = 90
    keyword_validation_cache_days: int = 14
    # Post-clustering LLM coherence gate (Phase 5). Judges each cluster as a
    # single-page topic; fails closed — unjudged / drop / review stay out of
    # CDP + topic drafting.
    cluster_validation_enabled: bool = True
    cluster_validation_batch_size: int = 8
    cluster_validation_threshold: float = 0.6
    cluster_validation_max_calls: int = 40
    # 36 small calls at 3 at a time took ~2 minutes of Phase 5; same calls, more at once.
    cluster_validation_concurrency: int = 16
    cluster_validation_call_timeout: int = 45
    cluster_validation_cache_days: int = 14
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
    # Every DataForSEO request sets location and language explicitly. These are
    # the fallbacks when a client's geographic focus resolves to nothing; a
    # project or request may override either.
    dataforseo_location_code: int = 2840  # United States
    dataforseo_language_code: str = "en"
    # sandbox.dataforseo.com for dev and the opt-in integration test.
    dataforseo_sandbox: bool = False
    # Stop further DataForSEO calls once today's recorded spend plus one call
    # would reach this many USD. 0 disables the cap.
    dataforseo_daily_budget_usd: float = 0.0
    # Expansion pushes this floor to Labs via filters. Default 50.
    keyword_expand_min_volume: int = 10
    # Model-suggested sub-service seeds per selected service (specialisations, delivery
    # formats, audience segments, topic verticals). They are fetched on top of the seed
    # budget, so each one adds a keyword fetch and gate work to Phase 5. 0 turns it off.
    subservice_expansion_max_seeds: int = 10
    # Reuse saved keyword fetches across clients (place-free keywords only). False fetches every seed fresh.
    keyword_pool_enabled: bool = True
    # Days a saved keyword fetch is trusted before it is fetched again.
    keyword_pool_ttl_days: int = 180
    # Most of a client's own places tried (as "<seed> <place>" volume lookups) when a saved seed is reused.
    keyword_pool_place_cap: int = 5
    # Most industry-level seeds added on top of the service seeds. 0 turns the industry layer off.
    keyword_industry_max_seeds: int = 8
    # Informational (top-of-funnel) share floor for industry seeds. Service seeds keep their own floor.
    keyword_industry_informational_share: float = 0.10
    # Secondary keywords wanted per URL-map entry; short entries are topped up with measured variants.
    keyword_secondary_target: int = 5
    # Most modifier-variant candidates checked for volume in one run (one batched call, all entries).
    # About ten per short row, so 1000 covers a 100-row map and is one provider task
    # (DataForSEO takes 1000 keywords per task). A run that hits the cap logs a warning.
    keyword_secondary_lookup_cap: int = 1000
    # related_keywords depth is 0–4. Default 1 (the seed plus one hop).
    keyword_related_depth: int = 2
    # Ads volume vs clickstream volume. Flag when the larger is more than this
    # many times the smaller. Both numbers still come from DataForSEO.
    volume_discrepancy_ratio: float = 2.0
    # SERP-overlap cluster refinement. Live SERP is one paid HTTP call per
    # keyword (DataForSEO does not batch live tasks), so this runs over the
    # head terms only; the tail keeps token clustering. 40 head terms at
    # concurrency 10 is enough overlap signal without a multi-minute stall.
    serp_clustering_enabled: bool = True
    serp_clustering_head_terms: int = 40
    serp_clustering_min_shared: int = 4
    # dataforseo._post caps the whole process at 25 simultaneous requests (limit 30).
    serp_clustering_concurrency: int = 20
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

    # --- Content Production site preview: built by the Manus agent ----------------
    # The draft page is rebuilt in the client's own site design by a Manus task.
    # Without a key the preview is unavailable (there is no other preview builder).
    manus_api_key: str = ""
    manus_api_base_url: str = "https://api.manus.ai"
    # Manus project (folder) for preview tasks: an exact id wins, else a name matched
    # loosely. Empty means no project. The project's own instruction applies to every
    # task in it, so keep that instruction empty or in line with the brief.
    manus_project_id: str = ""
    manus_project_name: str = ""
    # Connectors by name or id among those installed in the Manus account.
    manus_connectors: str = "Firecrawl"
    # standard | lite | max (standard balances quality and credits).
    manus_agent_profile: str = "standard"
    manus_timeout_minutes: int = 90
    manus_poll_seconds: float = 10.0
    # USD per Manus credit, for the cost tracker. 0 records credits only, no dollar
    # amount (nothing is guessed).
    manus_usd_per_credit: float = 0.0

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
    # QA lessons. Empty uses backend/data/agent_memory. Project files are per client.
    # System memory keeps the recurring failure, with no client name in it.
    agent_memory_dir: str = ""
    client_memory_retention_days: int = 30
    client_memory_retention_enabled: bool = True
    # QA lessons the next agent run reads. Empty uses backend/data/agent_memory.
    agent_memory_dir: str = ""
    readiness_threshold: float = 90.0
    # 0 = no soft cap — take every page discovered (hard safety ceiling still applies)
    seo_audit_max_pages: int = 0
    # Phase 7 composite fallback when Ahrefs Site Audit is unavailable
    technical_seo_seo_audit_max_pages: int = 40
    # Default chat-turn budget; heavy phases override below
    agent_timeout_seconds: int = 480
    technical_seo_agent_timeout_seconds: int = 900
    # Keyword research expands every selected seed, then checks SERPs and clusters.
    # 12 minutes was cutting ClickTrends-sized runs off during the last Claude pass.
    search_demand_agent_timeout_seconds: int = 1200
    # Drafting one page also measures the live page and builds the site preview.
    content_production_agent_timeout_seconds: int = 900
    # Phase 3 is intentionally bounded for interactive chat. A larger crawl can
    # be requested later through the dedicated crawl/audit tools.
    website_situation_max_pages: int = 40
    website_operation_timeout_seconds: int = 75
    # Comprehensive SEO audit needs headroom for discovery + multi-page fetches
    # (WAF/Cloudflare sites especially). Inner audit stops ~10s early for partials.
    website_seo_audit_timeout_seconds: int = 240

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
    def _resolve_clustering_engine_setting(self) -> "Settings":
        if self.keyword_clustering_use_model is None:
            legacy = self.keyword_cluster_use_llm
            if legacy is not None:
                logging.getLogger("app.config").warning(
                    "KEYWORD_CLUSTER_USE_LLM is deprecated; set KEYWORD_CLUSTERING_USE_MODEL instead."
                )
            self.keyword_clustering_use_model = bool(legacy)
        return self

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

    @model_validator(mode="after")
    def _live_providers_when_production_flags_unset(self) -> "Settings":
        """Production must not inherit the dev mock defaults.

        Unset USE_MOCK_* is True in this model. On Railway that blanks competitor
        discovery: mock domains are rejected as placeholders, so the phase stays
        on an empty card. An explicit true/false in the environment is kept.
        """
        if self.environment != "production":
            return self
        if os.environ.get("USE_MOCK_PROVIDERS") is None:
            self.use_mock_providers = False
        if os.environ.get("USE_MOCK_LLM") is None:
            self.use_mock_llm = False
        return self

    @model_validator(mode="after")
    def _celery_broker_follows_redis_in_production(self) -> "Settings":
        """Railway injects REDIS_URL. A leftover localhost broker cannot reach it.

        An explicit CELERY_BROKER_URL is kept. Local development is unchanged,
        and a Redis URL that is still localhost is left on the default broker.
        """
        if self.environment != "production":
            return self
        if os.environ.get("CELERY_BROKER_URL"):
            return self
        redis = (self.redis_url or "").strip()
        if not redis or "127.0.0.1" in redis or "localhost" in redis:
            return self
        self.celery_broker_url = redis
        return self

    @model_validator(mode="after")
    def _claude_model_alias(self) -> "Settings":
        if (self.claude_model or "").strip():
            self.skill_model = self.claude_model.strip()
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def asyncpg_engine_settings(url: str) -> tuple[str, dict]:
    """URL and connect args that asyncpg can actually open.

    Railway's public Postgres URL adds ``sslmode`` and ``channel_binding``.
    asyncpg rejects both as connect keywords. A URL without those parameters
    is returned unchanged, so local Postgres is unaffected.
    """
    if "postgresql" not in url or "?" not in url:
        return url, {}
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key in {"sslmode", "channel_binding"} for key, _value in query):
        return url, {}
    sslmode = None
    kept: list[tuple[str, str]] = []
    for key, value in query:
        if key == "sslmode":
            sslmode = value
            continue
        if key == "channel_binding":
            continue
        kept.append((key, value))
    cleaned = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
    connect: dict = {}
    if sslmode in {"require", "prefer", "verify-ca", "verify-full"}:
        import ssl

        context = ssl.create_default_context()
        if sslmode in {"require", "prefer"}:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        connect["ssl"] = context
    return cleaned, connect
