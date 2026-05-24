"""Field-level provenance: where each datapoint came from (anti-hallucination audit trail)."""

from __future__ import annotations

import re
from typing import Any

# Higher number = more trusted; LLM cannot overwrite higher-priority sources.
SOURCE_PRIORITY: dict[str, int] = {
    "seed": 10,
    "json_ld": 90,
    "meta": 80,
    "listing": 75,
    "heuristic": 60,
    "regex": 55,
    "taxonomy": 50,
    "derived": 40,
    "llm": 20,
}

LIST_ENTITY_FIELDS = (
    "attending_companies",
    "exhibitor_companies",
    "sponsor_companies",
    "speakers",
)

SCALAR_FIELDS = (
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
)


def empty_provenance() -> dict[str, Any]:
    return {}


def _has_value(val: Any) -> bool:
    if val is None or val == "":
        return False
    if isinstance(val, list):
        return len(val) > 0
    return True


def stamp(
    provenance: dict[str, Any],
    field: str,
    *,
    source: str,
    page_url: str | None = None,
    page_role: str | None = None,
    snippet: str | None = None,
) -> None:
    provenance[field] = {
        "source": source,
        "page_url": page_url,
        "page_role": page_role,
        "snippet": (snippet or "")[:200] or None,
    }


def stamp_fields(
    provenance: dict[str, Any],
    record: dict[str, Any],
    fields: list[str],
    *,
    source: str,
    page_url: str | None = None,
    page_role: str | None = None,
) -> None:
    for field in fields:
        if _has_value(record.get(field)):
            snippet = None
            val = record.get(field)
            if isinstance(val, str):
                snippet = val[:120]
            stamp(
                provenance,
                field,
                source=source,
                page_url=page_url,
                page_role=page_role,
                snippet=snippet,
            )


def get_source(provenance: dict[str, Any], field: str) -> str | None:
    entry = provenance.get(field)
    if isinstance(entry, dict):
        return entry.get("source")
    return None


def source_priority(source: str | None) -> int:
    if not source:
        return 0
    return SOURCE_PRIORITY.get(source, 0)


def can_overwrite(provenance: dict[str, Any], field: str, new_source: str) -> bool:
    current = get_source(provenance, field)
    if not current:
        return True
    return source_priority(new_source) >= source_priority(current)


def merge_provenance(
    target: dict[str, Any],
    incoming: dict[str, Any],
    *,
    only_fields: set[str] | None = None,
) -> None:
    for field, entry in incoming.items():
        if only_fields is not None and field not in only_fields:
            continue
        if not isinstance(entry, dict):
            continue
        new_source = entry.get("source")
        if can_overwrite(target, field, new_source or ""):
            target[field] = entry


def merge_record_with_provenance(
    target: dict[str, Any],
    target_prov: dict[str, Any],
    source: dict[str, Any],
    source_prov: dict[str, Any],
) -> None:
    """Merge source into target; provenance follows priority rules."""
    from conference_record import merge_nonempty

    for field in list(SCALAR_FIELDS) + list(LIST_ENTITY_FIELDS):
        val = source.get(field)
        if not _has_value(val):
            continue
        new_src = get_source(source_prov, field)
        if not can_overwrite(target_prov, field, new_src or "heuristic"):
            continue
        if target.get(field) in (None, "", []) or field in LIST_ENTITY_FIELDS:
            if field in LIST_ENTITY_FIELDS and _has_value(target.get(field)):
                existing = target.get(field) or []
                if isinstance(existing, list) and isinstance(val, list):
                    target[field] = existing + val
                else:
                    target[field] = val
            else:
                target[field] = val
            if new_src:
                target_prov[field] = source_prov.get(field, {"source": new_src})
        else:
            merge_nonempty(target, {field: val})
            if new_src and field not in target_prov:
                target_prov[field] = source_prov.get(field, {"source": new_src})


def attach_provenance(record: dict[str, Any]) -> dict[str, Any]:
    if "field_provenance" not in record:
        record["field_provenance"] = empty_provenance()
    return record["field_provenance"]


def provenance_verified_count(provenance: dict[str, Any], record: dict[str, Any]) -> int:
    count = 0
    for field in SCALAR_FIELDS + LIST_ENTITY_FIELDS:
        if not _has_value(record.get(field)):
            continue
        src = get_source(provenance, field)
        if src and src != "llm":
            count += 1
        elif src == "llm" and provenance.get(field, {}).get("snippet"):
            count += 1
    return count


def normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())
