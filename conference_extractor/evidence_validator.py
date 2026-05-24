"""Validate extracted values against saved HTML — drop ungrounded LLM/heuristic noise."""

from __future__ import annotations

import re
from typing import Any

from provenance import (
    LIST_ENTITY_FIELDS,
    SCALAR_FIELDS,
    get_source,
    normalize_for_match,
    stamp,
)

MAX_SPEAKERS = 40
MAX_ENTITIES = 80
PROGRAM_SOON_RE = re.compile(
    r"program will be released|coming soon|to be announced|tba\b",
    re.I,
)


def snippet_in_text(snippet: str, haystack: str, *, min_len: int = 8) -> bool:
    snip = normalize_for_match(snippet)
    if len(snip) < min_len:
        return False
    body = normalize_for_match(haystack)
    if snip in body:
        return True
    # Allow partial match for long snippets (first 40 chars)
    if len(snip) > 40 and snip[:40] in body:
        return True
    # Name-only: require full name tokens present
    words = [w for w in re.split(r"\W+", snip) if len(w) > 2]
    if len(words) >= 2:
        return all(w in body for w in words[:4])
    return False


def scalar_in_text(value: str, haystack: str) -> bool:
    val = normalize_for_match(str(value))
    if not val:
        return False
    if val in normalize_for_match(haystack):
        return True
    if len(val) > 6 and val[: min(30, len(val))] in normalize_for_match(haystack):
        return True
    return False


def _entity_name(item: Any) -> str:
    if isinstance(item, dict):
        return (item.get("name") or item.get("title") or "").strip()
    return str(item).strip()


def _entity_evidence(item: dict[str, Any]) -> str | None:
    return (
        item.get("evidence_snippet")
        or item.get("details")
        or item.get("name")
    )


def filter_entities(
    items: list[Any],
    haystack: str,
    *,
    source: str,
    require_evidence: bool = False,
) -> tuple[list[Any], int]:
    kept: list[Any] = []
    stripped = 0
    for item in items:
        name = _entity_name(item)
        if not name:
            stripped += 1
            continue
        evidence = _entity_evidence(item) if isinstance(item, dict) else name
        if require_evidence or source == "llm":
            if not evidence or not snippet_in_text(str(evidence), haystack):
                stripped += 1
                continue
        elif not snippet_in_text(name, haystack, min_len=4):
            stripped += 1
            continue
        if isinstance(item, dict):
            clean = {k: v for k, v in item.items() if k != "evidence_snippet"}
            kept.append(clean)
        else:
            kept.append(item)
    if len(kept) > MAX_ENTITIES:
        stripped += len(kept) - MAX_ENTITIES
        kept = kept[:MAX_ENTITIES]
    return kept, stripped


def filter_speakers(items: list[Any], haystack: str, *, source: str) -> tuple[list[Any], int]:
    kept, stripped = filter_entities(items, haystack, source=source, require_evidence=(source == "llm"))
    if len(kept) > MAX_SPEAKERS:
        stripped += len(kept) - MAX_SPEAKERS
        kept = kept[:MAX_SPEAKERS]
    return kept, stripped


def validate_llm_record(
    llm_data: dict[str, Any],
    combined_html_text: str,
    provenance: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Drop LLM fields not grounded in page text. Returns (cleaned, stripped_count)."""
    stripped = 0
    out: dict[str, Any] = {}

    for field in SCALAR_FIELDS:
        val = llm_data.get(field)
        if val is None or val == "":
            continue
        if isinstance(val, str) and scalar_in_text(val, combined_html_text):
            out[field] = val
            stamp(
                provenance,
                field,
                source="llm",
                snippet=str(val)[:120],
            )
        else:
            stripped += 1

    for field in LIST_ENTITY_FIELDS:
        raw = llm_data.get(field) or []
        if not raw:
            continue
        if field == "speakers":
            filtered, n = filter_speakers(raw, combined_html_text, source="llm")
        else:
            filtered, n = filter_entities(raw, combined_html_text, source="llm", require_evidence=True)
        stripped += n
        if filtered:
            out[field] = filtered
            stamp(provenance, field, source="llm", snippet=_entity_name(filtered[0]))

    return out, stripped


def merge_llm_safe(
    record: dict[str, Any],
    llm_data: dict[str, Any],
    provenance: dict[str, Any],
) -> int:
    """Merge validated LLM data — only fills empty scalars; lists append with dedupe."""
    from conference_record import merge_nonempty

    merged = 0
    for field in SCALAR_FIELDS:
        if record.get(field) not in (None, "", []):
            continue
        val = llm_data.get(field)
        if val is None or val == "":
            continue
        record[field] = val
        merged += 1

    for field in LIST_ENTITY_FIELDS:
        new_items = llm_data.get(field) or []
        if not new_items:
            continue
        existing = record.get(field) or []
        if not existing:
            record[field] = new_items
            merged += 1
            continue
        seen = {_entity_name(x).lower() for x in existing if _entity_name(x)}
        for item in new_items:
            name = _entity_name(item).lower()
            if name and name not in seen:
                existing.append(item)
                seen.add(name)
                merged += 1
        record[field] = existing

    merge_nonempty(record, {})
    return merged


def validate_list_fields(
    record: dict[str, Any],
    combined_html_text: str,
    provenance: dict[str, Any],
) -> int:
    """Re-validate heuristic list fields against HTML."""
    stripped = 0
    for field in LIST_ENTITY_FIELDS:
        raw = record.get(field) or []
        if not raw:
            continue
        src = get_source(provenance, field) or "heuristic"
        if field == "speakers":
            filtered, n = filter_speakers(raw, combined_html_text, source=src)
        else:
            filtered, n = filter_entities(raw, combined_html_text, source=src)
        stripped += n
        if filtered:
            record[field] = filtered
        else:
            record[field] = []
            if field in provenance:
                del provenance[field]
    return stripped


def program_not_published(combined_html_text: str) -> bool:
    return bool(PROGRAM_SOON_RE.search(combined_html_text or ""))


def clear_unpublished_lists(
    record: dict[str, Any],
    combined_html_text: str,
    provenance: dict[str, Any] | None = None,
) -> int:
    """Clear list fields only when agenda is TBA and data is not from a dedicated subpage."""
    if not program_not_published(combined_html_text):
        return 0
    cleared = 0
    prov = provenance or {}
    for field in ("speakers", "attending_companies"):
        if not record.get(field):
            continue
        if field == "speakers":
            role = (prov.get("speakers") or {}).get("page_role")
            if role in ("speakers", "program"):
                continue
        record[field] = []
        cleared += 1
    return cleared
