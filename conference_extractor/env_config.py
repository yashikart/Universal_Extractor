"""Load API keys from conference_extractor/.env (python-dotenv)."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"

_loaded = False


def load_env() -> None:
    global _loaded
    if _loaded:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH, override=False)
    except ImportError:
        pass
    _loaded = True


def env_bool(name: str, *, default: bool = False) -> bool:
    load_env()
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def tavily_configured() -> bool:
    load_env()
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def openai_configured() -> bool:
    load_env()
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def ci_official_search_enabled() -> bool:
    """Search official sites for aggregator listings when Tavily is configured."""
    load_env()
    if not tavily_configured():
        return False
    return env_bool("ENABLE_CI_OFFICIAL_SEARCH", default=True)
