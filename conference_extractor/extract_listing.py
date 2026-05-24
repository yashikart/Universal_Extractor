"""Extract structured fields from listing-aggregator pages (Conference Index, etc.)."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from conference_record import domain_from_url
from extract_heuristics import (
    _addr_from_node,
    _flatten_graph,
    _is_event_type,
    _iter_json_ld,
    extract_dates_from_text,
)
from scope_eval import load_taxonomy, map_industry, methodology_description

COUNTRY_CODES = {
    "au": "Australia",
    "ca": "Canada",
    "us": "United States",
    "uk": "United Kingdom",
    "gb": "United Kingdom",
    "de": "Germany",
    "fr": "France",
    "in": "India",
    "cn": "China",
    "jp": "Japan",
    "kr": "South Korea",
    "sg": "Singapore",
    "my": "Malaysia",
    "ae": "United Arab Emirates",
    "nl": "Netherlands",
    "es": "Spain",
    "it": "Italy",
    "ch": "Switzerland",
    "se": "Sweden",
    "no": "Norway",
    "dk": "Denmark",
    "fi": "Finland",
    "pl": "Poland",
    "at": "Austria",
    "be": "Belgium",
    "ie": "Ireland",
    "nz": "New Zealand",
    "za": "South Africa",
    "br": "Brazil",
    "mx": "Mexico",
    "tr": "Turkey",
    "sa": "Saudi Arabia",
    "il": "Israel",
    "hk": "Hong Kong",
    "tw": "Taiwan",
    "th": "Thailand",
    "id": "Indonesia",
    "ph": "Philippines",
    "vn": "Vietnam",
    "pt": "Portugal",
    "gr": "Greece",
    "cz": "Czech Republic",
    "hu": "Hungary",
    "ro": "Romania",
}

MONTH_SLUG = {
    "january": "01",
    "february": "02",
    "march": "03",
    "april": "04",
    "may": "05",
    "june": "06",
    "july": "07",
    "august": "08",
    "september": "09",
    "october": "10",
    "november": "11",
    "december": "12",
}


def _li_value(soup: BeautifulSoup, label: str) -> str | None:
    pattern = re.compile(rf"^{re.escape(label)}\s*:", re.I)
    for li in soup.find_all("li"):
        text = li.get_text(" ", strip=True)
        if not pattern.match(text):
            continue
        strong = li.find("strong")
        if strong:
            val = strong.get_text(" ", strip=True)
            if val:
                return val
        parts = text.split(":", 1)
        if len(parts) == 2:
            return parts[1].strip() or None
    return None


def _parse_location(raw: str | None) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Return venue, city, state, country, zip from a location string."""
    if not raw:
        return None, None, None, None, None
    raw = re.sub(r"\s+", " ", raw.strip())
    zip_m = re.search(r"\b(\d{5}(?:-\d{4})?)\b", raw)
    zip_code = zip_m.group(1) if zip_m else None
    if zip_m:
        raw = raw.replace(zip_m.group(0), "").strip(" ,")

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    venue = city = state = country = None
    if len(parts) >= 3:
        city, state, country = parts[-3], parts[-2], parts[-1]
        if len(parts) > 3:
            venue = ", ".join(parts[:-3])
    elif len(parts) == 2:
        city, country = parts[0], parts[1]
    elif len(parts) == 1:
        token = parts[0]
        if token.lower() in ("online", "virtual", "remote"):
            venue = token
        else:
            city = token
    return venue, city, state, country, zip_code


def _parse_slug(url: str) -> dict[str, Any]:
    path = urlparse(url).path or ""
    slug = path.rstrip("/").split("/")[-1]
    parts = slug.split("-")
    out: dict[str, Any] = {}
    for i, part in enumerate(parts):
        if part.isdigit() and len(part) == 4 and 2020 <= int(part) <= 2035:
            out["year"] = part
            if i + 1 < len(parts) and parts[i + 1].lower() in MONTH_SLUG:
                month_name = parts[i + 1].lower()
                out["month"] = MONTH_SLUG[month_name]
            if i + 2 < len(parts):
                out["city_slug"] = parts[i + 2].replace("-", " ").title()
            if i + 3 < len(parts):
                cc = parts[i + 3].lower()
                out["country_code"] = cc
                out["country"] = COUNTRY_CODES.get(cc, cc.upper())
            break
    return out


def _tags_blob(soup: BeautifulSoup) -> str:
    tags: list[str] = []
    for a in soup.select("li a[href*='/conferences/']"):
        t = a.get_text(" ", strip=True)
        if t:
            tags.append(t)
    meta = soup.find("meta", attrs={"name": "keywords"})
    if meta and meta.get("content"):
        tags.append(meta["content"])
    return ", ".join(tags)


