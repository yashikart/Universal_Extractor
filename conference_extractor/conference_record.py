"""Canonical schema for enriched conference records (data.txt datapoints)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

LIST_ENTITY_FIELDS = (
    "attending_companies",
    "exhibitor_companies",
    "sponsor_companies",
    "speakers",
)


def domain_from_url(url: str) -> str | None:
    try:
        host = (urlparse(url).netloc or "").lower()
        return host[4:] if host.startswith("www.") else host or None
    except Exception:
        return None


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
        "listing_url": None,
        "exhibitor_crawler_url": None,
        "sponsor_crawler_url": None,
        "venue_name": None,
        "address_1": None,
        "address_2": None,
        "city": None,
        "country": None,
        "state_province": None,
        "zip_code": None,
        "event_description_methodology": None,
        "hosting_entity": None,
        "conference_group": None,
        "industry": None,
        "attending_companies": [],
        "exhibitor_companies": [],
        "exhibitor_company_websites": [],
        "sponsor_companies": [],
        "sponsor_company_websites": [],
        "speakers": [],
        "scope": {
            "in_scope": False,
            "b2b": False,
            "networking": False,
            "has_exhibitors": False,
            "has_sponsors": False,
            "has_speakers": False,
            "scope_rule_version": None,
        },
        "resolution_confidence": None,
        "resolution_notes": [],
        "enrich_notes": [],
        "field_provenance": {},
        "validation": {},
    }


def _entity_key(item: Any) -> str:
    if isinstance(item, dict):
        return (item.get("name") or item.get("title") or "").strip().lower()
    return str(item).strip().lower()


def _normalize_entity_list(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if not name:
                continue
            ent = {"name": name, "website": None, "details": None}
        elif isinstance(item, dict):
            name = (item.get("name") or item.get("title") or "").strip()
            if not name:
                continue
            ent = {
                "name": name,
                "website": item.get("website") or item.get("url"),
                "details": item.get("details") or item.get("bio") or item.get("title"),
            }
            if item.get("company") and not ent.get("details"):
                ent["details"] = item.get("company")
        else:
            continue
        key = _entity_key(ent)
        if key in seen:
            continue
        seen.add(key)
        out.append(ent)
    return out


def _split_companies(field: str, record: dict[str, Any]) -> None:
    raw = record.get(field) or []
    if not raw:
        record[field] = []
        websites_field = f"{field.replace('_companies', '')}_company_websites"
        if websites_field in record:
            record[websites_field] = []
        return

    if raw and isinstance(raw[0], dict):
        normalized = _normalize_entity_list(raw)
        record[field] = [e["name"] for e in normalized]
        websites_field = (
            "exhibitor_company_websites"
            if field == "exhibitor_companies"
            else "sponsor_company_websites"
            if field == "sponsor_companies"
            else None
        )
        if websites_field:
            record[websites_field] = [e.get("website") for e in normalized]
        return

    record[field] = [str(x).strip() for x in raw if str(x).strip()]


def _is_junk_speaker_record(item: dict[str, Any]) -> bool:
    name = (item.get("name") or "").strip()
    if not name:
        return True
    if _entity_key(item) and len(name) < 2:
        return True
    lower = name.lower()
    if re.search(r"\d{1,2}:\d{2}", name):
        return True
    if any(
        x in lower
        for x in (
            "registration",
            "sign-in",
            "welcome message",
            "opening session",
            "a.m.",
            "p.m.",
            "conference index",
            " on august",
            " on september",
            " on october",
            " on november",
            " on december",
            " on january",
            " on february",
            " on march",
            " on april",
            " on may",
            " on june",
            " on july",
            "program will be released",
            "creative commons",
            "licensed under",
            "noun project",
            "except where otherwise",
            "content on this site",
        )
    ):
        return True
    if lower in ("australia", "sydney", "online", "virtual", "remote"):
        return True
    if " logo" in lower or lower.endswith(" logo"):
        return True
    if len(name) > 70:
        return True
    return False


def finalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize entity lists and fill conference_group from group name."""
    speakers_raw = record.get("speakers") or []
    if speakers_raw and isinstance(speakers_raw[0], str):
        record["speakers"] = [
            {"name": s, "title": None, "company": None, "details": None}
            for s in speakers_raw
            if s
        ]
    elif speakers_raw:
        normalized = _normalize_entity_list(speakers_raw)
        record["speakers"] = [
            {
                "name": e["name"],
                "title": e.get("title") or (e.get("details") if "@" not in str(e.get("details") or "") else None),
                "company": e.get("company"),
                "details": e.get("details"),
            }
            for e in normalized
            if not _is_junk_speaker_record(e)
        ]

    attending = record.get("attending_companies") or []
    if attending and isinstance(attending[0], dict):
        record["attending_companies"] = _normalize_entity_list(attending)
    else:
        record["attending_companies"] = [
            {"name": x, "website": None, "details": None}
            for x in (attending if isinstance(attending, list) else [])
            if x
        ]

    _split_companies("exhibitor_companies", record)
    _split_companies("sponsor_companies", record)

    if not record.get("conference_group") and record.get("conference_group_name"):
        record["conference_group"] = record["conference_group_name"]
    if not record.get("conference_group_name") and record.get("conference_group"):
        record["conference_group_name"] = record["conference_group"]
    if record.get("event_url"):
        record["conference_domain"] = record.get("conference_domain") or domain_from_url(
            record["event_url"]
        )
    return record


def merge_nonempty(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, val in source.items():
        if val is None or val == "" or val == []:
            continue
        if key == "scope" and isinstance(val, dict):
            target.setdefault("scope", {}).update({k: v for k, v in val.items() if v is not None})
            continue
        if key in LIST_ENTITY_FIELDS and isinstance(val, list):
            existing = target.get(key) or []
            combined = list(existing) + list(val)
            target[key] = combined
            continue
        if target.get(key) in (None, "", []):
            target[key] = val


def merge_records(base: dict[str, Any], *others: dict[str, Any]) -> dict[str, Any]:
    for other in others:
        merge_nonempty(base, other)
    return finalize_record(base)
