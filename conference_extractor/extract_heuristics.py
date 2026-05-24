"""Regex and DOM heuristics for conference field extraction (no API key)."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from conference_record import domain_from_url
from scope_eval import methodology_description

DATE_ISO = re.compile(
    r"\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b"
)
DATE_RANGE = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?,?\s*(20\d{2})\b",
    re.I,
)
DATE_LONG = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December|"
    r"Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+(\d{1,2}),?\s*(20\d{2})\b",
    re.I,
)
ZIP_RE = re.compile(r"\b(\d{5}(?:-\d{4})?)\b")
CITY_STATE = re.compile(
    r"\b([A-Za-z][A-Za-z .'-]{2,40}),\s*([A-Z]{2})\b"
)

JUNK_ALT = frozenset(
    {
        "logo", "icon", "image", "banner", "sponsor", "partner", "exhibitor", "home",
        "contact", "conferences", "disciplines", "locations", "search", "login", "log in",
        "donate", "cookie policy", "read more", "icons", "noted", "related events",
        "more events", "submit your event for free", "conference index", "home",
        "members", "faculty", "publications", "who we are", "photo gallery",
    }
)

NAV_PREFIXES = (
    "home", "contact", "conference", "past conference", "search for", "cookie",
    "log in", "donate", "download", "read more", "want more information",
)

TIME_SPEAKER_RE = re.compile(
    r"^\d{1,2}(:\d{2})?\s*(to|-)?\s*\d{0,2}:?\d{0,2}\s*(a\.?m\.?|p\.?m\.?)?",
    re.I,
)
SPEAKER_JUNK_RE = re.compile(
    r"(registration|sign-in|welcome message|opening session|closing session|"
    r"break\b|lunch\b|coffee\b|networking\b|cpeu\b|location:)",
    re.I,
)
NAME_LIKE_RE = re.compile(
    r"^(?:Dr\.|Prof\.|Mr\.|Ms\.|Mrs\.)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,4}|"
    r"^[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?$",
)
PANELIST_SPLIT = re.compile(r"\bPanelists?:\s*", re.I)


def soup_to_text(soup: BeautifulSoup, max_chars: int = 8000) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    alts = [
        (img.get("alt") or "").strip()
        for img in soup.find_all("img", alt=True)
        if (img.get("alt") or "").strip()
    ]
    if alts:
        text = f"{text}\n" + "\n".join(alts)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out = "\n".join(lines)
    return out[:max_chars]


def _text(el: Tag | None) -> str | None:
    if el is None:
        return None
    t = el.get_text(" ", strip=True)
    return t if t else None


def _iter_json_ld(soup: BeautifulSoup) -> list[Any]:
    out: list[Any] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            out.extend(data)
        else:
            out.append(data)
    return out


def _flatten_graph(items: list[Any]) -> list[dict[str, Any]]:
    flat: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        graph = item.get("@graph")
        if graph:
            flat.extend(x for x in graph if isinstance(x, dict))
        else:
            flat.append(item)
    return flat


def _is_event_type(node: dict[str, Any]) -> bool:
    t = node.get("@type")
    if t == "Event":
        return True
    if isinstance(t, list):
        return "Event" in t
    return False


def _addr_from_node(node: dict[str, Any]) -> dict[str, Any]:
    loc = node.get("location") or {}
    if isinstance(loc, list):
        loc = loc[0] if loc else {}
    if not isinstance(loc, dict):
        return {}
    addr = loc.get("address") or {}
    if isinstance(addr, str):
        return {"address_1": addr, "venue_name": loc.get("name")}
    if not isinstance(addr, dict):
        return {"venue_name": loc.get("name")}
    return {
        "venue_name": loc.get("name") or addr.get("name"),
        "address_1": addr.get("streetAddress"),
        "address_2": addr.get("addressLine2"),
        "city": addr.get("addressLocality"),
        "state_province": addr.get("addressRegion"),
        "zip_code": addr.get("postalCode"),
        "country": addr.get("addressCountry"),
    }


def _meta(soup: BeautifulSoup, *keys: str) -> str | None:
    for key in keys:
        tag = soup.find("meta", attrs={"name": key}) or soup.find("meta", property=key)
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def find_subpage(
    soup: BeautifulSoup,
    base_url: str,
    keywords: tuple[str, ...],
    *,
    prefer_same_host: bool = False,
) -> str | None:
    return _find_subpage(soup, base_url, keywords, prefer_same_host=prefer_same_host)


def _host(url: str) -> str:
    return (urlparse(url).netloc or "").lower().lstrip("www.")


def _find_subpage(
    soup: BeautifulSoup,
    base_url: str,
    keywords: tuple[str, ...],
    *,
    prefer_same_host: bool = False,
) -> str | None:
    best: tuple[float, str] | None = None
    base_host = _host(base_url)
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(base_url, href)
        if _is_bad_subpage(full):
            continue
        label = (a.get_text(" ", strip=True) or "").lower()
        path = href.lower()
        score = float(sum(1 for k in keywords if k in label or k in path))
        if score <= 0:
            continue
        if any(k in label for k in keywords):
            score += 1.5
        if any(k.replace(" ", "-") in path or k.replace(" ", "_") in path for k in keywords):
            score += 1.0
        link_host = _host(full)
        if prefer_same_host and base_host:
            if link_host == base_host:
                score += 12.0
            else:
                score -= 8.0
        if score > (best[0] if best else -1.0):
            best = (score, full)
    return best[1] if best else None


def _is_boilerplate(tag: Tag | None) -> bool:
    if tag is None:
        return False
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        name = (parent.name or "").lower()
        if name in ("footer", "nav", "header", "aside"):
            return True
        pid = (parent.get("id") or "").lower()
        classes = " ".join(parent.get("class") or []).lower()
        blob = f"{pid} {classes}"
        if any(x in blob for x in ("footer", "navbar", "nav-menu", "site-nav", "breadcrumb", "cookie")):
            return True
    return False


def _semantic_section(tag: Tag, keywords: tuple[str, ...]) -> bool:
    classes = " ".join(tag.get("class") or []).lower()
    tid = (tag.get("id") or "").lower()
    blob = f"{classes} {tid}"
    return any(k.replace(" ", "-") in blob or k.replace(" ", "_") in blob for k in keywords)


def _section_root(soup: BeautifulSoup, keywords: tuple[str, ...]) -> Tag | None:
    """Find the DOM subtree that holds sponsor/exhibitor/partner logos or lists."""
    priority_markers = (
        "partners-root",
        "sponsors-list",
        "sponsor-list",
        "exhibitors-list",
        "exhibitor-list",
        "partner-grid",
        "sponsor-grid",
    )
    scored: list[tuple[int, Tag]] = []

    for tag in soup.find_all(class_=True):
        if _is_boilerplate(tag):
            continue
        classes = " ".join(tag.get("class", [])).lower()
        tid = (tag.get("id") or "").lower()
        blob = f"{classes} {tid}"
        if not any(
            k.replace(" ", "-") in blob or k.replace(" ", "_") in blob or k in blob
            for k in keywords
        ):
            continue
        score = 1
        if any(m in blob for m in priority_markers):
            score += 10
        if "partner-card" in blob or "gold-grid" in blob or "gold-cell" in blob:
            score += 5
        scored.append((score, tag))

    if scored:
        scored.sort(key=lambda item: -item[0])
        return scored[0][1]

    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        if _is_boilerplate(tag):
            continue
        t = _text(tag)
        if not t or len(t) > 100:
            continue
        lower = t.lower()
        if any(k in lower for k in keywords):
            parent = tag.find_parent(["section", "main", "article"])
            if parent and not _is_boilerplate(parent):
                return parent
            return tag

    for tag in soup.find_all(["section", "main"]):
        if _is_boilerplate(tag):
            continue
        classes = " ".join(tag.get("class") or []).lower()
        tid = (tag.get("id") or "").lower()
        blob = f"{classes} {tid}"
        if any(k in blob for k in keywords):
            return tag

    return None


def _name_from_img(img: Tag) -> str | None:
    alt = (img.get("alt") or "").strip()
    if not alt or len(alt) < 2 or len(alt) > 120:
        return None
    if alt.lower() in JUNK_ALT:
        return None
    return alt


def _website_near(el: Tag, base_host: str) -> str | None:
    if el.name == "a" and el.get("href"):
        href = el["href"].strip()
        if href.startswith("http") and base_host not in urlparse(href).netloc.lower():
            return href
    a = el.find("a", href=True)
    if a:
        href = a["href"].strip()
        if href.startswith("http"):
            host = urlparse(href).netloc.lower()
            if host and base_host not in host:
                return href
    return None


def _is_junk_speaker_name(name: str) -> bool:
    if _is_junk_name(name):
        return True
    if TIME_SPEAKER_RE.match(name.strip()):
        return True
    if SPEAKER_JUNK_RE.search(name):
        return True
    if len(name) > 70:
        return True
    if name.count(":") >= 2:
        return True
    return False


def _parse_speaker_candidate(raw: str) -> dict[str, Any] | None:
    raw = re.sub(r"\s+", " ", raw.strip(" ,;•"))
    if not raw or _is_junk_speaker_name(raw):
        return None
    title = None
    company = None
    name = raw
    if " — " in raw:
        name, title = [p.strip() for p in raw.split(" — ", 1)]
    elif " - " in raw and raw.count(" - ") == 1:
        left, right = raw.split(" - ", 1)
        if len(left.split()) <= 6:
            name, title = left.strip(), right.strip()
    if " at " in name.lower():
        parts = re.split(r"\s+at\s+", name, maxsplit=1, flags=re.I)
        if len(parts) == 2 and len(parts[0].split()) <= 5:
            name, company = parts[0].strip(), parts[1].strip()
    if _is_junk_speaker_name(name):
        return None
    return {"name": name, "title": title, "company": company, "details": raw}


def _extract_speakers_from_text(text: str) -> list[dict[str, Any]]:
    speakers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in PANELIST_SPLIT.split(text):
        if not chunk.strip():
            continue
        segment = chunk.split(". Description:", 1)[0]
        segment = segment.split(" Location:", 1)[0]
        for part in re.split(r"(?<=[a-z])\s+(?=Dr\.|Prof\.)|[,;•\n]", segment):
            cand = _parse_speaker_candidate(part)
            if not cand:
                continue
            key = cand["name"].lower()
            if key in seen:
                continue
            seen.add(key)
            speakers.append(cand)
    for line in text.splitlines():
        line = line.strip()
        if not line or len(line) > 200:
            continue
        if re.search(r"\b(keynote|speaker|presenter|panelist)\b", line, re.I):
            for part in re.split(r"[,;•]", line):
                cand = _parse_speaker_candidate(part)
                if cand:
                    key = cand["name"].lower()
                    if key not in seen:
                        seen.add(key)
                        speakers.append(cand)
    return speakers[:80]


def _is_bad_subpage(url: str) -> bool:
    lower = (url or "").lower()
    if lower.endswith(".pdf"):
        return True
    return any(
        x in lower
        for x in (
            "donor-form",
            "blackbaud",
            "login",
            "signup",
            "register",
            "cookie",
            "privacy",
            "terms",
            "facebook.com",
            "twitter.com",
            "linkedin.com",
        )
    )


def _is_junk_name(name: str) -> bool:
    lower = name.lower().strip()
    if lower in JUNK_ALT:
        return True
    if " logo" in lower or lower.endswith(" logo"):
        return True
    if any(lower.startswith(p) for p in NAV_PREFIXES):
        return True
    if len(lower.split()) > 8:
        return True
    return False


def extract_entities(
    soup: BeautifulSoup,
    base_url: str,
    keywords: tuple[str, ...],
) -> list[dict[str, Any]]:
    base_host = (urlparse(base_url).netloc or "").lower()
    root = _section_root(soup, keywords)
    if root is None and any(k in keywords for k in ("partner", "partners", "sponsor", "sponsors")):
        main = soup.find("main") or soup.find(id="main-content")
        if isinstance(main, Tag) and not _is_boilerplate(main):
            root = main
    if root is None:
        return []
    scope = root
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: str | None, website: str | None = None, details: str | None = None) -> None:
        if not name:
            return
        name = re.sub(r"\s+", " ", name).strip()
        if len(name) < 2 or len(name) > 120:
            return
        if _is_junk_name(name):
            return
        key = name.lower()
        if key in seen:
            return
        if website:
            site_host = (urlparse(website).netloc or "").lower()
            if site_host and base_host and site_host == base_host:
                return
        seen.add(key)
        entities.append({"name": name, "website": website, "details": details})

    cards = [
        c
        for c in scope.find_all(["article", "li", "div"], limit=200)
        if not _is_boilerplate(c)
        and (_semantic_section(c, keywords) or c.find("img", alt=True))
    ]

    for img in scope.find_all("img", alt=True):
        if _is_boilerplate(img):
            continue
        parent = img.find_parent(["a", "div", "li", "article"])
        in_section = parent is not None and (
            _semantic_section(parent, keywords)
            or any(
                isinstance(p, Tag) and _semantic_section(p, keywords)
                for p in parent.parents
            )
        )
        if not in_section:
            classes = " ".join(img.get("class") or []).lower()
            if "logo" not in classes and "partner" not in classes and "sponsor" not in classes:
                continue
        name = _name_from_img(img)
        if name:
            add(name, _website_near(img.parent or img, base_host))

    for card in cards:
        if _is_boilerplate(card):
            continue
        img = card.find("img", alt=True)
        if img:
            name = _name_from_img(img)
            if name:
                add(name, _website_near(card, base_host), _text(card.find("p")))
                continue
        title = card.find(["h3", "h4", "h5", "strong"])
        name = _text(title)
        if name:
            add(name, _website_near(card, base_host), _text(card.find("p")))

    for a in scope.select(
        ".pic a[href], a.logo[href], [class*='partner'] a[href], [class*='sponsor'] a[href]"
    ):
        if _is_boilerplate(a):
            continue
        label = _text(a)
        href = (a.get("href") or "").strip()
        if not label or len(label) > 80 or not href.startswith("http"):
            continue
        if base_host not in urlparse(href).netloc.lower():
            add(label, href)

    return entities[:120]


def extract_speakers(soup: BeautifulSoup) -> list[dict[str, Any]]:
    speakers: list[dict[str, Any]] = []
    seen: set[str] = set()
    keywords = ("speaker", "keynote", "panelist", "presenter", "faculty")

    text = soup_to_text(soup, 15000)
    for sp in _extract_speakers_from_text(text):
        key = sp["name"].lower()
        if key not in seen:
            seen.add(key)
            speakers.append(sp)

    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        t = _text(tag)
        if not t or not any(k in t.lower() for k in keywords):
            continue
        block = tag.find_parent(["section", "main", "div"]) or tag
        for card in block.find_all(["article", "li", "div"], limit=100):
            name_el = card.find(["h3", "h4", "h5", "strong"])
            name = _text(name_el)
            if not name or _is_junk_speaker_name(name):
                continue
            paras = card.find_all("p")
            title = _text(paras[0]) if paras else None
            company = _text(paras[1]) if len(paras) > 1 else None
            if title == name:
                title = None
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            speakers.append(
                {
                    "name": name,
                    "title": title,
                    "company": company,
                    "details": _text(card),
                }
            )
    return speakers[:80]


MONTH_MAP = {
    "january": "01", "jan": "01",
    "february": "02", "feb": "02",
    "march": "03", "mar": "03",
    "april": "04", "apr": "04",
    "may": "05",
    "june": "06", "jun": "06",
    "july": "07", "jul": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "december": "12", "dec": "12",
}


def extract_dates_from_text(text: str) -> tuple[str | None, str | None]:
    isos = DATE_ISO.findall(text)
    if isos:
        dates = [f"{y}-{m}-{d}" for y, m, d in isos]
        dates.sort()
        return dates[0], dates[-1] if len(dates) > 1 else dates[0]

    m = DATE_LONG.search(text)
    if m:
        month_raw, day, year = m.group(1), m.group(2), m.group(3)
        month = MONTH_MAP.get(month_raw.lower()[:3], MONTH_MAP.get(month_raw.lower()))
        if month:
            iso = f"{year}-{month}-{int(day):02d}"
            return iso, iso

    m = DATE_RANGE.search(text)
    if m:
        month_raw, d1, d2, year = m.group(1), m.group(2), m.group(3), m.group(4)
        month = MONTH_MAP.get(month_raw.lower()[:3], MONTH_MAP.get(month_raw.lower()))
        if month:
            start = f"{year}-{month}-{int(d1):02d}"
            end_day = d2 or d1
            end = f"{year}-{month}-{int(end_day):02d}"
            return start, end
    return None, None


def extract_from_html(html: str, page_url: str, page_role: str = "main") -> dict[str, Any]:
    from extract_listing import extract_listing_page

    listing_data = extract_listing_page(html, page_url)
    if listing_data and page_role in ("main", "listing", "speakers", "program"):
        base = dict(listing_data)
    else:
        base = {}
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {
        "event_url": page_url,
        "conference_domain": domain_from_url(page_url),
    }

    flat = _flatten_graph(_iter_json_ld(soup))
    event_node = next((n for n in flat if _is_event_type(n)), None)
    org_node = next(
        (
            n
            for n in flat
            if n.get("@type") in ("Organization", "Corporation")
            or (isinstance(n.get("@type"), list) and "Organization" in n["@type"])
        ),
        None,
    )

    if event_node:
        out["event_name"] = event_node.get("name")
        out["start_date"] = event_node.get("startDate")
        out["end_date"] = event_node.get("endDate")
        desc = event_node.get("description")
        if isinstance(desc, str):
            out["event_description_methodology"] = methodology_description(desc)
        org = event_node.get("organizer")
        if isinstance(org, dict):
            out["hosting_entity"] = org.get("name")
        elif isinstance(org, str):
            out["hosting_entity"] = org
        out.update(_addr_from_node(event_node))

    if org_node and not out.get("hosting_entity"):
        out["hosting_entity"] = org_node.get("name")
        out["conference_group_name"] = org_node.get("name")
        desc = org_node.get("description")
        if isinstance(desc, str):
            out["conference_group_description"] = methodology_description(desc)

    out["event_name"] = out.get("event_name") or _meta(soup, "og:title", "twitter:title")
    desc = _meta(soup, "description", "og:description", "twitter:description")
    if desc:
        out["event_description_methodology"] = methodology_description(desc)

    site = _meta(soup, "og:site_name", "application-name")
    if site:
        out["conference_group_name"] = out.get("conference_group_name") or site

    text = soup_to_text(soup, 12000)
    if not out.get("start_date"):
        s, e = extract_dates_from_text(text)
        out["start_date"] = s
        out["end_date"] = e or s

    out["exhibitor_crawler_url"] = _find_subpage(
        soup,
        page_url,
        ("exhibitor", "exhibitors", "exhibitor-list", "floor-plan", "expo"),
        prefer_same_host=True,
    )
    out["sponsor_crawler_url"] = _find_subpage(
        soup,
        page_url,
        ("sponsor", "sponsors", "partner", "partners", "supporters"),
        prefer_same_host=True,
    )

    host = (urlparse(page_url).netloc or "").lower()
    is_listing = any(
        x in host for x in ("conferenceindex.org", "allevents.in", "10times.com", "eventbrite")
    )

    if page_role in ("main", "sponsors") and not is_listing:
        sponsors = extract_entities(soup, page_url, ("sponsor", "partner", "partners"))
        if sponsors:
            out["sponsor_companies"] = sponsors
    if page_role in ("main", "exhibitors") and not is_listing:
        exhibitors = extract_entities(soup, page_url, ("exhibitor", "exhibitors", "expo"))
        if exhibitors:
            out["exhibitor_companies"] = exhibitors
    if page_role in ("main", "speakers", "program") and not is_listing:
        speakers = extract_speakers(soup)
        if speakers:
            out["speakers"] = speakers

    if page_role == "main" and not is_listing:
        attending = extract_entities(
            soup, page_url, ("attend", "attendee", "who attends", "delegates", "companies")
        )
        if attending:
            out["attending_companies"] = attending

    if base:
        for key, val in base.items():
            if key == "program_url":
                continue
            if val is None or val == "" or val == []:
                continue
            if key not in out or out.get(key) in (None, "", []):
                out[key] = val

    from provenance import stamp

    prov: dict[str, Any] = {}
    listing_keys = set(base.keys()) if base else set()
    json_ld_keys = {
        "event_name",
        "start_date",
        "end_date",
        "event_description_methodology",
        "hosting_entity",
        "venue_name",
        "address_1",
        "address_2",
        "city",
        "country",
        "state_province",
        "zip_code",
        "conference_group_name",
        "conference_group_description",
    }
    for key, val in out.items():
        if key.startswith("_") or key in ("event_url", "conference_domain", "program_url"):
            continue
        if val is None or val == "" or val == []:
            continue
        if event_node and key in json_ld_keys:
            source = "json_ld"
        elif key in listing_keys:
            source = "listing"
        elif key in ("exhibitor_companies", "sponsor_companies", "speakers", "attending_companies"):
            source = "heuristic"
        elif key in ("start_date", "end_date") and not event_node:
            source = "regex"
        elif key in ("event_name", "event_description_methodology", "conference_group_name"):
            source = "meta"
        elif key in ("industry_methodology", "industry"):
            source = "taxonomy"
        else:
            source = "heuristic"
        snippet: str | None = None
        if isinstance(val, str):
            snippet = val[:120]
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            snippet = (val[0].get("name") or "")[:120]
        stamp(
            prov,
            key,
            source=source,
            page_url=page_url,
            page_role=page_role,
            snippet=snippet,
        )
    out["_provenance"] = prov
    return out
