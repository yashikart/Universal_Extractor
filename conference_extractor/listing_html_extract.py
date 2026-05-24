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

Next step (optional): open the Streamlit UI or run
``python listing_html_extract.py inspect_out/page.html --out events.json``.
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

# Path segments that usually mean an event *detail* page (not category/search/nav).
_EVENT_DETAIL_PATH_RE = re.compile(
    r"""(?i)(?:^|/)event/[^/?#]+|(?:^|/)events/(?!create|list|index)[^/?#]+|(?:^|/)conference/[^/?#]+""",
)

# Paths to skip when harvesting <a href> (listing hubs, auth, static pages).
_NON_EVENT_PATH_PREFIXES = (
    "/conferences/",
    "/disciplines",
    "/locations",
    "/search",
    "/auth/",
    "/user/",
    "/page/",
    "/static/",
    "/favicon",
)


def _looks_like_event_detail_url(url: str) -> bool:
    u = (url or "").strip()
    if not u or u.startswith(("javascript:", "mailto:", "#")):
        return False
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https"):
        return False
    path = parsed.path or ""
    lower = path.lower()
    for prefix in _NON_EVENT_PATH_PREFIXES:
        if lower.startswith(prefix) or prefix.rstrip("/") == lower.rstrip("/"):
            return False
    if not _EVENT_DETAIL_PATH_RE.search(path):
        return False
    # Category hubs: /conferences/education (plural, no /event/)
    if "/conferences/" in lower and "/event/" not in lower and "/events/" not in lower:
        return False
    return True


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


def _jsonld_type_matches(node: dict[str, Any], expected: str) -> bool:
    t = node.get("@type")
    if isinstance(t, str):
        return t == expected or expected in t.split()
    if isinstance(t, list):
        return expected in t
    return False


def _iter_jsonld_nodes(data: Any) -> list[dict[str, Any]]:
    """Flatten JSON-LD blocks: top-level object, @graph, or list of objects."""
    if isinstance(data, list):
        nodes: list[dict[str, Any]] = []
        for item in data:
            nodes.extend(_iter_jsonld_nodes(item))
        return nodes
    if not isinstance(data, dict):
        return []
    graph = data.get("@graph")
    if isinstance(graph, list):
        nodes = []
        for item in graph:
            nodes.extend(_iter_jsonld_nodes(item))
        return nodes
    return [data]


def _record_from_jsonld_event(
    item: dict[str, Any], *, position: Any = None
) -> dict[str, Any] | None:
    if not _jsonld_type_matches(item, "Event"):
        return None
    event_url = _event_url_from_jsonld_item(item)
    if not event_url:
        return None
    loc = item.get("location")
    venue_name = None
    if isinstance(loc, list):
        for block in loc:
            if isinstance(block, dict) and _jsonld_type_matches(block, "Place"):
                venue_name = block.get("name")
                break
    elif isinstance(loc, dict) and _jsonld_type_matches(loc, "Place"):
        venue_name = loc.get("name")
    return {
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
        "jsonld_position": position,
        "jsonld_image": item.get("image"),
    }


def parse_jsonld_events(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """JSON-LD: ``ItemList`` of ``Event``, standalone ``Event``, or ``@graph``."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _iter_jsonld_nodes(data):
            if _jsonld_type_matches(node, "Event"):
                rec = _record_from_jsonld_event(node)
                if rec:
                    key = _canonical_url(rec["event_url"])
                    if key and key not in seen:
                        seen.add(key)
                        out.append(rec)
                continue
            if not _jsonld_type_matches(node, "ItemList"):
                continue
            elements = node.get("itemListElement") or []
            for entry in elements:
                if not isinstance(entry, dict):
                    continue
                item = entry.get("item")
                if isinstance(item, str) and item.startswith("http"):
                    if _looks_like_event_detail_url(item):
                        key = _canonical_url(item)
                        if key and key not in seen:
                            seen.add(key)
                            out.append(
                                {
                                    "event_url": item,
                                    "source": "jsonld",
                                    "event_id": None,
                                    "edition_id": None,
                                    "name": entry.get("name"),
                                    "date_display": None,
                                    "start_date": None,
                                    "end_date": None,
                                    "venue": None,
                                    "description": None,
                                    "categories": None,
                                    "footer_stats": None,
                                    "jsonld_position": entry.get("position"),
                                    "jsonld_image": None,
                                }
                            )
                    continue
                if not isinstance(item, dict):
                    continue
                rec = _record_from_jsonld_event(item, position=entry.get("position"))
                if rec:
                    key = _canonical_url(rec["event_url"])
                    if key and key not in seen:
                        seen.add(key)
                        out.append(rec)
    return out


def parse_window_open_urls(html: str) -> list[dict[str, Any]]:
    """Every ``window.open('https://…')`` in the raw HTML (any host)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in WINDOW_OPEN_RE.finditer(html):
        url = m.group(2)
        key = _canonical_url(url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "event_url": url,
                "source": "window_open",
                "event_id": None,
                "edition_id": None,
                "name": None,
                "date_display": None,
                "start_date": None,
                "end_date": None,
                "venue": None,
                "description": None,
                "categories": None,
                "footer_stats": None,
            }
        )
    return out


def parse_anchor_event_links(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """``<a href>`` whose path looks like an event detail page (card/list sites)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        if not _looks_like_event_detail_url(href):
            continue
        key = _canonical_url(href)
        if not key or key in seen:
            continue
        seen.add(key)
        name = a.get("title") or a.get_text(" ", strip=True) or None
        out.append(
            {
                "event_url": href,
                "source": "anchor",
                "event_id": None,
                "edition_id": None,
                "name": name or None,
                "date_display": None,
                "start_date": None,
                "end_date": None,
                "venue": None,
                "description": None,
                "categories": None,
                "footer_stats": None,
            }
        )
    return out


def merge_events(
    listing: list[dict[str, Any]],
    featured: list[dict[str, Any]],
    jsonld: list[dict[str, Any]],
    window_open: list[dict[str, Any]] | None = None,
    anchors: list[dict[str, Any]] | None = None,
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
    for rec in window_open or []:
        put(rec)
    for rec in anchors or []:
        put(rec)

    return list(merged.values())


def parse_event_listing_html(html: str) -> list[dict[str, Any]]:
    """Directory DOM + JSON-LD + global window.open + event-detail anchor links."""
    soup = BeautifulSoup(html, "html.parser")
    listing = parse_listing_table(soup)
    featured = parse_featured(soup)
    jsonld = parse_jsonld_events(soup)
    window_open = parse_window_open_urls(html)
    anchors = parse_anchor_event_links(soup)
    return merge_events(listing, featured, jsonld, window_open, anchors)


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
        description="Extract event rows from saved HTML: directory tables, JSON-LD, "
        "global window.open URLs, and <a href> event-detail links."
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
