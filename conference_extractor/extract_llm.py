"""Optional LLM structured extraction (OpenAI-compatible API)."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

from conference_record import merge_nonempty
from env_config import load_env

SCHEMA_KEYS = [
    "conference_domain",
    "conference_group_name",
    "conference_group_description",
    "industry_methodology",
    "event_name",
    "start_date",
    "end_date",
    "event_url",
    "exhibitor_crawler_url",
    "sponsor_crawler_url",
    "venue_name",
    "address_1",
    "address_2",
    "city",
    "country",
    "state_province",
    "zip_code",
    "event_description_methodology",
    "hosting_entity",
    "conference_group",
    "industry",
    "attending_companies",
    "exhibitor_companies",
    "sponsor_companies",
    "speakers",
]

SYSTEM_PROMPT = """You extract structured conference/event data for a B2B conference database.
Return ONLY valid JSON (no markdown). Use null for unknown scalar fields and [] for empty lists.

CRITICAL anti-hallucination rules:
- If a field is NOT explicitly stated in the page text, use null or [].
- Do NOT infer addresses, dates, companies, or speakers from context or general knowledge.
- Ignore navigation menus, footers, cookie banners, related events, and social links.
- Each list item MUST include "evidence_snippet": an exact substring (max 120 chars) copied from the page text.

Fields (data.txt methodology):
- conference_domain: hostname of official event site
- conference_group_name, conference_group_description, conference_group: organizer/series
- industry_methodology: short code (HT, AI, TECH, FIN, EDU, MFG, GEN) ONLY if clearly stated
- event_name, start_date, end_date (ISO 8601 dates if possible), event_url
- exhibitor_crawler_url, sponsor_crawler_url: URLs of exhibitor/sponsor listing pages if present in text
- venue_name, address_1, address_2, city, country, state_province, zip_code
- event_description_methodology: 1-3 sentence factual summary copied from page (no marketing fluff)
- hosting_entity, industry (human-readable industry label)
- attending_companies, exhibitor_companies, sponsor_companies: arrays of {name, website, details, evidence_snippet}
- speakers: array of {name, title, company, details, evidence_snippet}

Only include speakers that appear in speaker/agenda/program sections, not session schedule titles alone.
"""


def llm_available() -> bool:
    load_env()
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _api_config() -> tuple[str, str, str] | None:
    load_env()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    return key, base, model


def _parse_json_content(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in SCHEMA_KEYS if k in data}


async def extract_with_llm(
    page_text: str,
    *,
    event_name: str | None,
    event_url: str | None,
) -> dict[str, Any]:
    cfg = _api_config()
    if not cfg:
        return {}

    api_key, base, model = cfg
    user = (
        f"Official event URL: {event_url or 'unknown'}\n"
        f"Listing event name: {event_name or 'unknown'}\n\n"
        f"Page text:\n{page_text[:14000]}"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()

    content = body["choices"][0]["message"]["content"]
    return _parse_json_content(content)


def extract_with_llm_sync(
    page_text: str,
    *,
    event_name: str | None,
    event_url: str | None,
) -> dict[str, Any]:
    cfg = _api_config()
    if not cfg:
        return {}

    api_key, base, model = cfg
    user = (
        f"Official event URL: {event_url or 'unknown'}\n"
        f"Listing event name: {event_name or 'unknown'}\n\n"
        f"Page text:\n{page_text[:14000]}"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    with httpx.Client(timeout=90.0) as client:
        resp = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        resp.raise_for_status()
        body = resp.json()

    content = body["choices"][0]["message"]["content"]
    return _parse_json_content(content)
