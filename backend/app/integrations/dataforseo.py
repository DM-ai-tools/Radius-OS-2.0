"""DataForSEO Labs helpers for automated competitor discovery."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("dataforseo")


async def competitors_domain(domain: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Return organic SERP competitors for a domain via DataForSEO Labs."""
    settings = get_settings()
    if not settings.dataforseo_login or not settings.dataforseo_password:
        return []

    target = domain.lower().removeprefix("www.")
    token = base64.b64encode(
        f"{settings.dataforseo_login}:{settings.dataforseo_password}".encode()
    ).decode()
    payload: list[dict[str, Any]] = [
        {
            "target": target,
            "location_code": 2840,
            "language_code": "en",
            "exclude_top_domains": True,
            "exclude_domains": [target, f"www.{target}"],
            "limit": limit,
            "item_types": ["organic"],
        }
    ]
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.dataforseo.com/v3/dataforseo_labs/google/competitors_domain/live",
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"DataForSEO HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        if body.get("status_code") != 20000:
            raise RuntimeError(
                f"DataForSEO status {body.get('status_code')}: {body.get('status_message')}"
            )
        tasks = body.get("tasks") or []
        if not tasks:
            return []
        task = tasks[0]
        if task.get("status_code") not in (20000, 20100):
            raise RuntimeError(
                f"DataForSEO task {task.get('status_code')}: {task.get('status_message')}"
            )
        results = task.get("result") or []
        items = (results[0].get("items") or []) if results else []
        out: list[dict[str, str]] = []
        for item in items:
            d = (item.get("domain") or "").strip().lower().removeprefix("www.")
            if not d or d == target:
                continue
            label = d.split(".")[0].replace("-", " ").title()
            out.append({"name": label, "url": f"https://{d}"})
        log.info("competitors_domain_ok", domain=target, count=len(out))
        return out
