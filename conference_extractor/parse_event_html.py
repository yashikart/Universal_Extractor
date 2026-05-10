"""Parse event detail and satellite listing pages from HTML (any host).

Selectors target common event-directory / tradeshow markup patterns. Extend
``parse_event_detail`` / listing parsers if your site uses different DOM.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag


def _abs(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    return urljoin(base.rstrip("/") + "/", href)


def _clean_int(s: str | None) -> int | None:
    if not s:
        return None
    d = "".join(ch for ch in str(s) if ch.isdigit())
    return int(d) if d else None


def _page_netloc(url: str) -> str:
    return (urlparse(url).netloc or "").lower()


def _is_same_site_http_url(href: str, site_netloc: str) -> bool:
    """True when ``href`` is HTTP(S) on the same host as ``site_netloc`` (internal links)."""
    if not href.startswith("http"):
        return True
    host = (urlparse(href).netloc or "").lower()
    if not host or not site_netloc:
        return False
    if host == site_netloc:
        return True
    if host.endswith("." + site_netloc):
        return True
    return False


def _looks_like_directory_root_title(title: str) -> bool:
    """Heuristic: global search / explore page title, not a single-event sponsors tab."""
    if not title or len(title) < 25:
        return False
    tl = title.lower()
    if "find" in tl and ("event" in tl or "events" in tl):
        return True
    if "compare" in tl and ("event" in tl or "events" in tl):
        return True
    if "explore" in tl and ("event" in tl or "events" in tl):
        return True
    return False


def parse_event_detail(html: str, event_url: str, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract fields from an event about/detail page. ``seed`` is the listing row when available."""
    seed = seed or {}
    soup = BeautifulSoup(html, "html.parser")
    base = f"{urlparse(event_url).scheme}://{urlparse(event_url).netloc}"
    notes: list[str] = []
    meta: dict[str, Any] = {}
    out: dict[str, Any] = {"detail_parse_notes": notes}

    h1 = soup.find("h1")
    out["event_name"] = (h1.get_text(" ", strip=True) if h1 else None) or seed.get("name")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc = (meta_desc.get("content") if meta_desc else None) or seed.get("description")
    org_a = soup.select_one("a#org-name")
    out["hosting_entity"] = org_a.get_text(" ", strip=True) if org_a else None
    out["hosting_entity_url"] = _abs(base, org_a.get("href")) if org_a else None

    slug_inp = soup.select_one("input#event_url")
    slug = slug_inp.get("value") if slug_inp else None
    if not slug and event_url:
        path = urlparse(event_url).path.strip("/").split("/")
        slug = path[0] if path else None

    paywall = soup.select_one("span.venue-wrapper span.paywall3, span.paywall3")
    if paywall:
        out["venue_name"] = paywall.get_text(" ", strip=True) or None

    lat = soup.select_one("span#event_latitude")
    lon = soup.select_one("span#event_longude, span#event_longitude")
    if lat and lat.get_text(strip=True):
        meta["geo_latitude"] = lat.get_text(strip=True)
    if lon and lon.get_text(strip=True):
        meta["geo_longitude"] = lon.get_text(strip=True)

    venue_block = soup.select_one("div.venue-wrapper1, .venue-wrapper1")
    if venue_block:
        addr_a = venue_block.select_one("a[href*='/venues/']")
        if addr_a:
            meta["venue_page_url"] = _abs(base, addr_a.get("href"))
            if not out.get("venue_name"):
                out["venue_name"] = addr_a.get_text(" ", strip=True)

    smalls = soup.select("div.mb-3 p.mb-0 small, .venue-wrapper1 ~ div small")
    texts = [t.get_text(" ", strip=True) for t in smalls if t.get_text(strip=True)]
    if not texts:
        hdr = soup.select_one("span.m-mins_lft, .m-mins_lft")
        if hdr:
            texts = [t for t in hdr.stripped_strings if len(t) > 1]
    if len(texts) >= 1 and not out.get("city"):
        out["city"] = texts[0]
    if len(texts) >= 2 and not out.get("country"):
        out["country"] = texts[-1]

    if slug:
        out["exhibitor_crawler_url"] = f"{base}/{slug}/exhibitors"
        out["sponsor_crawler_url"] = f"{base}/{slug}/sponsors"
        out["attending_crawler_url"] = f"{base}/{slug}/visitors"

    ex_td = soup.select_one("td#exhibitors")
    if ex_td:
        ec = ex_td.get("data-count")
        n = _clean_int(ec)
        if n is not None:
            meta["exhibitor_count_estimate"] = n
        ex_a = ex_td.select_one("a[href*='/exhibitors']")
        if ex_a:
            out["exhibitor_crawler_url"] = _abs(base, ex_a.get("href")) or out.get("exhibitor_crawler_url")

    sp_td = soup.select_one("td#speakers")
    speakers_url = None
    spk_disabled = None
    if sp_td:
        sp_a = sp_td.select_one("a[href]")
        if sp_a:
            speakers_url = _abs(base, sp_a.get("href"))
        onclick = ""
        span = sp_td.find("span", onclick=True)
        if span:
            onclick = span.get("onclick", "") or ""
        m = re.search(r"disabledTab\([^,]+,\s*'speakers',\s*'(\d+)'", onclick)
        if m:
            spk_disabled = m.group(1) == "0"
    if speakers_url:
        meta["speakers_tab_url"] = speakers_url
    if spk_disabled is True:
        notes.append("speakers_tab_disabled")

    hub = soup.select_one("div#redirect_link")
    if hub and hub.get_text(strip=True):
        hu = hub.get_text(strip=True)
        meta["hub_redirect_url"] = hu
        slug_hub = urlparse(hu).path.strip("/").split("/")[-1].replace("-hub", "").replace("_", " ").title()
        out["conference_group_name"] = slug_hub or None
        out["conference_group"] = slug_hub or None

    cats = seed.get("categories")
    out["industry_raw"] = cats
    if seed.get("start_date"):
        out["start_date"] = seed["start_date"]
    if seed.get("end_date"):
        out["end_date"] = seed["end_date"]
    out["event_url"] = event_url

    meta.update(
        {
            "seed_name": seed.get("name"),
            "seed_venue_line": seed.get("venue"),
            "seed_footer_stats": seed.get("footer_stats"),
        }
    )
    out["event_description_methodology"] = desc
    out["listing_metadata"] = meta

    return out


