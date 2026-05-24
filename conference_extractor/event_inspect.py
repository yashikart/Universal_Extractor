"""Per-event page inspect: fetch listing + subpages and save HTML artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from playwright_fetch import FetchResult, fetch_page


def event_slug(item: dict[str, Any]) -> str:
    name = item.get("name") or item.get("event_name") or item.get("title") or "event"
    url = item.get("event_url") or item.get("url") or ""
    safe = re.sub(r"[^\w\-]+", "-", name.lower()).strip("-")[:55]
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return f"{safe}-{digest}" if safe else digest


def event_inspect_dir(root: Path, item: dict[str, Any]) -> Path:
    return root / event_slug(item)


async def fetch_and_save_page(
    url: str,
    dest: Path,
    *,
    role: str,
) -> FetchResult:
    result = await fetch_page(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "role": role,
        "url": url,
        "ok": result.ok,
        "note": result.note,
        "html_file": dest.name if result.ok else None,
        "html_bytes": len(result.html) if result.html else 0,
    }
    if result.ok and result.html:
        dest.write_text(result.html, encoding="utf-8", errors="replace")
    (dest.with_suffix(".meta.json")).write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    return result


def save_pages_bundle(
    event_dir: Path,
    pages: dict[str, str],
    *,
    urls: dict[str, str],
    notes: list[str],
) -> None:
    event_dir.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    for role, html in pages.items():
        path = event_dir / f"{role}.html"
        path.write_text(html, encoding="utf-8", errors="replace")
        saved[role] = path.name

    bundle = {
        "pages": saved,
        "urls": urls,
        "notes": notes,
    }
    (event_dir / "inspect_meta.json").write_text(
        json.dumps(bundle, indent=2), encoding="utf-8"
    )


def write_coverage(event_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    from data_txt_fields import field_coverage

    cov = field_coverage(record)
    (event_dir / "coverage.json").write_text(
        json.dumps(cov, indent=2), encoding="utf-8"
    )
    return cov


def load_saved_pages(event_dir: Path) -> dict[str, str]:
    pages: dict[str, str] = {}
    if not event_dir.is_dir():
        return pages
    for path in sorted(event_dir.glob("*.html")):
        role = path.stem
        pages[role] = path.read_text(encoding="utf-8", errors="replace")
    return pages
