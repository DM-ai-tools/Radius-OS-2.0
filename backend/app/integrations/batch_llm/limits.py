"""Load and resolve llm_limits.yaml."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_CONFIG_CANDIDATES = (
    Path(__file__).resolve().parents[3] / "config" / "llm_limits.yaml",
    Path(__file__).resolve().parents[2] / "config" / "llm_limits.yaml",
)


class LimitsError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def load_limits() -> dict[str, Any]:
    for path in _CONFIG_CANDIDATES:
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise LimitsError(f"llm_limits.yaml must be a mapping: {path}")
            return data
    raise LimitsError(
        "llm_limits.yaml not found. Expected at backend/config/llm_limits.yaml"
    )


def clear_limits_cache() -> None:
    load_limits.cache_clear()


def defaults() -> dict[str, Any]:
    return dict(load_limits().get("defaults") or {})


def resolve_model_id(raw: str | None) -> str:
    aliases = load_limits().get("aliases") or {}
    key = (raw or "").strip()
    return str(aliases.get(key) or key)


def model_spec(model_key: str) -> dict[str, Any]:
    models = load_limits().get("models") or {}
    if model_key not in models:
        raise LimitsError(
            f"Unknown model_key '{model_key}'. Known: {', '.join(sorted(models))}"
        )
    return dict(models[model_key])


def effective_model_id(model_key: str, override: str | None = None) -> str:
    spec = model_spec(model_key)
    raw = override or spec.get("openrouter_id") or ""
    return resolve_model_id(str(raw))
