# Architecture

## Overview

The conference extractor is a three-phase pipeline: capture a listing page, extract event URLs, then enrich each event from its official website into a scoped CSV row.

```
┌──────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│  ① CAPTURE       │     │  ② EXTRACT           │     │  ③ ENRICH            │
│  (optional)      │     │                      │     │                      │
│  inspect_page.py │──►  │  listing_html_       │──►  │  enrich_events.py    │
│  inspect_app.py  │     │  extract.py          │     │  + web_search        │
│       │          │     │       │              │     │  + playwright_fetch  │
│       ▼          │     │       ▼              │     │  + parse_event_site  │
│   page.html      │     │  events.json         │     │  events_enriched.csv │
└──────────────────┘     └──────────────────────┘     └──────────────────────┘
```

---

## Phase 1 — Capture

**Modules:** `inspect_page.py`, `inspect_app.py` (Streamlit UI)  
**Input:** a conference listing URL  
**Output:** `inspect_out/page.html` (+ optional `elements.json`, screenshot)

Opens the listing URL in Chromium via Playwright. Waits through JavaScript rendering and common bot interstitials using stealth hooks in `harvest_all_html.py`.

---

## Phase 2 — Extract

**Module:** `listing_html_extract.py`  
**Input:** saved listing HTML  
**Output:** `events.json`

Detects event URLs via directory tables, JSON-LD, global `window.open`, and event-detail anchor heuristics.

---

## Phase 3 — Enrich

**Modules:** `enrich_events.py`, `web_search.py`, `playwright_fetch.py`, `parse_event_site.py`, `scope_eval.py`, `csv_export.py`  
**Input:** `events.json`  
**Output:** `events_enriched.csv` (one row per event; JSON columns for speakers/sponsors)

For each listing event the pipeline runs **inspect → extract → find all data.txt fields**:

1. **Inspect** — fetch listing (+ program on Conference Index), optional official site + exhibitor/sponsor/speaker subpages; save HTML under `inspect_out/events/<slug>/` with `inspect_meta.json` and `coverage.json`.
2. **Resolve official URL** — web search for non-aggregator listings (`TAVILY_API_KEY` if set, else DuckDuckGo). **Skipped** for Conference Index / aggregator hosts (listing + program only).
3. **Extract** — JSON-LD `Event`, meta tags, heuristics, optional LLM when a real official site exists. Every filled field gets **provenance** (`field_provenance`: source + evidence snippet). LLM output is **validated** against page HTML before merge; ungrounded entities are stripped.
4. **Validate** — `evidence_validator.py` checks snippets in HTML; `clear_unpublished_lists` when program is “coming soon”.
5. **Scope** — `taxonomy.json` + `scope_eval.py` (`in_scope` = B2B and (networking or engagement)); stores `scope_evidence`.
6. **Export** — `events_enriched.csv` + `events_coverage.csv` + **`events_review.csv`** (low-confidence / partial events).

**Streamlit flow** (`inspect_app.py` → **Enrich to CSV** tab):

1. Preview extracted event list (or upload `events.json`).
2. Confirm the list looks correct.
3. Choose **all events** or **limit to first N**.
4. Run per-event inspect → extract; download CSV, coverage report, and **review CSV**.

**Anti-hallucination:** Conference Index pages skip entity extraction and LLM. Official-site LLM runs at temperature 0 with required `evidence_snippet`; only empty scalars / append-only lists are merged after validation.

---

## Module reference

| Module | Role |
|---|---|
| `listing_html_extract.py` | Listing HTML parser |
| `harvest_all_html.py` | UA, stealth, Cloudflare heuristics |
| `inspect_page.py` | CLI capture |
| `inspect_app.py` | Streamlit UI (capture + enrich) |
| `web_search.py` | Official-site URL resolution |
| `playwright_fetch.py` | Fetch event pages |
| `parse_event_site.py` | Field extraction from official HTML |
| `conference_record.py` | Record schema (data.txt fields) |
| `taxonomy.json` | Industry + scope rules |
| `scope_eval.py` | B2B / in_scope evaluation |
| `csv_export.py` | CSV writer with JSON columns |
| `enrich_events.py` | Orchestrator + CLI (inspect dir, coverage CSV) |
| `event_inspect.py` | Per-event HTML bundle + `coverage.json` |
| `data_txt_fields.py` | All 25 data.txt fields + coverage helpers |
| `extract_heuristics.py` | JSON-LD, meta, entities, subpage discovery |
| `extract_listing.py` | Conference Index listing/program parser |
| `provenance.py` | Field-level source tracking and merge priority |
| `evidence_validator.py` | Snippet-in-HTML checks; LLM/heuristic entity filtering |
| `review_export.py` | Review queue CSV (`events_review.csv`) |
| `env_config.py` | `.env` loading (Tavily, OpenAI, CI search flag) |

---

## How to run

Optional env file: copy `.env.example` to `.env` in this folder.

| Variable | Purpose |
|---|---|
| `TAVILY_API_KEY` | Tavily search for official event sites (enables CI search too) |
| `OPENAI_API_KEY` | Optional LLM extraction on real event websites |
| `ENABLE_CI_OFFICIAL_SEARCH` | `true` (default) — Tavily search for Conference Index events |

```bash
copy .env.example .env
# edit .env — add TAVILY_API_KEY=tvly-...
pip install -r requirements.txt
playwright install chromium

# Full UI (capture → extract → enrich)
streamlit run inspect_app.py

# CLI enrich only (saves inspect_out/events/<slug>/ per event)
python enrich_events.py events.json --out events_enriched.csv --limit 5
```

Search uses **Tavily** when `TAVILY_API_KEY` is in `.env`; otherwise DuckDuckGo (non-aggregator listings only).

---

## Extension points

**Search quality** — tune blocklist/scoring in `web_search.py`, or add a paid provider via `TAVILY_API_KEY`.

**Field coverage** — extend `parse_event_site.py` for site-specific DOM patterns; add subpage crawls for exhibitor/sponsor lists.

**Scope rules** — edit `taxonomy.json` to match updated data.txt methodology.
