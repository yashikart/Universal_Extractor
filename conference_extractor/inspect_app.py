"""
Streamlit UI — capture listing pages, extract events, enrich to CSV.

  cd conference_extractor
  pip install -r requirements.txt
  playwright install chromium
  streamlit run inspect_app.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import streamlit as st

from csv_export import write_csv
from data_txt_fields import coverage_summary_row, field_coverage
from env_config import ci_official_search_enabled, load_env, openai_configured, tavily_configured
from enrich_events import enrich_events
from event_inspect import event_inspect_dir
from review_export import needs_review, review_row, write_review_csv
from inspect_page import InspectCaptureError, _inspect
from listing_html_extract import parse_event_listing_html

APP_DIR = Path(__file__).resolve().parent
OUT_DIR = APP_DIR / "inspect_out"
EVENTS_INSPECT_DIR = OUT_DIR / "events"
HTML_PATH = OUT_DIR / "page.html"


def _normalize_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _run_capture(url: str, *, headed: bool, chrome: bool) -> dict:
    return asyncio.run(
        _inspect(
            url,
            OUT_DIR,
            headed=headed,
            chrome_channel=chrome,
            proxy=None,
            apply_stealth=True,
            timeout_s=60.0,
            cf_max_wait_s=90.0,
            write_csv=False,
            screenshot=False,
        )
    )


def _load_events() -> list[dict] | None:
    upload = st.session_state.get("enrich_upload")
    if upload:
        return json.loads(upload)
    return st.session_state.get("last_events")


def _render_capture_tab(*, headed: bool, chrome: bool) -> None:
    url = st.text_input(
        "Listing URL",
        placeholder="https://conferenceindex.org/conferences/health-science",
    )
    if st.button("Capture & extract events", type="primary"):
        normalized = _normalize_url(url)
        if not normalized:
            st.error("Enter a listing URL.")
            return
        with st.spinner("Capturing page and extracting events…"):
            try:
                _run_capture(normalized, headed=headed, chrome=chrome)
            except InspectCaptureError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Capture failed: {e}")
                return

        if not HTML_PATH.is_file():
            st.error("Capture finished but page.html was not saved.")
            return

        events = parse_event_listing_html(
            HTML_PATH.read_text(encoding="utf-8", errors="replace")
        )
        st.session_state["last_events"] = events
        if events:
            st.success(f"Found {len(events)} event(s). Go to **Enrich to CSV** when ready.")
        else:
            st.warning("Capture saved, but no events were detected. Try a deeper listing URL.")

    events = st.session_state.get("last_events")
    if not events:
        return

    st.subheader(f"Events ({len(events)})")
    st.dataframe(
        [{"name": e.get("name"), "url": e.get("event_url")} for e in events],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "Download events.json",
        data=json.dumps(events, ensure_ascii=False, indent=2),
        file_name="events.json",
        mime="application/json",
    )


def _render_enrich_tab() -> None:
    uploaded = st.file_uploader("Or upload events.json", type=["json"])
    if uploaded is not None:
        st.session_state["enrich_upload"] = uploaded.read().decode("utf-8")

    events = _load_events()
    if not events:
        st.info("Capture a listing on the other tab first, or upload events.json.")
        return

    st.metric("Events", len(events))
    st.dataframe(
        [{"name": e.get("name"), "url": e.get("event_url")} for e in events[:100]],
        use_container_width=True,
        hide_index=True,
    )

    if not st.radio(
        "Does this list look correct?",
        ["No", "Yes — continue"],
        horizontal=True,
    ).startswith("Yes"):
        return

    limit: int | None = None
    if st.radio(
        "How many to enrich?",
        ["All", "First N only"],
        horizontal=True,
    ).startswith("First"):
        limit = int(
            st.number_input("N", min_value=1, max_value=len(events), value=min(5, len(events)))
        )

    if not st.button("Start enrichment", type="primary"):
        records = st.session_state.get("enriched_records")
        if not records:
            return
    else:
        progress = st.progress(0.0)
        status = st.empty()

        def on_progress(done: int, total: int, msg: str) -> None:
            progress.progress(min(1.0, done / max(total, 1)), text=msg)
            status.caption(msg)

        with st.spinner("Inspect → extract → data.txt fields…"):
            try:
                records = enrich_events(
                    events,
                    limit=limit,
                    inspect_dir=EVENTS_INSPECT_DIR,
                    on_progress=on_progress,
                )
            except Exception as exc:
                st.error(str(exc))
                return

        out_path = APP_DIR / "events_enriched.csv"
        cov_path = APP_DIR / "events_coverage.csv"
        review_path = APP_DIR / "events_review.csv"
        write_csv(records, out_path)
        import csv

        with cov_path.open("w", newline="", encoding="utf-8") as f:
            rows = [coverage_summary_row(r) for r in records]
            if rows:
                writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
        write_review_csv(records, review_path)
        st.session_state["enriched_records"] = records
        st.session_state["enriched_csv_path"] = str(out_path)
        st.session_state["coverage_csv_path"] = str(cov_path)
        st.session_state["review_csv_path"] = str(review_path)
        review_count = sum(1 for r in records if needs_review(r))
        st.success(
            f"Done — {len(records)} event(s). "
            f"{review_count} need review. Inspect: `{EVENTS_INSPECT_DIR}`"
        )

    records = st.session_state.get("enriched_records") or []
    if not records:
        return

    st.subheader("Per-event data.txt coverage")
    coverage_rows = []
    for i, r in enumerate(records):
        cov = field_coverage(r)
        src = events[i] if i < len(events) else {"name": r.get("event_name")}
        validation = r.get("validation") or {}
        coverage_rows.append(
            {
                "event": r.get("event_name"),
                "filled": cov["filled_count"],
                "total": cov["total_count"],
                "pct": cov["pct"],
                "in_scope": (r.get("scope") or {}).get("in_scope"),
                "needs_review": needs_review(r),
                "llm_stripped": validation.get("llm_stripped", 0),
                "heuristic_stripped": validation.get("heuristic_stripped", 0),
                "inspect_dir": str(event_inspect_dir(EVENTS_INSPECT_DIR, src)),
            }
        )
    st.dataframe(coverage_rows, use_container_width=True, hide_index=True)

    review_rows = [review_row(r) for r in records if needs_review(r)]
    if review_rows:
        st.subheader("Review queue")
        st.dataframe(review_rows, use_container_width=True, hide_index=True)

    st.subheader("Summary")
    st.dataframe(
        [
            {
                "event": r.get("event_name"),
                "url": r.get("event_url"),
                "in_scope": (r.get("scope") or {}).get("in_scope"),
            }
            for r in records
        ],
        use_container_width=True,
        hide_index=True,
    )
    csv_path = Path(st.session_state.get("enriched_csv_path", APP_DIR / "events_enriched.csv"))
    if csv_path.is_file():
        st.download_button(
            "Download events_enriched.csv",
            data=csv_path.read_bytes(),
            file_name="events_enriched.csv",
            mime="text/csv",
        )
    cov_csv = Path(
        st.session_state.get("coverage_csv_path", APP_DIR / "events_coverage.csv")
    )
    if cov_csv.is_file():
        st.download_button(
            "Download events_coverage.csv",
            data=cov_csv.read_bytes(),
            file_name="events_coverage.csv",
            mime="text/csv",
        )
    review_csv = Path(
        st.session_state.get("review_csv_path", APP_DIR / "events_review.csv")
    )
    if review_csv.is_file():
        st.download_button(
            "Download events_review.csv",
            data=review_csv.read_bytes(),
            file_name="events_review.csv",
            mime="text/csv",
        )


def main() -> None:
    load_env()
    st.set_page_config(page_title="Conference extractor", layout="wide")
    st.title("Conference extractor")

    with st.sidebar:
        st.subheader("API keys")
        st.caption(f"Config file: `{APP_DIR / '.env'}`")
        if tavily_configured():
            st.success("Tavily — configured")
            if ci_official_search_enabled():
                st.caption("CI official-site search: on")
            else:
                st.caption("CI official-site search: off")
        else:
            st.warning("Tavily — not set (copy `.env.example` → `.env`)")
        if openai_configured():
            st.success("OpenAI — configured")
        else:
            st.caption("OpenAI — optional (LLM on real event sites)")

        st.divider()
        st.caption("Advanced (bot walls)")
        headed = st.checkbox("Show browser", value=False)
        chrome = st.checkbox("Use Chrome", value=False)

    tab_capture, tab_enrich = st.tabs(["1. Capture & extract", "2. Enrich to CSV"])
    with tab_capture:
        _render_capture_tab(headed=headed, chrome=chrome)
    with tab_enrich:
        _render_enrich_tab()


if __name__ == "__main__":
    main()
