"""AI intake fill + CDD document extraction (Perplexity Pro via OpenRouter)."""

from __future__ import annotations

import io
import re
from typing import Any

from app.config import get_settings
from app.integrations.llm import _openrouter_chat, synthesize_json
from app.integrations.web_fetch import fetch_url, page_text_excerpt, parse_html
from app.logging_config import get_logger
from app.services.discovery_fields import (
    ACCESS_FIELDS,
    CDD_LABEL_ALIASES,
    CLIENT_ONLY_FIELDS,
    RESEARCH_FIELDS,
    cdd_json_schema_hint,
)

log = get_logger("intake_ai")

# Never AI-fill personal / account-manager fields
PERSONAL_KEYS = {
    "primary_contact_name",
    "primary_contact_email",
    "primary_contact_phone",
    "account_manager",
}


async def _perplexity_json(system: str, user: str) -> dict[str, Any] | None:
    settings = get_settings()
    model = settings.research_model or "perplexity/sonar-pro"
    if settings.use_mock_llm or not settings.openrouter_api_key:
        return None
    try:
        text = await _openrouter_chat(
            system=system + "\nReturn ONLY valid JSON. No markdown fences.",
            user=user,
            model=model,
            max_tokens=4000,
        )
        if not text:
            return None
        return _parse_json_loose(text)
    except Exception as exc:  # noqa: BLE001
        log.warning("perplexity_json_failed", error=str(exc), model=model)
        # Fallback to default skill model JSON helper
        return await synthesize_json(system, user)


def _parse_json_loose(text: str) -> dict[str, Any] | None:
    import json

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


async def fill_client_form_from_url(url: str) -> dict[str, Any]:
    """Research a website and return New Client form fields (no personal contacts)."""
    url = (url or "").strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    excerpt = ""
    title = ""
    try:
        fetched = await fetch_url(url)
        parser = parse_html(fetched.get("text") or "")
        excerpt = page_text_excerpt(parser, 4000)
        title = parser.title or ""
    except Exception as exc:  # noqa: BLE001
        log.warning("fill_fetch_failed", error=str(exc), url=url)

    system = (
        "You are researching a company for SEO onboarding intake. "
        "Use the website URL and any public knowledge to fill business fields. "
        "Do NOT invent personal contact names, emails, or phone numbers — leave those null. "
        "Return JSON only."
    )
    user = (
        f"Website: {url}\nPage title: {title}\n"
        f"Site excerpt:\n{excerpt or '(empty — use public knowledge about this domain)'}\n\n"
        "Return JSON with keys:\n"
        "name, industry, company_size, headquarters_location, target_audience,\n"
        "geographic_focus, known_competitors, website_priorities, objectives,\n"
        "business_keywords, b2b_b2c, business_model.\n"
        "objectives = string array of SEO/marketing goals.\n"
        "known_competitors = short text with competitor names/URLs.\n"
        "Omit or null any field you cannot support."
    )
    parsed = await _perplexity_json(system, user)
    if not parsed:
        # Deterministic stub when LLM unavailable
        host = url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
        parsed = {
            "name": host.split(".")[0].replace("-", " ").title(),
            "industry": "",
            "company_size": "",
            "headquarters_location": "",
            "target_audience": "",
            "geographic_focus": "",
            "known_competitors": "",
            "website_priorities": "Improve organic visibility and lead quality",
            "objectives": ["Increase organic traffic", "Generate more qualified leads"],
            "business_keywords": "",
            "b2b_b2c": "",
            "business_model": "",
            "_note": "Mock/fallback fill — configure OpenRouter + Perplexity for live research.",
        }

    out: dict[str, Any] = {}
    for k, v in parsed.items():
        if k.startswith("_") or k in PERSONAL_KEYS:
            continue
        if v in (None, "", [], {}):
            continue
        out[k] = v
    if "name" not in out and title:
        out["name"] = title.split("|")[0].split("-")[0].strip()[:80]
    out["primary_url"] = url
    return out


