"""Fetch rendered HTML with Playwright (+ optional stealth)."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from harvest_all_html import (
    DEFAULT_UA,
    _apply_playwright_stealth,
    _is_cf_hard_ip_block,
    _is_cloudflare_block,
)


async def fetch_page_html(
    url: str,
    *,
    headed: bool = False,
    chrome_channel: bool = False,
    proxy: Optional[str] = None,
    apply_stealth: bool = True,
    timeout_s: float = 90.0,
    cf_max_wait_s: float = 90.0,
) -> str:
    from playwright.async_api import async_playwright

    launch_kwargs: dict = {
        "headless": not headed,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if chrome_channel:
        launch_kwargs["channel"] = "chrome"
    if proxy and proxy.strip():
        launch_kwargs["proxy"] = {"server": proxy.strip()}

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
            nav_ms = int(max(30.0, timeout_s) * 1000)
            await page.goto(url, wait_until="domcontentloaded", timeout=nav_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(60_000, nav_ms))
            except Exception:
                pass
            await page.wait_for_timeout(2000)
            deadline = time.monotonic() + max(45.0, cf_max_wait_s, timeout_s + 30.0)
            cf_iter = 0
            while time.monotonic() < deadline and cf_iter < 45:
                cf_iter += 1
                html = await page.content()
                if _is_cf_hard_ip_block(html):
                    raise RuntimeError(
                        "Cloudflare hard block (IP). Try --headed --chrome or --proxy."
                    )
                if not _is_cloudflare_block(html):
                    break
                await asyncio.sleep(1.0)
            return await page.content()
        finally:
            await page.close()
            await context.close()
            await browser.close()
