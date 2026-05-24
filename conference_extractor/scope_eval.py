"""Scope and industry classification from taxonomy.json (data.txt criteria)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    p = path or Path(__file__).resolve().parent / "taxonomy.json"
    return json.loads(p.read_text(encoding="utf-8"))


def map_industry(blob: str, taxonomy: dict[str, Any]) -> tuple[str | None, str | None]:
    lower = (blob or "").lower()
    for rule in taxonomy.get("industry_rules", []):
        for sub in rule.get("substrings", []):
            if sub.lower() in lower:
                return rule.get("code"), rule.get("label")
    return None, None


def methodology_description(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if len(text) > 500:
        return text[:497].rstrip() + "..."
    return text


def evaluate_scope(record: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    blob = " ".join(
        str(record.get(k) or "")
        for k in (
            "event_name",
            "event_description_methodology",
            "industry_raw",
            "industry",
            "conference_group_description",
        )
    ).lower()

    b2b_keys = taxonomy.get("b2b_positive_substrings", [])
    penalties = taxonomy.get("consumer_festival_penalty", [])
    b2b = any(k in blob for k in b2b_keys) and not any(p in blob for p in penalties)

    net_keys = taxonomy.get("networking_substrings", [])
    networking = any(k in blob for k in net_keys)

    has_exhibitors = bool(record.get("exhibitor_companies"))
    has_sponsors = bool(record.get("sponsor_companies"))
    speakers = record.get("speakers") or []
    has_speakers = bool(speakers)

    logic = taxonomy.get("in_scope_logic", {})
    engagement = has_exhibitors or has_sponsors or has_speakers
    if logic.get("require_engagement"):
        if logic.get("require_b2b", True):
            in_scope = b2b and (networking or engagement)
        else:
            in_scope = networking or engagement
    else:
        in_scope = b2b

    b2b_hits = [k for k in b2b_keys if k in blob]
    net_hits = [k for k in net_keys if k in blob]
    penalty_hits = [p for p in penalties if p in blob]

    return {
        "b2b": b2b,
        "networking": networking,
        "has_exhibitors": has_exhibitors,
        "has_sponsors": has_sponsors,
        "has_speakers": has_speakers,
        "in_scope": in_scope,
        "scope_rule_version": taxonomy.get("scope_rule_version"),
        "scope_evidence": {
            "b2b_hits": b2b_hits[:8],
            "networking_hits": net_hits[:8],
            "consumer_penalties": penalty_hits[:5],
            "engagement": {
                "exhibitors": has_exhibitors,
                "sponsors": has_sponsors,
                "speakers": has_speakers,
            },
        },
    }
