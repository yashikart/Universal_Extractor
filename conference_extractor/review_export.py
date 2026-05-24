"""Review queue CSV — flags rows that need human verification."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from data_txt_fields import field_coverage
from provenance import get_source, provenance_verified_count

CONFIDENCE_REVIEW_THRESHOLD = 0.5
PARTIAL_COVERAGE_THRESHOLD = 0.45


def _is_listing_host(url: str | None) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    return any(
        x in host
        for x in ("conferenceindex.org", "10times.com", "allevents.in", "eventbrite")
    )


def data_completeness(record: dict[str, Any]) -> str:
    cov = field_coverage(record)
    pct = cov["pct"]
    if pct >= 70:
        return "full"
    if pct >= 40:
        return "partial"
    return "minimal"


def build_review_reasons(record: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    prov = record.get("field_provenance") or {}
    cov = field_coverage(record)
    listing_url = record.get("listing_url") or record.get("event_url")
    official = record.get("event_url")
    confidence = record.get("resolution_confidence")
    validation = record.get("validation") or {}

    if validation.get("llm_stripped", 0) > 0:
        reasons.append(f"llm_ungrounded:{validation['llm_stripped']}")
    if validation.get("heuristic_stripped", 0) > 0:
        reasons.append(f"heuristic_ungrounded:{validation['heuristic_stripped']}")
    if validation.get("program_unpublished_lists_cleared"):
        reasons.append("program_not_published")

    if confidence is not None and confidence < CONFIDENCE_REVIEW_THRESHOLD:
        if official and listing_url and _normalize_url(official) != _normalize_url(listing_url):
            reasons.append("low_official_url_confidence")

    if _is_listing_host(listing_url) and _is_listing_host(official):
        reasons.append("aggregator_only_no_official_site")

    if cov["pct"] < PARTIAL_COVERAGE_THRESHOLD * 100:
        reasons.append("low_field_coverage")

    scope = record.get("scope") or {}
    if scope.get("in_scope") and not (
        scope.get("has_exhibitors") or scope.get("has_sponsors") or scope.get("has_speakers")
    ):
        reasons.append("in_scope_without_engagement_lists")

    unverified_lists = 0
    for field in ("speakers", "exhibitor_companies", "sponsor_companies"):
        if record.get(field) and get_source(prov, field) == "llm":
            unverified_lists += 1
    if unverified_lists:
        reasons.append("llm_sourced_lists")

    notes = record.get("enrich_notes") or []
    if any("location mismatch" in str(n).lower() for n in notes):
        reasons.append("official_url_location_mismatch")

    return reasons


def _normalize_url(url: str | None) -> str:
    return (url or "").rstrip("/").lower()


def needs_review(record: dict[str, Any]) -> bool:
    return len(build_review_reasons(record)) > 0


def review_row(record: dict[str, Any]) -> dict[str, Any]:
    cov = field_coverage(record)
    prov = record.get("field_provenance") or {}
    validation = record.get("validation") or {}
    reasons = build_review_reasons(record)
    scope = record.get("scope") or {}

    return {
        "event_name": record.get("event_name"),
        "event_url": record.get("event_url"),
        "listing_url": record.get("listing_url"),
        "needs_review": needs_review(record),
        "review_reasons": "; ".join(reasons),
        "data_completeness": data_completeness(record),
        "coverage_pct": cov["pct"],
        "filled_count": cov["filled_count"],
        "missing_fields": ", ".join(cov["missing"]),
        "resolution_confidence": record.get("resolution_confidence"),
        "verified_field_count": provenance_verified_count(prov, record),
        "llm_stripped": validation.get("llm_stripped", 0),
        "heuristic_stripped": validation.get("heuristic_stripped", 0),
        "scope_in_scope": scope.get("in_scope"),
        "scope_b2b": scope.get("b2b"),
        "scope_networking": scope.get("networking"),
        "scope_evidence": "; ".join((scope.get("scope_evidence") or {}).get("b2b_hits", [])[:3]),
    }


def write_review_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [review_row(r) for r in records]
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def split_confidence_exports(
    records: list[dict[str, Any]],
    out_dir: Path,
    *,
    base_name: str = "events_enriched",
) -> tuple[Path, Path]:
    """Write high-confidence and review CSV paths (review rows duplicated for queue)."""
    from csv_export import write_csv

    high = [r for r in records if not needs_review(r)]
    review = [r for r in records if needs_review(r)]

    high_path = out_dir / f"{base_name}.csv"
    review_path = out_dir / f"{base_name}_review.csv"

    write_csv(high if high else records, high_path)
    write_review_csv(review if review else records, review_path)
    return high_path, review_path
