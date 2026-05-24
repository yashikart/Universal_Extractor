"""Enrich listing events: resolve official URL → fetch → parse → scope → CSV."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from env_config import ci_official_search_enabled, load_env
from conference_record import domain_from_url, empty_record, merge_nonempty
from csv_export import write_csv
from data_txt_fields import coverage_summary_row
from event_inspect import event_inspect_dir, save_pages_bundle, write_coverage
from extract_listing import extract_listing_page
from parse_event_site import parse_event_pages
from playwright_fetch import fetch_page
from review_export import write_review_csv
from subpage_crawl import crawl_all_subpages, merge_crawler_urls
from web_search import resolve_official_url

ProgressFn = Callable[[int, int, str], None]


async def _fetch_page_tuple(url: str) -> tuple[bool, str, str]:
    result = await fetch_page(url)
    return result.ok, result.html or "", result.note or ""


def _noop_progress(done: int, total: int, msg: str) -> None:
    pass


def _is_listing_host(url: str | None) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    return any(
        x in host
        for x in ("conferenceindex.org", "10times.com", "allevents.in", "eventbrite")
    )


def _seed_from_listing(item: dict[str, Any]) -> dict[str, Any]:
    record = empty_record()
    name = item.get("name") or item.get("title") or item.get("event_name")
    url = item.get("event_url") or item.get("url") or item.get("href")
    record["event_name"] = name
    record["listing_url"] = url
    if url:
        record["event_url"] = url
    if item.get("start_date"):
        record["start_date"] = item["start_date"]
    if item.get("end_date"):
        record["end_date"] = item["end_date"]
    if item.get("venue"):
        record["venue_name"] = item["venue"]
    return record


async def enrich_one(
    item: dict[str, Any],
    *,
    inspect_dir: Path | None = None,
    on_step: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    def step(msg: str) -> None:
        if on_step:
            on_step(msg)

    seed = _seed_from_listing(item)
    name = seed.get("event_name") or "Unknown event"
    listing_url = seed.get("listing_url")

    pages: dict[str, str] = {}
    page_urls: dict[str, str] = {}
    notes: list[str] = []
    listing_meta: dict[str, Any] = {}
    listing_only = _is_listing_host(listing_url)

    if listing_url:
        step(f"Inspect {name} — listing page")
        listing_fetch = await fetch_page(listing_url)
        if listing_fetch.ok:
            pages["listing"] = listing_fetch.html
            page_urls["listing"] = listing_url
            listing_meta = extract_listing_page(listing_fetch.html, listing_url)
            merge_nonempty(seed, listing_meta)
            program_url = listing_meta.get("program_url")
            if program_url:
                step(f"Inspect {name} — program page")
                program_fetch = await fetch_page(program_url)
                if program_fetch.ok:
                    pages["program"] = program_fetch.html
                    page_urls["program"] = program_url
            notes.append(f"listing_fetch: {listing_fetch.note}")
        else:
            notes.append(f"listing_fetch_failed: {listing_fetch.note}")

    skip_official_search = listing_only and not ci_official_search_enabled()
    if skip_official_search:
        official = None
        confidence = 0.0
        resolution_notes = [
            "Aggregator listing — official site search skipped "
            "(set TAVILY_API_KEY in .env to enable Tavily search)"
        ]
    else:
        if listing_only:
            step(f"Search {name} — official site (Tavily)")
        official, confidence, resolution_notes = await resolve_official_url(
            name, listing_url, listing_context=listing_meta
        )
    seed["resolution_confidence"] = confidence
    seed["resolution_notes"] = resolution_notes

    fetch_url = official
    if not fetch_url or _is_listing_host(fetch_url):
        fetch_url = listing_url

    if fetch_url and fetch_url != listing_url:
        step(f"Inspect {name} — official site")
        main = await fetch_page(fetch_url)
        if main.ok:
            pages["main"] = main.html
            page_urls["main"] = fetch_url
            extra_pages, extra_urls, crawler_urls = await crawl_all_subpages(
                main.html,
                fetch_url,
                _fetch_page_tuple,
                max_pages_per_role=5,
            )
            merge_crawler_urls(seed, crawler_urls)
            merge_crawler_urls(listing_meta, crawler_urls)
            for role, sub_url in extra_urls.items():
                if role in pages:
                    continue
                step(f"Inspect {name} — {role} page")
            pages.update(extra_pages)
            page_urls.update(extra_urls)
            notes.append(f"official_fetch: {main.note}")
        else:
            notes.append(f"official_fetch_failed: {main.note}")
            if listing_url and "listing" not in pages:
                retry = await fetch_page(listing_url)
                if retry.ok:
                    pages["listing"] = retry.html

    if not pages:
        seed["enrich_notes"] = notes + ["no pages fetched"]
        return seed

    event_dir: Path | None = None
    if inspect_dir is not None:
        event_dir = event_inspect_dir(inspect_dir, item)
        save_pages_bundle(event_dir, pages, urls=page_urls, notes=notes)

    step(f"Extract {name} — all data.txt fields")
    event_url = official or listing_url or fetch_url or ""
    parsed = parse_event_pages(pages, event_url, seed=seed)
    parsed["resolution_confidence"] = confidence
    parsed["resolution_notes"] = resolution_notes
    parsed["enrich_notes"] = list(parsed.get("enrich_notes") or []) + notes
    parsed["enrich_notes"].append(f"pages_fetched: {', '.join(pages.keys())}")

    if official and not _is_listing_host(official):
        parsed["event_url"] = official
        parsed["conference_domain"] = domain_from_url(official)
    elif listing_url:
        parsed["event_url"] = listing_url
        parsed["listing_url"] = listing_url
        parsed["conference_domain"] = domain_from_url(listing_url)

    if event_dir is not None:
        cov = write_coverage(event_dir, parsed)
        parsed.setdefault("enrich_notes", []).append(
            f"inspect: {event_dir} ({cov['filled_count']}/{cov['total_count']} fields)"
        )

    return parsed


async def enrich_events_async(
    items: list[dict[str, Any]],
    *,
    limit: int | None = None,
    concurrency: int = 2,
    inspect_dir: Path | None = None,
    on_progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    progress = on_progress or _noop_progress
    batch = items[:limit] if limit else items
    total = len(batch)
    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[dict[str, Any]] = [empty_record() for _ in batch]

    async def worker(idx: int, item: dict[str, Any]) -> None:
        async with sem:
            label = item.get("name") or item.get("title") or item.get("event_name") or f"#{idx + 1}"

            def on_step(msg: str) -> None:
                progress(idx, total, msg)

            progress(idx, total, f"Event {idx + 1}/{total}: {label}")
            results[idx] = await enrich_one(
                item,
                inspect_dir=inspect_dir,
                on_step=on_step,
            )
            progress(idx + 1, total, f"Done {label}")

    await asyncio.gather(*(worker(i, it) for i, it in enumerate(batch)))
    return results


def enrich_events(
    items: list[dict[str, Any]],
    *,
    limit: int | None = None,
    concurrency: int = 2,
    inspect_dir: Path | None = None,
    on_progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    load_env()
    return asyncio.run(
        enrich_events_async(
            items,
            limit=limit,
            concurrency=concurrency,
            inspect_dir=inspect_dir,
            on_progress=on_progress,
        )
    )


def load_events_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return data["events"]
    raise ValueError(f"Unsupported events JSON shape in {path}")


def enrich_file(
    events_path: Path,
    out_csv: Path,
    *,
    limit: int | None = None,
    inspect_dir: Path | None = None,
    on_progress: ProgressFn | None = None,
) -> list[dict[str, Any]]:
    items = load_events_json(events_path)
    records = enrich_events(
        items,
        limit=limit,
        inspect_dir=inspect_dir,
        on_progress=on_progress,
    )
    write_csv(records, out_csv)
    if records:
        import csv

        cov_path = out_csv.with_name("events_coverage.csv")
        with cov_path.open("w", newline="", encoding="utf-8") as f:
            rows = [coverage_summary_row(r) for r in records]
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        write_review_csv(records, out_csv.with_name("events_review.csv"))
    return records


if __name__ == "__main__":
    import argparse

    load_env()

    parser = argparse.ArgumentParser(description="Enrich conference listing events to CSV")
    parser.add_argument("events_json", type=Path, help="events.json from listing extract")
    parser.add_argument("--out", type=Path, default=Path("events_enriched.csv"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--inspect-dir",
        type=Path,
        default=Path("inspect_out/events"),
        help="Save per-event HTML + coverage.json under this directory",
    )
    args = parser.parse_args()

    def _cli_progress(done: int, total: int, msg: str) -> None:
        print(f"[{done}/{total}] {msg}")

    enrich_file(
        args.events_json,
        args.out,
        limit=args.limit,
        inspect_dir=args.inspect_dir,
        on_progress=_cli_progress,
    )
    print(f"Wrote {args.out}")
    print(f"Wrote {args.out.with_name('events_coverage.csv')}")
    print(f"Wrote {args.out.with_name('events_review.csv')}")