async def extract_cdd_fields_from_text(text: str, *, filename: str = "") -> dict[str, Any]:
    """Map free-text / spreadsheet dump into CDD discovery field keys."""
    system = (
        "You extract Client Discovery Document (CDD / APSA) fields for SEO onboarding. "
        "Map whatever is present into the JSON keys listed. "
        "Do not invent personal contact details. Return JSON only."
    )
    user = (
        f"Source file: {filename or 'upload'}\n"
        f"Allowed keys: {cdd_json_schema_hint()}, business_name, website_url\n\n"
        f"Document text:\n{text[:18000]}\n\n"
        "Return JSON object keyed by those fields. Use null when unknown."
    )
    parsed = await _perplexity_json(system, user)
    if not parsed:
        # Heuristic label:value scrape for APSA-style sheets already flattened
        parsed = _heuristic_cdd_map(text)
    return _sanitize_cdd(parsed or {})


def _sanitize_cdd(parsed: dict[str, Any]) -> dict[str, Any]:
    allowed = set(RESEARCH_FIELDS) | set(CLIENT_ONLY_FIELDS) | set(ACCESS_FIELDS) | {
        "business_name",
        "website_url",
        "inferred_industry",
    }
    # aliases
    if "industry" in parsed and "inferred_industry" not in parsed:
        parsed["inferred_industry"] = parsed.pop("industry")
    if "average_order_value" in parsed and "average_ticket_size" not in parsed:
        parsed["average_ticket_size"] = parsed.pop("average_order_value")
    out: dict[str, Any] = {}
    for k, v in parsed.items():
        if k in PERSONAL_KEYS:
            continue
        if k not in allowed:
            continue
        if v in (None, "", [], {}):
            continue
        out[k] = v
    return out


def _heuristic_cdd_map(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("##"):
            continue
        label = ""
        value = ""
        if "|" in raw:
            parts = [p.strip() for p in raw.split("|")]
            # APSA dump style: idx|Label|Value  OR  Label|Value  OR  empty|Label|Value
            nonempty = [p for p in parts if p]
            if len(parts) >= 3 and parts[1]:
                label = parts[1].lower().strip()
                value = parts[2].strip() if len(parts) > 2 else ""
                # Sometimes value is only in parentheses on the label
                if not value and len(nonempty) >= 1:
                    value = ""
            elif len(parts) >= 2:
                label = parts[0].lower().strip() or (parts[1].lower().strip() if len(parts) > 1 else "")
                value = parts[1].strip() if parts[0] else (parts[2].strip() if len(parts) > 2 else "")
                if not parts[0] and len(parts) >= 3:
                    label = parts[1].lower().strip()
                    value = parts[2].strip()
            else:
                continue
        elif ":" in raw:
            label, value = raw.split(":", 1)
            label, value = label.lower().strip(), value.strip()
        else:
            # Standalone access question lines: "GTM? (pending)"
            label = raw.lower().strip()
            value = ""

        # Pull parenthetical note when cell has no separate value
        if not value and "(" in label and ")" in label:
            start = label.rfind("(")
            end = label.rfind(")")
            if start >= 0 and end > start:
                value = label[start + 1 : end].strip()
                label = label[:start].strip()

        key = CDD_LABEL_ALIASES.get(label)
        if not key:
            # fuzzy: strip trailing ?
            key = CDD_LABEL_ALIASES.get(label.rstrip("?").strip())
        if not key and label in CDD_LABEL_ALIASES.values():
            key = label
        if key and key not in PERSONAL_KEYS:
            if key == "industry":
                key = "inferred_industry"
            if value:
                out[key] = value
            elif key in (
                "analytics_access",
                "gtm_access",
                "google_ads_access",
                "merchant_center_access",
                "access_level",
                "search_console_access",
                "cms_access",
                "hosting_access",
            ):
                out.setdefault(key, "To confirm")
    return out


def extract_text_from_upload(filename: str, data: bytes) -> str:
    """Pull plain text from xlsx / csv / docx / pdf / txt."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        return _xlsx_to_text(data)
    if name.endswith(".csv"):
        return data.decode("utf-8", errors="ignore")
    if name.endswith(".docx"):
        return _docx_to_text(data)
    if name.endswith(".pdf"):
        return _pdf_to_text(data)
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore")
    # try xlsx then utf-8
    try:
        return _xlsx_to_text(data)
    except Exception:  # noqa: BLE001
        return data.decode("utf-8", errors="ignore")


def _xlsx_to_text(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    lines: list[str] = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        lines.append(f"## Sheet: {sheet}")
        for row in ws.iter_rows(values_only=True):
            vals = ["" if c is None else str(c).strip() for c in row]
            if any(vals):
                lines.append("|".join(vals))
    return "\n".join(lines)


def _docx_to_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t)
    return "\n".join(parts)
