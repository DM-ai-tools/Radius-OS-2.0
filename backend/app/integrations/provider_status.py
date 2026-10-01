"""Why a provider call did not produce a metric.

The backlinks chain used to collapse every failure into one message — "SEMrush /
Ahrefs / Moz keys not set" — which was printed even when the keys WERE set and
the call had failed for an entirely different reason. An operator reading that
would go looking for a config problem that did not exist.

These statuses distinguish the cases that need different action:

    not_configured   no key in config          -> add a key
    unauthorized     401 / 403                 -> key is wrong or lacks scope
    rate_limited     429                       -> back off, or raise the plan
    provider_error   5xx                       -> their outage, retry later
    timeout          request timed out         -> network or provider slowness
    network_error    connection failed         -> egress / DNS / TLS
    bad_response     2xx but unparseable       -> contract drift, needs a look
    ok               metric obtained

Nothing here ever carries a credential: the classifier only sees a status code
or an exception type, and ``detail`` is scrubbed before it is stored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NOT_CONFIGURED = "not_configured"
UNAUTHORIZED = "unauthorized"
RATE_LIMITED = "rate_limited"
PROVIDER_ERROR = "provider_error"
TIMEOUT = "timeout"
NETWORK_ERROR = "network_error"
BAD_RESPONSE = "bad_response"
OK = "ok"

# Operator-facing sentence per status. `{provider}` is interpolated.
STATUS_MESSAGES: dict[str, str] = {
    NOT_CONFIGURED: "{provider} API key is not configured.",
    UNAUTHORIZED: "{provider} rejected the credentials (unauthorized) — the key is wrong, expired, or lacks access to this endpoint.",
    RATE_LIMITED: "{provider} rate-limited the request (429). The key works; the quota or request rate is the problem.",
    PROVIDER_ERROR: "{provider} returned a server error — their side, not the key.",
    TIMEOUT: "{provider} did not respond in time.",
    NETWORK_ERROR: "Could not reach {provider} (network or DNS failure).",
    BAD_RESPONSE: "{provider} replied but the response could not be read as the expected metric.",
    OK: "{provider} returned the metric.",
}

# Anything that looks like a credential, stripped from any detail we keep.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._\-=/+]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|apikey|token|secret|password)\b\s*[:=]\s*\S+"),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\b"),
)


def scrub(text: Any, *, limit: int = 200) -> str:
    """Trim a detail string and remove anything credential-shaped."""
    out = str(text or "")
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out[:limit]


def classify_status(status_code: int) -> str:
    if status_code in (401, 403):
        return UNAUTHORIZED
    if status_code == 429:
        return RATE_LIMITED
    if 500 <= status_code < 600:
        return PROVIDER_ERROR
    if status_code >= 400:
        return PROVIDER_ERROR
    return OK


def classify_exception(exc: BaseException) -> str:
    """Map an exception to a status without importing httpx at module scope."""
    name = type(exc).__name__
    if "Timeout" in name:
        return TIMEOUT
    if name in ("ConnectError", "ConnectTimeout", "ReadError", "WriteError",
                "NetworkError", "TransportError", "ProxyError", "RemoteProtocolError"):
        return NETWORK_ERROR
    if isinstance(exc, ValueError):  # json() raises ValueError
        return BAD_RESPONSE
    # A status we raised ourselves carries the code in the message.
    match = re.search(r"\bHTTP\s+(\d{3})\b", str(exc))
    if match:
        return classify_status(int(match.group(1)))
    if "not configured" in str(exc).lower():
        return NOT_CONFIGURED
    return PROVIDER_ERROR


@dataclass
class ProviderAttempt:
    """One provider's outcome, safe to log and to return over the API."""

    provider: str
    status: str
    detail: str = ""
    http_status: int | None = None

    @property
    def message(self) -> str:
        template = STATUS_MESSAGES.get(self.status, "{provider} failed.")
        return template.format(provider=self.provider.title())

    @property
    def ok(self) -> bool:
        return self.status == OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "message": self.message,
            "detail": scrub(self.detail),
            "http_status": self.http_status,
        }


@dataclass
class AttemptLog:
    """Every provider tried, in order, plus the reason a fallback was needed."""

    attempts: list[ProviderAttempt] = field(default_factory=list)

    def record(
        self,
        provider: str,
        status: str,
        *,
        detail: Any = "",
        http_status: int | None = None,
    ) -> ProviderAttempt:
        attempt = ProviderAttempt(
            provider=provider,
            status=status,
            detail=scrub(detail),
            http_status=http_status,
        )
        self.attempts.append(attempt)
        return attempt

    def to_list(self) -> list[dict[str, Any]]:
        return [a.to_dict() for a in self.attempts]

    def fallback_reason(self) -> str:
        """One sentence explaining why no provider metric is available.

        Says what actually happened per provider — never "keys not set" unless
        that is genuinely the case for all of them.
        """
        if not self.attempts:
            return "No backlink provider is configured (SEMrush, Ahrefs or Moz)."
        if all(a.status == NOT_CONFIGURED for a in self.attempts):
            names = ", ".join(a.provider.title() for a in self.attempts)
            return f"No backlink provider API key is configured ({names})."
        tried = [a for a in self.attempts if a.status != NOT_CONFIGURED]
        if tried:
            return "Provider metrics unavailable: " + " ".join(a.message for a in tried)
        return "No backlink provider produced a metric."
