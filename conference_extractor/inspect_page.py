"""
Dump a page's HTML plus a full DOM element listing (flat tree with parent links).

Uses Playwright with helpers from ``harvest_all_html``. Run **from this folder**
(``.../conference_extractor``), not from a nested ``conference_extractor`` path:

  cd C:\\Users\\YOU\\Universal_Extractor\\conference_extractor

  pip install -r requirements.txt
  playwright install chromium
  python inspect_page.py https://example.com/events --out inspect_out --chrome --headed

Outputs:
  - page.html          raw HTML after navigation / challenge wait
  - elements.json      all elements (tag, id, class, attrs, text preview, parent index)
  - elements.csv       same data tabular (optional with --csv)

For list pages, extract events without an LLM:
  python listing_html_extract.py <out_dir>/page.html --out events.json

Or use the Streamlit UI:
  streamlit run inspect_app.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from harvest_all_html import (
    DEFAULT_UA,
    _apply_playwright_stealth,
    _is_cf_hard_ip_block,
    _is_cloudflare_block,
)

class InspectCaptureError(RuntimeError):
    """Page capture failed (e.g. Cloudflare block); partial files may exist in out_dir."""


EVAL_ELEMENTS = """
() => {
  const flat = [];
  function visit(el, parentIdx) {
    if (el.nodeType !== 1) return;
    const idx = flat.length;
    const attrs = {};
    for (const a of el.attributes || []) attrs[a.name] = a.value;
    let cls = el.className;
    if (cls && typeof cls !== "string") cls = String(cls);
    const text = (el.textContent || "").replace(/\\s+/g, " ").trim().slice(0, 400);
    flat.push({
      index: idx,
      parent_index: parentIdx,
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      class: cls || null,
      attrs,
      text_preview: text || null,
      child_element_count: el.children ? el.children.length : 0,
    });
    for (const c of el.children) visit(c, idx);
  }
  if (document.documentElement) visit(document.documentElement, -1);
  return flat;
}
"""


async def _inspect(
    url: str,
    out_dir: Path,
    *,
    headed: bool,
    chrome_channel: bool,
    proxy: Optional[str],
    apply_stealth: bool,
    timeout_s: float,
    cf_max_wait_s: float,
    write_csv: bool,
    screenshot: bool,
) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    out_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict = {
        "headless": not headed,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if chrome_channel:
        launch_kwargs["channel"] = "chrome"
    if (proxy or "").strip():
        launch_kwargs["proxy"] = {"server": (proxy or "").strip()}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            user_agent=DEFAULT_UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            java_script_enabled=True,
        )
        await context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
        page = await context.new_page()
        try:
            if apply_stealth:
                await _apply_playwright_stealth(page)

            nav_timeout = int(max(30.0, timeout_s) * 1000)
            await page.goto(url, wait_until="domcontentloaded", timeout=nav_timeout)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(60_000, nav_timeout))
            except Exception:
                pass

            deadline = time.monotonic() + max(45.0, cf_max_wait_s, timeout_s + 30.0)
            while time.monotonic() < deadline:
                html = await page.content()
                if _is_cf_hard_ip_block(html):
                    (out_dir / "page.html").write_text(html, encoding="utf-8", errors="replace")
                    raise InspectCaptureError(
                        "Cloudflare hard block (IP). Try another network, VPN, or --proxy. "
                        "Saved partial page.html for inspection."
                    )
                if not _is_cloudflare_block(html):
                    break
                await asyncio.sleep(1.0)

            html = await page.content()
            if _is_cloudflare_block(html):
                (out_dir / "page.html").write_text(html, encoding="utf-8", errors="replace")
                raise InspectCaptureError(
                    "Still on Cloudflare challenge. Try --headed --chrome --proxy, "
                    "or increase timeouts. Saved page.html."
                )

            if screenshot:
                await page.screenshot(path=str(out_dir / "screenshot.png"), full_page=False)

            elements: List[Dict[str, Any]] = await page.evaluate(EVAL_ELEMENTS)

            (out_dir / "page.html").write_text(html, encoding="utf-8", errors="replace")
            (out_dir / "elements.json").write_text(
                json.dumps({"url": page.url, "element_count": len(elements), "elements": elements}, indent=2),
                encoding="utf-8",
            )

            meta = {
                "final_url": page.url,
                "element_count": len(elements),
                "files": {"html": "page.html", "json": "elements.json"},
            }
            if screenshot:
                meta["files"]["screenshot"] = "screenshot.png"
            (out_dir / "inspect_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

            if write_csv and elements:
                fields = ["index", "parent_index", "tag", "id", "class", "child_element_count", "text_preview", "attrs_json"]
                with (out_dir / "elements.csv").open("w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                    w.writeheader()
                    for row in elements:
                        r = {**row, "attrs_json": json.dumps(row.get("attrs") or {}, ensure_ascii=False)}
                        w.writerow(r)

            return meta
        finally:
            await page.close()
            await context.close()
            await browser.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Save page HTML + full DOM element listing (JSON/CSV).")
    p.add_argument("url", help="Page URL to inspect")
    p.add_argument("--out", type=str, default="inspect_out", help="Output directory")
    p.add_argument("--chrome", action="store_true", help="Use installed Google Chrome")
    p.add_argument("--headed", action="store_true", help="Show browser window")
    p.add_argument("--proxy", type=str, default=None, help="HTTP(S) proxy URL")
    p.add_argument("--no-stealth", action="store_true", help="Disable playwright-stealth")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--cf-wait", type=float, default=90.0)
    p.add_argument("--csv", action="store_true", help="Also write elements.csv")
    p.add_argument("--screenshot", action="store_true", help="Save screenshot.png (viewport)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    out = Path(args.out)

    try:
        meta = asyncio.run(
            _inspect(
                url,
                out,
                headed=args.headed,
                chrome_channel=args.chrome,
                proxy=args.proxy,
                apply_stealth=not args.no_stealth,
                timeout_s=max(10.0, args.timeout),
                cf_max_wait_s=max(15.0, args.cf_wait),
                write_csv=args.csv,
                screenshot=args.screenshot,
            )
        )
        print(json.dumps(meta, indent=2), flush=True)
    except InspectCaptureError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(1) from e
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
