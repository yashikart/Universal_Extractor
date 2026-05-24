"""Write enriched records to CSV (JSON columns for list fields)."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

JSON_COLUMNS = (
    "attending_companies",
    "exhibitor_companies",
    "exhibitor_company_websites",
    "sponsor_companies",
    "sponsor_company_websites",
    "speakers",
    "resolution_notes",
    "enrich_notes",
    "field_provenance",
    "validation",
    "scope_evidence",
)

SCOPE_COLUMNS = (
    "scope_in_scope",
    "scope_b2b",
    "scope_networking",
    "scope_has_exhibitors",
    "scope_has_sponsors",
    "scope_has_speakers",
    "scope_rule_version",
)

FLAT_COLUMNS = (
    "conference_domain",
    "conference_group_name",
    "conference_group_description",
    "industry_methodology",
    "event_name",
    "start_date",
    "end_date",
    "event_url",
    "listing_url",
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
    "resolution_confidence",
) + JSON_COLUMNS + SCOPE_COLUMNS


def _flatten_row(record: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for col in FLAT_COLUMNS:
        if col in SCOPE_COLUMNS:
            continue
        val = record.get(col)
        if col == "scope_evidence":
            val = (record.get("scope") or {}).get("scope_evidence")
        if col in JSON_COLUMNS:
            row[col] = json.dumps(val if val is not None else [], ensure_ascii=False)
        else:
            row[col] = val

    scope = record.get("scope") or {}
    row["scope_in_scope"] = scope.get("in_scope")
    row["scope_b2b"] = scope.get("b2b")
    row["scope_networking"] = scope.get("networking")
    row["scope_has_exhibitors"] = scope.get("has_exhibitors")
    row["scope_has_sponsors"] = scope.get("has_sponsors")
    row["scope_has_speakers"] = scope.get("has_speakers")
    row["scope_rule_version"] = scope.get("scope_rule_version")
    row["scope_evidence"] = scope.get("scope_evidence")
    return row


def write_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [_flatten_row(r) for r in records]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(FLAT_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
