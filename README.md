# Cinema Dashboard

A Streamlit dashboard that merges Letterboxd watchlist data with French cinema showtimes.

## Overview

Cinema Dashboard is the orchestration layer for a three-project pipeline:

| Project | Role |
|---------|------|
| `movies_management` | Fetches and caches Letterboxd ratings + watchlist as parquet files |
| `Allocine-Showtimes-Scraping` | Scrapes French cinema showtimes to `showtimes.parquet` |
| `cinema_dashboard` *(this repo)* | Reads both parquets and visualises the combined data |

The dashboard is mostly read-only — it reads parquet files written by the other two projects. The one exception is the Recommendations page, which can append new theaters to the theaters CSV (`ALLOCINE_INPUT_PATH`) when the user confirms adding one via the chat.

## Pages

### Showtimes (🎟️)

Reads `showtimes.parquet` produced by `Allocine-Showtimes-Scraping` and displays upcoming showtimes by theater. Run the scraper CLI to refresh the data.

**Requires**: `ALLOCINE_OUTPUT_PATH`

### Movies Database (📊)

Displays statistics from your Letterboxd ratings and watchlist:
- Total films rated, average rating, median runtime
- Genre and rating distribution charts
- Cache freshness report
- Raw data explorer

**Requires**: `MOVIES_OUTPUT_PATH`

### Watchlist Calendar (📅)

Inner-joins your watchlist with current showtimes to show which watchlist movies are screening at your configured theaters. Provides an interactive calendar view (requires `streamlit-calendar`) and a Google Calendar CSV export.

**Requires**: `MOVIES_OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH`

### Recommendations (🤖)

Chat interface powered by the [Hugging Face Inference API](https://huggingface.co/inference-api) (`Qwen/Qwen2.5-72B-Instruct`). Ask questions like:

- "Which watchlist movies are showing this weekend?"
- "Based on my taste, what should I prioritise?"
- "What's showing at Cinéma X that I'd enjoy?"

The page derives a taste profile from your Letterboxd ratings (top genres and directors by average rating) and sends only the matched watchlist-showtime rows to the model — no full parquets are transmitted.

#### Auto-adding theaters

If you mention a theater that isn't already tracked, the model automatically searches Allocine for matching Paris cinemas (via tool use). You'll see "Add" buttons for each match — clicking one appends the theater to your theaters CSV (`ALLOCINE_INPUT_PATH`) as `theater_id,theater_name,address`. Re-run the Allocine scraper afterwards to fetch its showtimes.

The page also backfills missing addresses for existing CSV entries on first load, using the Allocine API cache.

**Requires**: `MOVIES_OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH` + `ALLOCINE_INPUT_PATH` + `HF_API_KEY`

## Architecture

```
movies_management          Allocine-Showtimes-Scraping
        │                             │    ▲
        │  watchlist_with_letterboxd  │    │ theaters.csv (append)
        │  ratings_with_letterboxd    │  showtimes.parquet
        │  data_letterboxd            │
        └─────────────┬───────────────┘
                      │
              cinema_dashboard
       ┌───────┬──────┼─────────┬─────────────┐
  Showtimes  Database  Calendar  Recommendations
                                      │
                              Hugging Face API
                          (Qwen/Qwen2.5-72B-Instruct)
                                      │
                               utils/data_loader.py       ← cached parquet readers
                               utils/allocine_search.py   ← theater lookup
                               utils/theater_manager.py   ← CSV append
```

## Project structure

```
cinema_dashboard/
├── app.py                        # Streamlit entry point — registers all pages
├── orchestrate.py                # CLI to refresh all data (runs both scrapers in parallel)
├── pages/
│   ├── showtimes.py              # Showtimes page
│   ├── database.py               # Movies Database page
│   ├── calendar.py               # Watchlist Calendar page
│   └── recommendations.py        # Recommendations chat page (LLM + tool use)
├── utils/
│   ├── data_loader.py            # Cached parquet readers + watchlist↔showtimes join
│   ├── allocine_search.py        # Searches Paris theaters via the Allocine API
│   └── theater_manager.py        # Reads/appends to the theaters CSV
└── .env                          # Local environment variables (not committed)
```

All pages share `utils/data_loader.py` for parquet I/O and the watchlist↔showtimes join. Centralising the loaders means Streamlit's `@st.cache_data` keys on a single qualified function name, so each parquet is read once across all pages within the cache TTL — navigating between pages is a cache hit.

All pages are read-only with respect to parquet data. The only file the dashboard ever **writes** is the theaters CSV (`ALLOCINE_INPUT_PATH`), and only when the user explicitly confirms adding a theater via the Recommendations chat.

## Setup

### Requirements

- Python 3.11+
- The two companion repos cloned as siblings: `../movies_management/` and `../Allocine-Showtimes-Scraping/`

### Installation

```bash
python -m venv .venv
source .venv/bin/activate
make install
```

`make install` installs `requirements.txt` plus the dependencies of both companion projects.

### Configuration

Copy `.env.example` to `.env` and fill in the paths:

```bash
cp .env.example .env
```

| Variable | Description |
|----------|-------------|
| `MOVIES_OUTPUT_PATH` | Directory containing the three `*_letterboxd.parquet` files from `movies_management` |
| `ALLOCINE_OUTPUT_PATH` | Path to `showtimes.parquet` written by `Allocine-Showtimes-Scraping` |
| `ALLOCINE_INPUT_PATH` | Path to the theaters CSV read by `Allocine-Showtimes-Scraping` — also written to when adding a theater via the Recommendations chat |
| `HF_API_KEY` | Hugging Face API token (free) — required for the Recommendations page. Create one at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) |

