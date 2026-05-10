"""
Enrich listing JSON (e.g. events.json) with event detail + optional satellite pages.

  python enrich_events.py events.json -o events_enriched.json --limit 5 --satellite

Requires: playwright install chromium
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from conference_record import (
    domain_from_url,
    empty_record,
    merge_nonempty,
)
from parse_event_html import (
    parse_event_detail,
    parse_exhibitors_listing,
    parse_speakers_listing,
    parse_sponsors_listing,
)
from playwright_fetch import fetch_page_html
from scope_eval import evaluate_scope, load_taxonomy, map_industry, methodology_description


def _venue_fallback_from_seed(rec: dict[str, Any]) -> None:
    line = (rec.get("listing_metadata") or {}).get("seed_venue_line") or ""
    if not line or rec.get("city"):
        return
    parts = [p.strip() for p in line.split(",") if p.strip()]
    if len(parts) >= 1:
        rec.setdefault("city", parts[0])
    if len(parts) >= 2:
        rec.setdefault("country", parts[-1])
    if not rec.get("address_line_1"):
        rec["address_line_1"] = line


async def enrich_one(
    seed: dict[str, Any],
    *,
    taxonomy: dict[str, Any],
    semaphore: asyncio.Semaphore,
    headed: bool,
    chrome: bool,
    proxy: str | None,
    satellite: bool,
    speakers_fetch: bool,
    timeout: float,
) -> dict[str, Any]:
    url = (seed.get("event_url") or "").strip()
    if not url:
        r = empty_record()
        r["detail_parse_notes"] = ["missing event_url"]
        return r

    async with semaphore:
        try:
            html = await fetch_page_html(
                url,
                headed=headed,
                chrome_channel=chrome,
                proxy=proxy,
                timeout_s=timeout,
            )
        except Exception as e:
            r = empty_record()
            r["event_url"] = url
            r["listing_metadata"] = dict(seed)
            r["detail_parse_notes"] = [f"fetch_error:{e!r}"]
            blob = json.dumps(seed).lower()
            code, label = map_industry(blob, taxonomy)
            r["industry_methodology"] = {"code": code, "label": label}
            r["industry"] = label
            r["scope"].update(evaluate_scope(r, taxonomy))
            return r

    detail = parse_event_detail(html, url, seed=seed)
    notes = list(detail.pop("detail_parse_notes", []) or [])
    meta = detail.pop("listing_metadata", {}) or {}

    rec = empty_record()
    rec["listing_metadata"] = {**seed, **meta}
    merge_nonempty(rec, detail)

    rec["conference_domain"] = domain_from_url(url)
    _venue_fallback_from_seed(rec)

    raw_desc = rec.get("event_description_methodology")
    rec["event_description_methodology"] = methodology_description(
        str(raw_desc) if raw_desc else None
    )

    blob = (
        f"{rec.get('event_name', '')} {rec.get('industry_raw', '')} "
        f"{rec.get('event_description_methodology', '')}"
    ).lower()
    code, label = map_industry(blob, taxonomy)
    rec["industry_methodology"] = {"code": code, "label": label}
    rec["industry"] = label

    if satellite and rec.get("exhibitor_crawler_url"):
        try:
            async with semaphore:
                exh_html = await fetch_page_html(
                    str(rec["exhibitor_crawler_url"]),
                    headed=headed,
                    chrome_channel=chrome,
                    proxy=proxy,
                    timeout_s=timeout,
                )
            ex = parse_exhibitors_listing(exh_html, str(rec["exhibitor_crawler_url"]))
            rec["exhibitor_companies"] = ex.get("exhibitor_companies") or []
            rec["exhibitor_company_websites"] = ex.get("exhibitor_company_websites") or []
            em = ex.get("listing_metadata") or {}
            if em:
                rec["listing_metadata"].update(em)
        except Exception as e:
            notes.append(f"exhibitors_fetch:{e!r}")

    if satellite and rec.get("sponsor_crawler_url"):
        try:
            async with semaphore:
                sp_html = await fetch_page_html(
                    str(rec["sponsor_crawler_url"]),
                    headed=headed,
                    chrome_channel=chrome,
                    proxy=proxy,
                    timeout_s=min(timeout, 60.0),
                )
            sp = parse_sponsors_listing(sp_html, str(rec["sponsor_crawler_url"]))
            if not (sp.get("listing_metadata") or {}).get("sponsors_page_generic"):
                rec["sponsor_companies"] = sp.get("sponsor_companies") or []
                rec["sponsor_company_websites"] = sp.get("sponsor_company_websites") or []
        except Exception as e:
            notes.append(f"sponsors_fetch:{e!r}")

    sp_url = (rec.get("listing_metadata") or {}).get("speakers_tab_url")
    if speakers_fetch and sp_url and "speakers_tab_disabled" not in notes:
        try:
            async with semaphore:
                spk_html = await fetch_page_html(
                    sp_url,
                    headed=headed,
                    chrome_channel=chrome,
                    proxy=proxy,
                    timeout_s=timeout,
                )
            spk = parse_speakers_listing(spk_html, sp_url)
            rec["speakers"] = spk.get("speakers") or []
        except Exception as e:
            notes.append(f"speakers_fetch:{e!r}")

    rec["detail_parse_notes"] = notes
    rec["scope"].update(evaluate_scope(rec, taxonomy))
    return rec


async def run_enrich(
    seeds: list[dict[str, Any]],
    *,
    concurrency: int,
    headed: bool,
    chrome: bool,
    proxy: str | None,
    satellite: bool,
    speakers_fetch: bool,
    timeout: float,
    taxonomy_path: Path | None,
) -> list[dict[str, Any]]:
    tax = load_taxonomy(taxonomy_path)
    sem = asyncio.Semaphore(max(1, concurrency))
    tasks = [
        enrich_one(
            s,
            taxonomy=tax,
            semaphore=sem,
            headed=headed,
            chrome=chrome,
            proxy=proxy,
            satellite=satellite,
            speakers_fetch=speakers_fetch,
            timeout=timeout,
        )
        for s in seeds
    ]
    return await asyncio.gather(*tasks)


def main() -> None:
    p = argparse.ArgumentParser(description="Enrich listing rows with event detail + satellite pages.")
    p.add_argument("input_json", type=Path, help="Listing JSON array (e.g. events.json)")
    p.add_argument("-o", "--out", type=Path, required=True)
    p.add_argument("--limit", type=int, default=0, help="Max events (0 = all)")
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--headed", action="store_true")
    p.add_argument("--chrome", action="store_true", help="Use Google Chrome channel")
    p.add_argument("--proxy", type=str, default=None)
    p.add_argument("--satellite", action="store_true", help="Fetch exhibitors + sponsors pages")
    p.add_argument("--speakers", action="store_true", help="Fetch speakers tab when URL present")
    p.add_argument("--timeout", type=float, default=90.0)
    p.add_argument("--taxonomy", type=Path, default=None)
    args = p.parse_args()

    raw = json.loads(args.input_json.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print("Input must be a JSON array.", file=sys.stderr)
        raise SystemExit(1)
    seeds = raw[: args.limit] if args.limit and args.limit > 0 else raw

    rows = asyncio.run(
        run_enrich(
            seeds,
            concurrency=args.concurrency,
            headed=args.headed,
            chrome=args.chrome,
            proxy=args.proxy,
            satellite=args.satellite,
            speakers_fetch=args.speakers,
            timeout=args.timeout,
            taxonomy_path=args.taxonomy,
        )
    )
    args.out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"written": str(args.out), "count": len(rows)}, indent=2))


if __name__ == "__main__":
    main()
