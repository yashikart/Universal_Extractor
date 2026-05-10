"""Canonical schema for enriched conference records (25 datapoints + scope + metadata)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def empty_record() -> dict[str, Any]:
    return {
        "conference_domain": None,
        "conference_group_name": None,
        "conference_group_description": None,
        "industry_methodology": None,
        "event_name": None,
        "start_date": None,
        "end_date": None,
        "event_url": None,
        "exhibitor_crawler_url": None,
        "sponsor_crawler_url": None,
        "venue_name": None,
        "address_line_1": None,
        "address_line_2": None,
        "city": None,
        "country": None,
        "state_or_province": None,
        "postal_code": None,
        "event_description_methodology": None,
        "hosting_entity": None,
        "hosting_entity_url": None,
        "conference_group": None,
        "industry": None,
        "industry_raw": None,
        "attending_companies": [],
        "attending_crawler_url": None,
        "exhibitor_companies": [],
        "exhibitor_company_websites": [],
        "sponsor_companies": [],
        "sponsor_company_websites": [],
        "speakers": [],
        "scope": {
            "b2b": None,
            "networking_opportunities": None,
            "has_exhibitors": None,
            "has_sponsors": None,
            "has_speakers": None,
            "in_scope": None,
            "scope_rule_version": None,
        },
        "listing_metadata": {},
        "detail_parse_notes": [],
    }


def domain_from_url(url: str | None) -> str | None:
    if not url:
        return None
    p = urlparse(url.strip())
    return (p.netloc or "").lower() or None


def merge_nonempty(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for k, v in overlay.items():
        if k == "scope" and isinstance(v, dict):
            for sk, sv in v.items():
                if sv is not None:
                    base["scope"][sk] = sv
            continue
        if v is None:
            continue
        if isinstance(v, list) and len(v) == 0:
            continue
        if isinstance(v, dict) and len(v) == 0:
            continue
        base[k] = v
    return base
