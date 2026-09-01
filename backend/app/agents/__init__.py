from app.agents.discovery import run_discovery
from app.agents.tracking import run_tracking
from app.agents.website import run_website
from app.agents.competitor import run_competitor
from app.agents.search_demand import run_search_demand
from app.agents.content_strategy import run_content_strategy
from app.agents.site_architecture import run_site_architecture
from app.agents.technical_seo import run_technical_seo
from app.agents.content_audit import run_content_audit
from app.agents.content_planning import run_content_planning
from app.agents.content_production import run_content_production
from app.agents.on_page_seo import run_on_page_seo
from app.agents.publishing import run_publishing

AGENT_RUNNERS = {
    "discovery_agent": run_discovery,
    "tracking_access_agent": run_tracking,
    "website_situation_agent": run_website,
    "competitor_market_agent": run_competitor,
    "search_demand": run_search_demand,
    "content_strategy": run_content_strategy,
    "site_architecture": run_site_architecture,
    "technical_seo": run_technical_seo,
    "content_audit": run_content_audit,
    "content_planning": run_content_planning,
    "content_production": run_content_production,
    "on_page_seo": run_on_page_seo,
    "publishing": run_publishing,
}

__all__ = [
    "AGENT_RUNNERS",
    "run_discovery",
    "run_tracking",
    "run_website",
    "run_competitor",
    "run_search_demand",
    "run_content_strategy",
    "run_site_architecture",
    "run_technical_seo",
    "run_content_audit",
    "run_content_planning",
    "run_content_production",
    "run_on_page_seo",
    "run_publishing",
]
