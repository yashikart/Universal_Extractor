"""Scope classification from extracted text + structure flags."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _text_blob(record: dict[str, Any]) -> str:
    parts = [
        str(record.get("event_name") or ""),
        str(record.get("event_description_methodology") or ""),
        str(record.get("industry_raw") or ""),
        str(record.get("listing_metadata") or {}),
    ]
    return " \n ".join(parts).lower()


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    p = path or Path(__file__).resolve().parent / "taxonomy.json"
    return json.loads(p.read_text(encoding="utf-8"))


def map_industry(blob: str, taxonomy: dict[str, Any]) -> tuple[str | None, str | None]:
    for rule in taxonomy.get("industry_rules", []):
        sub = (rule.get("contains") or "").lower()
        if sub and sub in blob:
            return rule.get("code"), rule.get("label")
    return None, None


def methodology_description(raw: str | None, max_len: int = 1200) -> str | None:
    if not raw:
        return None
    t = " ".join(raw.split())
    if len(t) > max_len:
        t = t[: max_len - 3].rsplit(" ", 1)[0] + "..."
    return t


def evaluate_scope(record: dict[str, Any], taxonomy: dict[str, Any]) -> dict[str, Any]:
    blob = _text_blob(record)
    flags: dict[str, Any] = {}

    b2b_keys = taxonomy.get("b2b_positive_substrings", [])
    penalties = taxonomy.get("consumer_festival_penalty", [])
    flags["b2b"] = any(k in blob for k in b2b_keys) and not any(p in blob for p in penalties)

    net_keys = taxonomy.get("networking_substrings", [])
    flags["networking_opportunities"] = any(k in blob for k in net_keys)

    exh = record.get("exhibitor_companies") or []
    exh_url = record.get("exhibitor_crawler_url")
    exh_n = len(exh) if isinstance(exh, list) else 0
    from_listing = (record.get("listing_metadata") or {}).get("exhibitor_count_estimate")
    flags["has_exhibitors"] = exh_n > 0 or (isinstance(from_listing, int) and from_listing > 0)

    sp = record.get("sponsor_companies") or []
    flags["has_sponsors"] = isinstance(sp, list) and len(sp) > 0

    spk = record.get("speakers") or []
    flags["has_speakers"] = isinstance(spk, list) and len(spk) > 0

    logic = taxonomy.get("in_scope_logic", {})
    req_b2b = logic.get("require_b2b", True)
    req_net = logic.get("require_networking", False)
    req_any = logic.get("require_any_engagement", True)
    eng_fields = logic.get("engagement_fields", [])

    engagement_hit = any(flags.get(f) for f in eng_fields if f in flags)

    in_scope = True
    if req_b2b and not flags["b2b"]:
        in_scope = False
    if req_net and not flags["networking_opportunities"]:
        in_scope = False
    if req_any and not engagement_hit:
        in_scope = False

    flags["in_scope"] = in_scope
    flags["scope_rule_version"] = taxonomy.get("scope_rule_version")

    return flags
