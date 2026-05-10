"""
Playwright helpers shared by inspect / harvest scripts: UA, stealth, Cloudflare heuristics.
"""

from __future__ import annotations

# Match current Windows Chrome look; override stealth's default to stay consistent with context UA.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Challenge / interstitial pages (best-effort string match).
_CF_CHALLENGE_MARKERS = (
    "cdn-cgi/challenge-platform",
    "cf-challenge",
    "__cf_chl_jschl_tk__",
    "chk_jschl",
    "cf-browser-verification",
    "checking your browser",
    "just a moment",
    "turnstile",
    "cf-chl-bypass",
)

# Hard blocks (often IP / WAF) — page may save but navigation will not complete usefully.
_CF_HARD_BLOCK_MARKERS = (
    "error 1020",
    "access denied",
    "banned your ip",
    "your ip has been blocked",
    "blocked by cloudflare",
)


def _is_cloudflare_block(html: str) -> bool:
    if not html:
        return False
    lower = html.lower()
    return any(m in lower for m in _CF_CHALLENGE_MARKERS)


def _is_cf_hard_ip_block(html: str) -> bool:
    if not html:
        return False
    lower = html.lower()
    return any(m in lower for m in _CF_HARD_BLOCK_MARKERS)


async def _apply_playwright_stealth(page) -> None:
    from playwright_stealth import Stealth

    stealth = Stealth(navigator_user_agent_override=DEFAULT_UA)
    await stealth.apply_stealth_async(page)
