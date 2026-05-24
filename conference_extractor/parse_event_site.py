"""Merge multi-page HTML into a full conference record (heuristics + optional LLM)."""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from conference_record import empty_record, finalize_record, merge_nonempty, merge_records
from evidence_validator import (
    clear_unpublished_lists,
    merge_llm_safe,
    validate_list_fields,
    validate_llm_record,
)
from extract_heuristics import extract_from_html, soup_to_text
from extract_llm import extract_with_llm_sync, llm_available
from provenance import merge_provenance
from scope_eval import evaluate_scope, load_taxonomy, map_industry, methodology_description


def parse_event_pages(
    pages: dict[str, str],
    event_url: str,
    seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record = empty_record()
    provenance: dict[str, Any] = {}
    validation: dict[str, Any] = {
        "llm_stripped": 0,
        "heuristic_stripped": 0,
        "program_unpublished_lists_cleared": 0,
        "llm_merged_fields": 0,
    }

    if seed:
        merge_nonempty(record, seed)
        if seed.get("listing_url"):
            provenance.setdefault(
                "listing_url",
                {"source": "seed", "page_url": seed.get("listing_url"), "page_role": "listing"},
            )

    partials: list[dict[str, Any]] = []
    combined_text: list[str] = []

    for role, html in pages.items():
        norm_role = role
        if role.startswith("exhibitors"):
            norm_role = "exhibitors"
        elif role.startswith("sponsors"):
            norm_role = "sponsors"
        elif role.startswith("speakers") or role == "program":
            norm_role = "speakers"
        partial = extract_from_html(html, event_url, page_role=norm_role)
        partial_prov = partial.pop("_provenance", {})
        partials.append(partial)
        merge_provenance(provenance, partial_prov)
        soup = BeautifulSoup(html, "html.parser")
        combined_text.append(f"--- {role} ---\n{soup_to_text(soup, 6000)}")

    merge_records(record, *partials)
    combined_html_text = "\n\n".join(combined_text)

    validation["heuristic_stripped"] = validate_list_fields(
        record, combined_html_text, provenance
    )
    validation["program_unpublished_lists_cleared"] = clear_unpublished_lists(
        record, combined_html_text, provenance
    )

    use_llm = llm_available() and "conferenceindex.org" not in (event_url or "").lower()
    if use_llm:
        llm_raw = extract_with_llm_sync(
            combined_html_text,
            event_name=record.get("event_name"),
            event_url=event_url,
        )
        llm_prov: dict[str, Any] = {}
        llm_clean, llm_stripped = validate_llm_record(
            llm_raw, combined_html_text, llm_prov
        )
        validation["llm_stripped"] = llm_stripped
        validation["llm_merged_fields"] = merge_llm_safe(record, llm_clean, provenance)
        merge_provenance(provenance, llm_prov)
        record.setdefault("enrich_notes", []).append(
            f"llm: used (stripped {llm_stripped} ungrounded)"
        )
    elif llm_available():
        record.setdefault("enrich_notes", []).append("llm: skipped (listing-only source)")

    record["event_url"] = event_url
    blob = " ".join(
        filter(
            None,
            [
                record.get("event_name"),
                record.get("event_description_methodology"),
                record.get("conference_group_description"),
            ],
        )
    )
    code, label = map_industry(blob, load_taxonomy())
    if not record.get("industry_methodology") and code:
        record["industry_methodology"] = code
        provenance["industry_methodology"] = {
            "source": "taxonomy",
            "page_url": event_url,
            "page_role": "derived",
            "snippet": code,
        }
    if not record.get("industry") and label:
        record["industry"] = label
        provenance["industry"] = {
            "source": "taxonomy",
            "page_url": event_url,
            "page_role": "derived",
            "snippet": label,
        }

    desc = record.get("event_description_methodology")
    if desc:
        record["event_description_methodology"] = methodology_description(desc)

    record["scope"] = evaluate_scope(record, load_taxonomy())
    record["field_provenance"] = provenance
    record["validation"] = validation
    record.setdefault("enrich_notes", []).append(
        f"validation: stripped llm={validation['llm_stripped']} "
        f"heuristic={validation['heuristic_stripped']}"
    )
    return finalize_record(record)


def parse_event_html(
    html: str,
    event_url: str,
    seed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return parse_event_pages({"main": html}, event_url, seed=seed)