### Running

```bash
streamlit run app.py
# or
make run
```

## Data refresh

Use `orchestrate.py` to refresh all data in one command. It runs both scrapers in parallel and only re-runs a scraper if its data is stale:

```bash
python orchestrate.py            # refresh stale data only
python orchestrate.py --force    # always re-run both scrapers
python orchestrate.py --days 7   # scrape 7 days of showtimes instead of 14
python orchestrate.py --reset    # pass --reset to Allocine scraper (clears tmp cache)
python orchestrate.py --reset-db # pass --reset_database to movies_management
```

**Staleness rules:**
- `showtimes.parquet` — stale if last written before the most recent Tuesday (French cinemas publish the new week's programme on Tuesdays)
- `watchlist_with_letterboxd.parquet` — stale if older than 7 days

Output is prefixed per scraper so parallel output stays readable:
```
[allocine]   Fetching Le Champo...
[letterboxd] Fetching watchlist for adjm...
[allocine]   Done.
```

You can also run each scraper manually:
```bash
cd ../movies_management && python main.py
cd ../Allocine-Showtimes-Scraping && python main.py
```

Streamlit cache TTL is **5 minutes**, shared across all pages (`DATA_TTL_SECONDS` in [`utils/data_loader.py`](utils/data_loader.py)). Conversation history on the Recommendations page is session-scoped and not affected by the cache.

## Troubleshooting

**"MOVIES_OUTPUT_PATH is not set"** — add it to `cinema_dashboard/.env`.

**"Watchlist data not found"** — run `python main.py` in `movies_management`.

**"Showtimes data not found"** — run `python main.py` in `Allocine-Showtimes-Scraping`.

**"No upcoming showtimes for your watchlist"** — either your watchlist is empty, no watchlist movies are currently showing, or the showtimes data is stale (re-run the scraper).

**`streamlit-calendar` not available** — the calendar page falls back to a table view. Install the package with `pip install streamlit-calendar`.

**"HF_API_KEY is not set"** — add your Hugging Face token to `cinema_dashboard/.env`.

**"No upcoming showtimes for your watchlist"** (Recommendations page) — either no watchlist movies are currently showing, or the showtimes data is stale. Re-run both scrapers to refresh.

## Known limitations

- Only covers Allocine (French cinemas). Other regions require a different showtimes source.
- Watchlist-to-showtimes matching is title-based (case-insensitive, with `original_title` fallback); edge cases like remakes may be missed.
- Data is only as fresh as the last scraper run.
