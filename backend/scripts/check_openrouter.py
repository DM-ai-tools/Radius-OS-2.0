"""Print OpenRouter remaining credits. Does not print the API key."""

from __future__ import annotations

import json
import urllib.request

from app.config import get_settings


def _get(path: str, key: str) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"https://openrouter.ai/api/v1{path}",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return exc.code, parsed


def _ping_model(key: str, model: str) -> None:
    payload: dict = {
        "model": model,
        "max_tokens": 512,
        "messages": [{"role": "user", "content": "Reply with JSON {\"ok\": true} only."}],
    }
    if "gemini" in model.lower():
        payload["reasoning"] = {"max_tokens": 256}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://searchfit.local",
            "X-Title": "Radius OS credit check",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = message.get("content")
            usage = data.get("usage") or {}
            print(
                "ping",
                model,
                "http",
                resp.status,
                "finish",
                choice.get("finish_reason"),
                "tokens",
                usage.get("completion_tokens"),
                "content",
                (str(content)[:80] if content else None),
            )
    except urllib.error.HTTPError as exc:
        print("ping", model, "http", exc.code, exc.read().decode("utf-8", errors="replace")[:300])


def main() -> None:
    settings = get_settings()
    key = (settings.openrouter_api_key or "").strip()
    print("provider", settings.llm_provider)
    print("skill_model", settings.skill_model)
    print("competitor_model", settings.competitor_model)
    print("use_mock_llm", settings.use_mock_llm)
    print("key_present", bool(key), "key_len", len(key))
    if not key:
        print("NO_KEY")
        return
    for path in ("/key", "/credits"):
        status, data = _get(path, key)
        print("endpoint", path, "http", status)
        if path == "/key" and isinstance(data, dict):
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            print(
                json.dumps(
                    {
                        "is_free_tier": payload.get("is_free_tier"),
                        "limit": payload.get("limit"),
                        "usage": payload.get("usage"),
                        "usage_daily": payload.get("usage_daily"),
                        "limit_remaining": payload.get("limit_remaining"),
                    },
                    indent=2,
                )
            )
        elif path == "/credits" and isinstance(data, dict):
            payload = data.get("data") if isinstance(data.get("data"), dict) else data
            credits = float(payload.get("total_credits") or 0)
            usage = float(payload.get("total_usage") or 0)
            print(
                json.dumps(
                    {
                        "total_credits": credits,
                        "total_usage": usage,
                        "remaining_usd": round(credits - usage, 2),
                    },
                    indent=2,
                )
            )
        else:
            print(json.dumps(data, indent=2)[:1500])
    _ping_model(key, settings.competitor_model)
    _ping_model(key, settings.skill_model)


if __name__ == "__main__":
    main()