def parse_exhibitors_listing(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    companies: list[str] = []
    websites: list[str] = []
    site = _page_netloc(page_url)
    for block in soup.select("div.exhibitorsBlock"):
        if not isinstance(block, Tag):
            continue
        hid = block.select_one("div.exhibitorName input[type=hidden][id^=exhibitor-]")
        if hid and hid.get("value"):
            name = hid["value"].strip()
            if name and name not in companies:
                companies.append(name)
        for a in block.select("a[href^=http]"):
            href = (a.get("href") or "").strip()
            if _is_same_site_http_url(href, site):
                continue
            if href.startswith("http") and href not in websites:
                websites.append(href)
    meta_count = None
    hid = soup.select_one("input.exhibitorsPagecount[data-count]")
    if hid:
        meta_count = _clean_int(hid.get("data-count"))
    return {
        "exhibitor_companies": companies,
        "exhibitor_company_websites": websites,
        "listing_metadata": {"exhibitor_page_total_hint": meta_count},
    }


def parse_sponsors_listing(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else "") or ""
    companies: list[str] = []
    if title and _looks_like_directory_root_title(title):
        return {
            "sponsor_companies": [],
            "sponsor_company_websites": [],
            "listing_metadata": {"sponsors_page_generic": True},
        }
    for card in soup.select("div.box.fs-14, section.box"):
        if not isinstance(card, Tag):
            continue
        strong = card.find("strong")
        if strong:
            t = strong.get_text(" ", strip=True)
            if t and len(t) > 2 and t not in companies:
                companies.append(t)
    if not companies and title and _looks_like_directory_root_title(title):
        return {
            "sponsor_companies": [],
            "sponsor_company_websites": [],
            "listing_metadata": {"sponsors_page_generic": True},
        }
    return {"sponsor_companies": companies, "sponsor_company_websites": []}


def parse_speakers_listing(html: str, page_url: str) -> dict[str, Any]:
    """Optional /speakers tab when populated."""
    soup = BeautifulSoup(html, "html.parser")
    speakers: list[dict[str, Any]] = []
    for row in soup.select("[class*='speaker'], .box-210"):
        if not isinstance(row, Tag):
            continue
        name_el = row.select_one(".fs-14.mt-2, .fs-14.fw-500, div.fs-14")
        if name_el:
            nm = name_el.get_text(" ", strip=True)
            if nm and len(nm) > 2 and not nm.lower().startswith("follow"):
                speakers.append({"name": nm})
    seen = set()
    uniq: list[dict[str, Any]] = []
    for s in speakers:
        n = s.get("name") or ""
        if n and n not in seen:
            seen.add(n)
            uniq.append(s)
    return {"speakers": uniq[:200]}
