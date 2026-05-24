"""Resolve official event websites via Tavily (optional) or DuckDuckGo (no key)."""

from __future__ import annotations

import os

from env_config import load_env, tavily_configured
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

# Listing / aggregator domains — not official event sites.
BLOCKLIST_HOSTS = {
    "conferenceindex.org",
    "10times.com",
    "allevents.in",
    "eventbrite.com",
    "eventbrite.co.uk",
    "facebook.com",
    "linkedin.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "wikipedia.org",
    "meetup.com",
    "lanyrd.com",
    "conferencealerts.com",
    "expocenter.com",
    "tradefairdates.com",
    "google.com",
    "bing.com",
    "bookmyshow.com",
    "loc.gov",
    "archive.org",
    "scribd.com",
    "researchgate.net",
    "academia.edu",
    "travelandtourworld.com",
    "medium.com",
    "prnewswire.com",
    "businesswire.com",
    "globenewswire.com",
    "yahoo.com",
    "msn.com",
    "reddit.com",
    "pinterest.com",
    "tiktok.com",
    "blackbaud.com",
}

# URL path fragments that indicate news/blog, not an event site.
BLOCKLIST_PATH_PARTS = (
    "/news/",
    "/article/",
    "/articles/",
    "/blog/",
    "/press/",
    "/press-release",
    "/donor-form",
    "/donate",
)


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    score: float


def _normalize_host(url: str) -> str | None:
    try:
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def _is_blocked(url: str) -> bool:
    lower = (url or "").lower()
    if lower.endswith(".pdf"):
        return True
    if any(part in lower for part in BLOCKLIST_PATH_PARTS):
        return True
    host = _normalize_host(url)
    if not host:
        return True
    for blocked in BLOCKLIST_HOSTS:
        if host == blocked or host.endswith("." + blocked):
            return True
    return False


def _tokenize(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(t) > 2}


def _score_candidate(
    url: str,
    title: str,
    snippet: str,
    event_name: str,
    listing_context: dict[str, Any] | None = None,
) -> float:
    if _is_blocked(url):
        return -1.0
    host = _normalize_host(url) or ""
    tokens = _tokenize(event_name)
    score = 0.0
    hay = f"{host} {title} {snippet}".lower()
    for tok in tokens:
        if tok in hay:
            score += 1.5
        if tok in host.replace("-", " ").replace(".", " "):
            score += 2.0
    if any(k in hay for k in ("official", "home", "conference", "summit", "expo", "week")):
        score += 0.5
    if host.endswith((".gov", ".edu")):
        score += 0.3

    ctx = listing_context or {}
    city = (ctx.get("city") or "").strip().lower()
    country = (ctx.get("country") or "").strip().lower()
    org = (ctx.get("hosting_entity") or ctx.get("conference_group_name") or "").strip().lower()
    if city and len(city) > 2:
        if city in hay:
            score += 2.5
        elif city not in hay and country and country in hay:
            score += 0.5
        else:
            score -= 1.5
    if country and len(country) > 2 and country in hay:
        score += 1.0
    if org and len(org) > 4:
        org_tokens = _tokenize(org)
        overlap = sum(1 for t in org_tokens if t in hay)
        if overlap >= 2:
            score += 2.0
    return score


async def _tavily_search(query: str, max_results: int = 8) -> list[SearchHit]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("https://api.tavily.com/search", json=payload)
        resp.raise_for_status()
        data = resp.json()
    hits: list[SearchHit] = []
    for row in data.get("results") or []:
        url = row.get("url") or ""
        if not url:
            continue
        hits.append(
            SearchHit(
                url=url,
                title=row.get("title") or "",
                snippet=row.get("content") or "",
                score=float(row.get("score") or 0),
            )
        )
    return hits


def _duckduckgo_search(query: str, max_results: int = 8) -> list[SearchHit]:
    ddgs_cls = None
    try:
        from ddgs import DDGS as DDGSNew
        ddgs_cls = DDGSNew
    except ImportError:
        try:
            from duckduckgo_search import DDGS as DDGSOld
            ddgs_cls = DDGSOld
        except ImportError:
            return []

    hits: list[SearchHit] = []
    with ddgs_cls() as ddgs:
        for row in ddgs.text(query, max_results=max_results):
            url = row.get("href") or row.get("link") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=row.get("title") or "",
                    snippet=row.get("body") or "",
                    score=0.0,
                )
            )
    return hits


async def search_web(query: str, max_results: int = 8) -> list[SearchHit]:
    load_env()
    hits = await _tavily_search(query, max_results=max_results)
    if hits:
        return hits
    if tavily_configured():
        return []
    return _duckduckgo_search(query, max_results=max_results)


async def resolve_official_url(
    event_name: str,
    listing_url: str | None = None,
    *,
    listing_context: dict[str, Any] | None = None,
) -> tuple[str | None, float, list[str]]:
    """Return (official_url, confidence 0-1, notes)."""
    notes: list[str] = []
    if not (event_name or "").strip():
        return None, 0.0, ["missing event name"]

    ctx = listing_context or {}
    city = ctx.get("city")
    country = ctx.get("country")
    location_hint = ""
    if city and country:
        location_hint = f" {city} {country}"
    elif city:
        location_hint = f" {city}"

    query = f'"{event_name.strip()}" official conference website{location_hint}'
    hits = await search_web(query)
    if not hits:
        hits = await search_web(f"{event_name.strip()} official site{location_hint}")
    if not hits:
        notes.append("no search results")
        return None, 0.0, notes

    scored: list[tuple[float, SearchHit]] = []
    for hit in hits:
        s = _score_candidate(hit.url, hit.title, hit.snippet, event_name, listing_context=ctx)
        if s >= 0:
            scored.append((s, hit))

    if not scored:
        notes.append("all results blocked or low score")
        return None, 0.0, notes

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    confidence = min(1.0, best_score / max(len(_tokenize(event_name)) * 2, 1))
    notes.append(f"search: {query}")
    notes.append(f"pick: {best.url} (score={best_score:.1f})")
    if os.environ.get("TAVILY_API_KEY"):
        notes.append("provider: tavily")
    else:
        notes.append("provider: duckduckgo")

    if listing_url and _normalize_host(listing_url) == _normalize_host(best.url):
        notes.append("same as listing URL")

    if confidence < 0.35:
        notes.append("low confidence — using listing page only")
        return None, confidence, notes

    city = (ctx.get("city") or "").strip().lower()
    if city and len(city) > 2 and city not in f"{best.title} {best.snippet} {best.url}".lower():
        notes.append(f"location mismatch (expected {city}) — using listing page only")
        return None, confidence, notes

    return best.url, confidence, notes
