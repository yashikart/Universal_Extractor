"""
Deterministic extraction of event rows from saved listing HTML.

Works across sites for:
  - ``window.open('https://…')`` / ``window.open("https://…")`` — **any** http(s) URL
  - Schema.org JSON-LD: ``ItemList`` of ``Event`` (standard pattern)

DOM selectors below match common **event-directory** listing markup (and similar clones).
If your site uses different ``id``/classes, extend or fork ``parse_listing_table`` / ``parse_featured``.

Usage (from the ``conference_extractor`` directory, after ``inspect_page.py`` has created ``inspect_out/page.html``):

  python listing_html_extract.py inspect_out/page.html --out events.json
  python listing_html_extract.py inspect_out/page.html --jsonl events.jsonl

Next step: merge detail + exhibitor/sponsor/speaker and scope with
``python enrich_events.py events.json --out events_enriched.json --satellite --speakers``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup
from bs4.element import Tag

# First quoted http(s) URL inside window.open( … ) — any host.
WINDOW_OPEN_RE = re.compile(
    r"""window\.open\s*\(\s*(['"])(https?://[^'"]+)\1""",
    re.IGNORECASE,
)


def _canonical_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme.lower(), (parsed.netloc or "").lower(), path, "", "", "")
    )


def _first_window_open_url(tag: Tag | None) -> str | None:
    if not tag:
        return None
    for attr in ("onclick", "oncontextmenu"):
        val = tag.get(attr)
        if not val:
            continue
        m = WINDOW_OPEN_RE.search(val)
        if m:
            return m.group(2)
    return None


def _event_time_div(td: Tag) -> Tag | None:
    return td.find("div", class_=lambda c: bool(c) and "eventTime" in c.split())  # type: ignore[arg-type]


