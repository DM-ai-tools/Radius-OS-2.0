from app.agents.discovery import run_discovery
from app.agents.tracking import run_tracking
from app.agents.website import run_website
from app.agents.competitor import run_competitor

AGENT_RUNNERS = {
    "discovery_agent": run_discovery,
    "tracking_access_agent": run_tracking,
    "website_situation_agent": run_website,
    "competitor_market_agent": run_competitor,
}

__all__ = ["AGENT_RUNNERS", "run_discovery", "run_tracking", "run_website", "run_competitor"]
