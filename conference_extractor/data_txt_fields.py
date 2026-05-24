"""data.txt methodology — canonical field list and per-record coverage."""

from __future__ import annotations

import json
from typing import Any

# Flat scalar fields from data.txt (items 1–19, 7–15, etc.)
DATA_TXT_SCALAR_FIELDS: tuple[str, ...] = (
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
)

DATA_TXT_LIST_FIELDS: tuple[str, ...] = (
    "attending_companies",
    "exhibitor_companies",
    "exhibitor_company_websites",
    "sponsor_companies",
    "sponsor_company_websites",
    "speakers",
)

DATA_TXT_SCOPE_FIELDS: tuple[str, ...] = (
    "scope_b2b",
    "scope_networking",
    "scope_has_exhibitors",
    "scope_has_sponsors",
    "scope_has_speakers",
    "scope_in_scope",
)

FIELD_LABELS: dict[str, str] = {
    "conference_domain": "1) Conference Domain",
    "conference_group_name": "2) Conference Group Name",
    "conference_group_description": "3) Conference Group Description",
    "industry_methodology": "4) Industry (methodology code)",
    "event_name": "5) Event Name",
    "start_date": "6) Start Date",
    "end_date": "6) End Date",
    "event_url": "7) Event URL",
    "exhibitor_crawler_url": "8) Exhibitor Crawler URL",
    "sponsor_crawler_url": "9) Sponsor Crawler URL",
    "venue_name": "10) Venue Name",
    "address_1": "11) Address 1",
    "address_2": "11) Address 2",
    "city": "12) City",
    "country": "13) Country",
    "state_province": "14) State/Province",
    "zip_code": "15) Zip code",
    "event_description_methodology": "16) Event Description",
    "hosting_entity": "17) Hosting Entity",
    "conference_group": "18) Conference Group",
    "industry": "19) Industry",
    "attending_companies": "20) Attending Companies",
    "exhibitor_companies": "21) Exhibitor Companies",
    "exhibitor_company_websites": "22) Exhibitor Company Websites",
    "sponsor_companies": "23) Sponsor Companies",
    "sponsor_company_websites": "24) Sponsor Company Websites",
    "speakers": "25) Speakers",
}


def _filled_scalar(record: dict[str, Any], key: str) -> bool:
    val = record.get(key)
    if val is None:
        return False
    if isinstance(val, str):
        return bool(val.strip())
    return bool(val)


def _filled_list(record: dict[str, Any], key: str) -> bool:
    val = record.get(key)
    if not val:
        return False
    if isinstance(val, list):
        return len(val) > 0
    return bool(val)


def field_coverage(record: dict[str, Any]) -> dict[str, Any]:
    """Return filled/missing status for every data.txt datapoint."""
    filled: list[str] = []
    missing: list[str] = []
    details: dict[str, bool] = {}

    for key in DATA_TXT_SCALAR_FIELDS:
        ok = _filled_scalar(record, key)
        details[key] = ok
        (filled if ok else missing).append(key)

    for key in DATA_TXT_LIST_FIELDS:
        ok = _filled_list(record, key)
        details[key] = ok
        (filled if ok else missing).append(key)

    scope = record.get("scope") or {}
    scope_details: dict[str, bool] = {}
    for key in DATA_TXT_SCOPE_FIELDS:
        short = key.replace("scope_", "")
        ok = bool(scope.get(short if short != "in_scope" else "in_scope"))
        scope_details[key] = ok

    total = len(DATA_TXT_SCALAR_FIELDS) + len(DATA_TXT_LIST_FIELDS)
    count = sum(1 for k in DATA_TXT_SCALAR_FIELDS + DATA_TXT_LIST_FIELDS if details.get(k))

    return {
        "filled_count": count,
        "total_count": total,
        "pct": round(100.0 * count / total, 1) if total else 0.0,
        "filled": filled,
        "missing": missing,
        "fields": details,
        "scope": scope_details,
        "labels": {k: FIELD_LABELS.get(k, k) for k in DATA_TXT_SCALAR_FIELDS + DATA_TXT_LIST_FIELDS},
    }


def coverage_summary_row(record: dict[str, Any]) -> dict[str, Any]:
    cov = field_coverage(record)
    row: dict[str, Any] = {
        "event_name": record.get("event_name"),
        "event_url": record.get("event_url"),
        "filled_count": cov["filled_count"],
        "total_count": cov["total_count"],
        "coverage_pct": cov["pct"],
        "missing_fields": ", ".join(cov["missing"]),
        "scope_in_scope": (record.get("scope") or {}).get("in_scope"),
    }
    for key in DATA_TXT_SCALAR_FIELDS + DATA_TXT_LIST_FIELDS:
        row[f"has_{key}"] = cov["fields"].get(key, False)
    return row
