"""Fetch event pages with Playwright (stealth + Cloudflare heuristics)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from harvest_all_html import (
    DEFAULT_UA,
    _apply_playwright_stealth,
    _is_cf_hard_ip_block,
    _is_cloudflare_block,
)


@dataclass
class FetchResult:
    url: str
    html: str
    ok: bool
    note: str | None = None


def _looks_like_real_page(html: str) -> bool:
    if not html or len(html) < 400:
        return False
    lower = html.lower()
    if _is_cf_hard_ip_block(html) or _is_cloudflare_block(html):
        return False
    if "just a moment" in lower and "cloudflare" in lower:
        return False
    return True


async def _httpx_fetch(url: str, timeout: float = 25.0) -> FetchResult:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": DEFAULT_UA, "Accept-Language": "en-US,en;q=0.9"},
        ) as client:
            resp = await client.get(url)
            html = resp.text
            if resp.status_code >= 400:
                return FetchResult(
                    url=url,
                    html=html,
                    ok=False,
                    note=f"http {resp.status_code}",
                )
            if _looks_like_real_page(html):
                return FetchResult(url=url, html=html, ok=True, note="httpx")
            return FetchResult(url=url, html=html, ok=False, note="httpx: blocked or empty")
    except Exception as exc:
        return FetchResult(url=url, html="", ok=False, note=f"httpx: {exc}")


async def fetch_page(url: str, timeout_ms: int = 45000) -> FetchResult:
    fast = await _httpx_fetch(url)
    if fast.ok:
        return fast

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=DEFAULT_UA, locale="en-US")
        page = await context.new_page()
        await _apply_playwright_stealth(page)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1500)
            html = await page.content()
        except Exception as exc:
            await browser.close()
            return FetchResult(url=url, html="", ok=False, note=str(exc))

        await browser.close()

    if _is_cf_hard_ip_block(html):
        return FetchResult(url=url, html=html, ok=False, note="cloudflare hard block")
    if _is_cloudflare_block(html):
        return FetchResult(url=url, html=html, ok=False, note="cloudflare challenge")
    return FetchResult(url=url, html=html, ok=True, note="playwright")
