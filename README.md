# Cinema Dashboard

A Streamlit dashboard that merges Letterboxd watchlist data with French cinema showtimes.

## Overview

Cinema Dashboard is the orchestration layer for a three-project pipeline:

| Project | Role |
|---------|------|
| `movies_management` | Fetches and caches Letterboxd ratings + watchlist as parquet files |
| `Allocine-Showtimes-Scraping` | Scrapes French cinema showtimes to `showtimes.parquet` |
| `cinema_dashboard` *(this repo)* | Reads both parquets and visualises the combined data |

The dashboard **never produces data** — it only reads parquet files written by the other two projects.

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

Inner-joins your watchlist with current showtimes to show which watchlist movies are screening near you. Provides an interactive calendar view (requires `streamlit-calendar`) and a Google Calendar CSV export.

**Requires**: `MOVIES_OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH`

## Architecture

```
movies_management          Allocine-Showtimes-Scraping
        │                             │
        │  watchlist_with_letterboxd  │  showtimes.parquet
        │  ratings_with_letterboxd    │
        │  data_letterboxd            │
        └─────────────┬───────────────┘
                      │
              cinema_dashboard
           ┌──────────┼──────────┐
        Showtimes  Database  Calendar
```

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

### Running

```bash
streamlit run app.py
# or
make run
```

## Data refresh

The dashboard reads cached parquet files. To refresh:

```bash
# Letterboxd data
cd ../movies_management && python main.py

# Showtimes
cd ../Allocine-Showtimes-Scraping && python main.py
```

Cache TTLs (configurable in page files):
- Showtimes: 5 minutes
- Movies Database: 5 minutes
- Watchlist Calendar: 2 minutes

## Troubleshooting

**"MOVIES_OUTPUT_PATH is not set"** — add it to `cinema_dashboard/.env`.

**"Watchlist data not found"** — run `python main.py` in `movies_management`.

**"Showtimes data not found"** — run `python main.py` in `Allocine-Showtimes-Scraping`.

**"No upcoming showtimes for your watchlist"** — either your watchlist is empty, no watchlist movies are currently showing, or the showtimes data is stale (re-run the scraper).

**`streamlit-calendar` not available** — the calendar page falls back to a table view. Install the package with `pip install streamlit-calendar`.

## Known limitations

- Only covers Allocine (French cinemas). Other regions require a different showtimes source.
- Watchlist-to-showtimes matching is title-based (case-insensitive, with `original_title` fallback); edge cases like remakes may be missed.
- Data is only as fresh as the last scraper run.

## License

[Specify your license here]