def parse_listing_table(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Rows in ``table#listing-events`` (directory-style card table)."""
    out: list[dict[str, Any]] = []
    table = soup.find("table", id="listing-events")
    if not table or not isinstance(table, Tag):
        return out
    tbody = table.find("tbody")
    if not tbody or not isinstance(tbody, Tag):
        return out
    for tr in tbody.find_all("tr", class_=lambda c: bool(c) and "event-card" in c.split()):  # type: ignore[arg-type]
        if not isinstance(tr, Tag):
            continue
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 2:
            continue
        name_td = tds[1]
        event_url = _first_window_open_url(name_td) or _first_window_open_url(
            name_td.find("h2")
        )
        if not event_url:
            continue
        h2 = name_td.find("h2")
        edition_id = h2.get("data-edition") if h2 else None
        event_id = name_td.get("data-id") or (h2.get("id") if h2 else None)
        et = _event_time_div(tds[0])
        start_date = et.get("data-start-date") if et else None
        end_date = et.get("data-end-date") if et else None
        date_display = (et.get_text(" ", strip=True) if et else None) or (
            tds[0].get_text(" ", strip=True) or None
        )
        name_text = (h2.get_text(" ", strip=True) if h2 else None) or name_td.get_text(
            " ", strip=True
        )
        venue = tds[2].get_text(" ", strip=True) if len(tds) > 2 else None
        description = tds[3].get_text(" ", strip=True) if len(tds) > 3 else None
        categories = tds[4].get_text(" ", strip=True) if len(tds) > 4 else None
        footer_stats = tds[5].get_text(" ", strip=True) if len(tds) > 5 else None
        out.append(
            {
                "event_url": event_url,
                "source": "listing",
                "event_id": str(event_id) if event_id is not None else None,
                "edition_id": str(edition_id) if edition_id is not None else None,
                "name": name_text or None,
                "date_display": date_display,
                "start_date": start_date,
                "end_date": end_date,
                "venue": venue or None,
                "description": description or None,
                "categories": categories or None,
                "footer_stats": footer_stats or None,
            }
        )
    return out


def parse_featured(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Premium rail: ``#featured-events`` (directory-style)."""
    out: list[dict[str, Any]] = []
    root = soup.find("div", id="featured-events")
    if not root or not isinstance(root, Tag):
        return out
    for card in root.find_all(
        "div", class_=lambda c: bool(c) and "premium-event" in c.split() and "event-card" in c.split()  # type: ignore[arg-type]
    ):
        if not isinstance(card, Tag):
            continue
        el = None
        for cand in card.find_all(attrs={"onclick": True}):
            if WINDOW_OPEN_RE.search(cand.get("onclick") or ""):
                el = cand
                break
        event_url = _first_window_open_url(el) if el else None
        if not event_url:
            continue
        et = card.find("div", class_=lambda c: bool(c) and "eventTime" in c.split())  # type: ignore[arg-type]
        title_el = card.find(["h2", "h3"]) or el
        name_text = title_el.get_text(" ", strip=True) if title_el else None
        out.append(
            {
                "event_url": event_url,
                "source": "featured",
                "event_id": str(el.get("data-id")) if el and el.get("data-id") is not None else None,
                "edition_id": str(el.get("data-edition")) if el and el.get("data-edition") is not None else None,
                "name": name_text or None,
                "date_display": (et.get_text(" ", strip=True) if et else None),
                "start_date": et.get("data-start-date") if et else None,
                "end_date": et.get("data-end-date") if et else None,
                "venue": None,
                "description": None,
                "categories": None,
                "footer_stats": None,
            }
        )
    return out


def _event_url_from_jsonld_item(item: dict[str, Any]) -> str | None:
    u = item.get("url")
    if isinstance(u, str) and u.startswith("http"):
        return u
    loc = item.get("location")
    if isinstance(loc, list):
        for block in loc:
            if isinstance(block, dict):
                u2 = block.get("url")
                if isinstance(u2, str) and u2.startswith("http"):
                    return u2
    if isinstance(loc, dict):
        u2 = loc.get("url")
        if isinstance(u2, str) and u2.startswith("http"):
            return u2
    return None


def parse_jsonld_events(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Any page with ``ItemList`` / ``Event`` JSON-LD."""
    out: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if data.get("@type") != "ItemList":
            continue
        elements = data.get("itemListElement") or []
        for entry in elements:
            if not isinstance(entry, dict):
                continue
            item = entry.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("@type") != "Event":
                continue
            event_url = _event_url_from_jsonld_item(item)
            if not event_url:
                continue
            loc = item.get("location")
            venue_name = None
            if isinstance(loc, list):
                for block in loc:
                    if isinstance(block, dict) and block.get("@type") == "Place":
                        venue_name = block.get("name")
                        break
            elif isinstance(loc, dict) and loc.get("@type") == "Place":
                venue_name = loc.get("name")
            out.append(
                {
                    "event_url": event_url,
                    "source": "jsonld",
                    "event_id": None,
                    "edition_id": None,
                    "name": item.get("name"),
                    "date_display": None,
                    "start_date": item.get("startDate"),
                    "end_date": item.get("endDate"),
                    "venue": venue_name,
                    "description": None,
                    "categories": None,
                    "footer_stats": None,
                    "jsonld_position": entry.get("position"),
                    "jsonld_image": item.get("image"),
                }
            )
    return out


def merge_events(
    listing: list[dict[str, Any]],
    featured: list[dict[str, Any]],
    jsonld: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}

    def put(rec: dict[str, Any]) -> None:
        key = _canonical_url(rec["event_url"])
        if not key:
            return
        if key not in merged:
            merged[key] = {
                "event_url": rec["event_url"],
                "sources": [],
            }
        m = merged[key]
        src = rec.get("source")
        if src and src not in m["sources"]:
            m["sources"].append(src)
        for field, val in rec.items():
            if field in ("event_url", "source"):
                continue
            if val is None or val == "":
                continue
            if field not in m or m[field] is None or m[field] == "":
                m[field] = val

    for rec in listing:
        put(rec)
    for rec in featured:
        put(rec)
    for rec in jsonld:
        put(rec)

    return list(merged.values())


def parse_event_listing_html(html: str) -> list[dict[str, Any]]:
    """Parse listing + featured (directory-style DOM) + JSON-LD events; merge by URL."""
    soup = BeautifulSoup(html, "html.parser")
    listing = parse_listing_table(soup)
    featured = parse_featured(soup)
    jsonld = parse_jsonld_events(soup)
    return merge_events(listing, featured, jsonld)


def get_listing_ajax_url(html: str) -> str | None:
    """Hidden ``#desktopdropdown`` listing AJAX URL when present (some directory sites)."""
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.find("input", id="desktopdropdown")
    if inp and inp.get("value"):
        v = str(inp["value"]).strip()
        return v or None
    return None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract event rows from saved HTML (generic window.open URLs + JSON-LD; "
        "directory-style #listing-events / #featured-events when present)."
    )
    ap.add_argument("html_path", type=Path, help="Path to page.html (or similar dump)")
    ap.add_argument("--out", type=Path, help="Write JSON array to this file")
    ap.add_argument("--jsonl", type=Path, help="Write one JSON object per line")
    ap.add_argument("--stdout", action="store_true", help="Print JSON array to stdout")
    ap.add_argument(
        "--ajax-url",
        action="store_true",
        help="Print #desktopdropdown URL if present (pagination hook on some sites)",
    )
    args = ap.parse_args()
    html = args.html_path.read_text(encoding="utf-8", errors="replace")
    if args.ajax_url:
        u = get_listing_ajax_url(html)
        print(u or "")
        return
    events = parse_event_listing_html(html)
    payload = json.dumps(events, ensure_ascii=False, indent=2)
    if args.stdout:
        print(payload)
    if args.out:
        args.out.write_text(payload, encoding="utf-8")
    if args.jsonl:
        with args.jsonl.open("w", encoding="utf-8") as f:
            for row in events:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if not args.stdout and not args.out and not args.jsonl:
        print(payload)


if __name__ == "__main__":
    main()