def extract_conferenceindex(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {
        "event_url": page_url,
        "listing_url": page_url,
        "conference_domain": domain_from_url(page_url),
        "conference_group_name": "Conference Index",
    }

    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
        out["event_name"] = title.split(" on ")[0].strip() if " on " in title else title

    loc = _li_value(soup, "Location")
    venue, city, state, country, zip_code = _parse_location(loc)
    out["venue_name"] = venue or city
    out["city"] = city
    out["state_province"] = state
    out["country"] = country
    out["zip_code"] = zip_code

    date_raw = _li_value(soup, "Date")
    if date_raw:
        start, end = extract_dates_from_text(date_raw)
        out["start_date"] = start
        out["end_date"] = end or start

    org = _li_value(soup, "Organization")
    if org:
        out["hosting_entity"] = org
        out["conference_group"] = org
        out["conference_group_name"] = org

    venue_li = _li_value(soup, "Venue")
    if venue_li:
        v_venue, v_city, v_state, v_country, v_zip = _parse_location(venue_li)
        out["venue_name"] = v_venue or venue_li
        out["city"] = out.get("city") or v_city
        out["state_province"] = out.get("state_province") or v_state
        out["country"] = out.get("country") or v_country
        out["zip_code"] = out.get("zip_code") or v_zip
        if v_venue and not out.get("address_1"):
            out["address_1"] = v_venue

    program_li = soup.find("a", href=re.compile(r"/program$"))
    if program_li and program_li.get("href"):
        program_href = program_li["href"]
        if not program_href.startswith("http"):
            program_href = urljoin(page_url, program_href)
        out["program_url"] = program_href
        out["sponsor_crawler_url"] = program_href
        out["exhibitor_crawler_url"] = program_href

    desc_pane = soup.select_one("#event-description")
    if desc_pane:
        desc = desc_pane.get_text("\n", strip=True)
        out["event_description_methodology"] = methodology_description(desc)

    org_pane = soup.select_one("#event-organization")
    if org_pane:
        org_text = org_pane.get_text("\n", strip=True)
        if org_text and not out.get("conference_group_description"):
            out["conference_group_description"] = methodology_description(org_text)

    slug_info = _parse_slug(page_url)
    if slug_info.get("city_slug") and not out.get("city"):
        out["city"] = slug_info["city_slug"]
    if slug_info.get("country") and not out.get("country"):
        out["country"] = slug_info["country"]
    if slug_info.get("year") and slug_info.get("month") and not out.get("start_date"):
        out["start_date"] = f"{slug_info['year']}-{slug_info['month']}-01"
        out["end_date"] = out["start_date"]

    flat = _flatten_graph(_iter_json_ld(soup))
    event_node = next((n for n in flat if _is_event_type(n)), None)
    if event_node:
        out["event_name"] = out.get("event_name") or event_node.get("name")
        out["start_date"] = out.get("start_date") or event_node.get("startDate")
        out["end_date"] = out.get("end_date") or event_node.get("endDate")
        desc = event_node.get("description")
        if isinstance(desc, str) and not out.get("event_description_methodology"):
            out["event_description_methodology"] = methodology_description(desc)
        org = event_node.get("organizer") or event_node.get("performer")
        if isinstance(org, dict):
            out["hosting_entity"] = out.get("hosting_entity") or org.get("name")

    blob = " ".join(
        filter(
            None,
            [
                out.get("event_name"),
                out.get("event_description_methodology"),
                _tags_blob(soup),
            ],
        )
    )
    code, label = map_industry(blob, load_taxonomy())
    out["industry_methodology"] = code
    out["industry"] = label

    return out


def _allevents_category(soup: BeautifulSoup) -> str | None:
    for a in soup.select("a[href*='/business'], a[title*='Business events']"):
        t = a.get_text(" ", strip=True)
        if t and "business" in t.lower():
            return t
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if re.search(r"/[a-z-]+/business", href, re.I):
            return a.get_text(" ", strip=True) or "Business"
    return None


def _allevents_organizer(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    org = soup.select_one(".eps-org-container")
    if not org:
        return None, None
    name_el = org.select_one(".eps-org-name, h2")
    name = name_el.get_text(" ", strip=True) if name_el else None
    href = org.get("href")
    if href and not href.startswith("http"):
        href = urljoin("https://allevents.in", href)
    return name, href


def _allevents_ticket_exit_url(soup: BeautifulSoup) -> str | None:
    for btn in soup.select("[data-ehref*='go.php']"):
        url = (btn.get("data-ehref") or "").strip()
        if url:
            return url
    return None


def extract_allevents(html: str, page_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {
        "event_url": page_url,
        "listing_url": page_url,
        "conference_domain": domain_from_url(page_url),
        "conference_group_name": "AllEvents",
    }

    h1 = soup.find("h1")
    if h1:
        out["event_name"] = h1.get_text(" ", strip=True)

    org_name, org_url = _allevents_organizer(soup)
    if org_name:
        out["hosting_entity"] = org_name
        out["conference_group"] = org_name
        out["conference_group_name"] = org_name
    if org_url:
        out["organizer_url"] = org_url

    exit_url = _allevents_ticket_exit_url(soup)
    if exit_url:
        out["ticket_exit_url"] = exit_url

    flat = _flatten_graph(_iter_json_ld(soup))
    event_node = next((n for n in flat if _is_event_type(n)), None)
    if event_node:
        out["event_name"] = out.get("event_name") or event_node.get("name")
        start = event_node.get("startDate")
        end = event_node.get("endDate")
        if isinstance(start, str) and len(start) >= 10:
            out["start_date"] = start[:10]
        if isinstance(end, str) and len(end) >= 10:
            out["end_date"] = end[:10]
        desc = event_node.get("description")
        if isinstance(desc, str):
            out["event_description_methodology"] = methodology_description(desc)
        org = event_node.get("organizer")
        if isinstance(org, dict):
            out["hosting_entity"] = out.get("hosting_entity") or org.get("name")
        out.update(_addr_from_node(event_node))

    category = _allevents_category(soup)
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    kw_blob = meta_kw.get("content", "") if meta_kw else ""
    blob = " ".join(
        filter(
            None,
            [
                out.get("event_name"),
                out.get("event_description_methodology"),
                category,
                kw_blob,
            ],
        )
    )
    code, label = map_industry(blob, load_taxonomy())
    out["industry_methodology"] = code
    out["industry"] = label
    if category:
        out["event_category"] = category

    return out


def extract_listing_page(html: str, page_url: str) -> dict[str, Any]:
    host = (urlparse(page_url).netloc or "").lower()
    if "conferenceindex.org" in host:
        return extract_conferenceindex(html, page_url)
    if "allevents.in" in host:
        return extract_allevents(html, page_url)
    return {}
