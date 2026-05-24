"""Discover and crawl exhibitor/sponsor/speaker subpages with pagination."""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from extract_heuristics import find_subpage

FetchFn = Callable[[str], Awaitable[tuple[bool, str, str]]]

SUBPAGE_ROLES: dict[str, tuple[str, ...]] = {
    "exhibitors": (
        "exhibitor",
        "exhibitors",
        "exhibitor-list",
        "exhibitor list",
        "exhibitor directory",
        "floor-plan",
        "floor plan",
        "expo hall",
        "who exhibits",
    ),
    "sponsors": (
        "sponsor",
        "sponsors",
        "partner",
        "partners",
        "supporter",
        "supporters",
        "media partner",
        "our partners",
    ),
    "speakers": (
        "speaker",
        "speakers",
        "keynote",
        "agenda",
        "program",
        "schedule",
        "faculty",
        "presenters",
    ),
}

PAGINATION_HINTS = (
    "next",
    "older",
    "more",
    "page=",
    "/page/",
    "p=",
    "pagenum",
    "start=",
)

PAGE_NUM_RE = re.compile(r"(?:[?&](?:page|p|pagenum|start)=|\bpage[/-])(\d+)", re.I)


def _normalize_host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().lstrip("www.")


def discover_subpage_urls(html: str, base_url: str) -> dict[str, str]:
    """Return role -> first subpage URL on the same site when possible."""
    soup = BeautifulSoup(html, "html.parser")
    base_host = _normalize_host(base_url)
    urls: dict[str, str] = {}
    for role, keywords in SUBPAGE_ROLES.items():
        found = find_subpage(soup, base_url, keywords, prefer_same_host=True)
        if not found or found.rstrip("/") == base_url.rstrip("/"):
            continue
        if base_host and _normalize_host(found) != base_host:
            continue
        urls[role] = found
    return urls


def discover_pagination_urls(html: str, page_url: str, *, max_pages: int = 5) -> list[str]:
    """Find additional pages for a listing (page 2..N)."""
    soup = BeautifulSoup(html, "html.parser")
    base = urlparse(page_url)
    base_host = _normalize_host(page_url)
    seen: set[str] = {page_url.rstrip("/")}
    ordered: list[str] = []

    def add_candidate(full: str) -> None:
        full = full.split("#")[0].strip()
        if not full or not full.startswith("http"):
            return
        if full.lower().split("?")[0].endswith(".pdf"):
            return
        if _normalize_host(full) != base_host:
            return
        key = full.rstrip("/")
        if key in seen:
            return
        seen.add(key)
        ordered.append(full)

    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(page_url, href)
        lower_href = href.lower()
        label = (a.get_text(" ", strip=True) or "").lower()
        rel = " ".join(a.get("rel") or []).lower()
        classes = " ".join(a.get("class") or []).lower()

        is_next = "next" in rel or "next" in classes or label in ("next", "next »", "›", "→")
        has_page_hint = any(h in lower_href for h in PAGINATION_HINTS) or is_next
        if not has_page_hint:
            continue
        if not PAGE_NUM_RE.search(lower_href) and not is_next:
            continue
        add_candidate(full)

    for link in soup.find_all("link", rel=True, href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "next" not in rel:
            continue
        add_candidate(urljoin(page_url, link["href"]))

    return ordered[: max(0, max_pages - 1)]


async def crawl_role_pages(
    role: str,
    start_url: str,
    fetch_page: FetchFn,
    *,
    max_pages: int = 5,
) -> tuple[dict[str, str], dict[str, str]]:
    """Fetch role subpage and pagination; return pages dict and url map."""
    pages: dict[str, str] = {}
    urls: dict[str, str] = {}
    queue = [start_url]
    fetched = 0

    while queue and fetched < max_pages:
        url = queue.pop(0)
        ok, html, _note = await fetch_page(url)
        if not ok or not html:
            continue
        key = role if fetched == 0 else f"{role}_p{fetched + 1}"
        pages[key] = html
        urls[key] = url
        fetched += 1
        if fetched >= max_pages:
            break
        for nxt in discover_pagination_urls(html, url, max_pages=max_pages):
            if nxt not in queue and nxt not in urls.values():
                queue.append(nxt)
                break

    return pages, urls


async def crawl_all_subpages(
    main_html: str,
    base_url: str,
    fetch_page: FetchFn,
    *,
    max_pages_per_role: int = 5,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """
    Discover exhibitor/sponsor/speaker URLs from main HTML and crawl with pagination.

    Returns (extra_pages, extra_urls, crawler_urls) where crawler_urls maps role to
    the first discovered URL (for exhibitor_crawler_url / sponsor_crawler_url fields).
    """
    discovered = discover_subpage_urls(main_html, base_url)
    extra_pages: dict[str, str] = {}
    extra_urls: dict[str, str] = {}
    crawler_urls: dict[str, str] = {}

    for role, start_url in discovered.items():
        if role == "exhibitors":
            crawler_urls["exhibitor_crawler_url"] = start_url
        elif role == "sponsors":
            crawler_urls["sponsor_crawler_url"] = start_url

        role_pages, role_urls = await crawl_role_pages(
            role,
            start_url,
            fetch_page,
            max_pages=max_pages_per_role,
        )
        extra_pages.update(role_pages)
        extra_urls.update(role_urls)

    return extra_pages, extra_urls, crawler_urls


def merge_crawler_urls(seed: dict[str, Any], crawler_urls: dict[str, str]) -> None:
    for key in ("exhibitor_crawler_url", "sponsor_crawler_url"):
        if crawler_urls.get(key) and not seed.get(key):
            seed[key] = crawler_urls[key]
