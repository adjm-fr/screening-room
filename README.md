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

### Home (🏠)

Lead-with-the-answer overview hub: a hero card for tonight's next watchlist screening, horizontal poster rails ("screening next on your watchlist", "available on streaming platforms", "because you like {top director}", discover by genre), and a small KPI strip at the bottom. Uses the cinema theme + Inter/Playfair editorial typography. Renders a designed empty state with CTA when no upcoming watchlist screenings exist.

The "available on streaming platforms" rail is drawn from the full watchlist (not the cinema join), sorted by Letterboxd rating, and filtered to the providers in `STREAMING_SERVICES` when set — when unset it falls back to any provider so the rail is still useful before subscriptions are configured.

When `STREAMING_SERVICES` is configured, every card also shows a small badge row indicating which of your subscribed streaming services currently carries the film in France (filled chip).

**Requires**: `MOVIES_OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH`

### Showtimes (🎟️)

Top chip-filter bar (theaters, genres, runtime buckets `<90` / `90–120` / `>120`, weekend toggle, free-text search) over three tabs:
- **By day** — horizontal poster rails grouped by date, with posters resolved via a left-join to the watchlist
- **Map** — pydeck map of theaters with marker size ∝ today's showtime count
- **Table** — raw dataframe with poster + Letterboxd link columns

**Requires**: `ALLOCINE_OUTPUT_PATH` (+ `MOVIES_OUTPUT_PATH` for posters, `ALLOCINE_INPUT_PATH` for the map)

### Movies Database (📊)

Three calmer tabs in place of the old chart wall:
- **Overview** — Genre × avg rating chart (rated films only) + micro-card insights (runtime distribution sparkline, top directors chip cloud, top themes chip cloud). A caption below the title clarifies the stats are based on your rated films count.
- **Discover** — chip filters (genre, director multiselect with live search, min-rating slider) over a poster rail of matching films
- **Tables** — raw dataframes with poster, IMDB, TMDB, and Letterboxd link columns. When `STREAMING_SERVICES` is set, a "Streaming on" column lists the subscribed services currently carrying each film.

**Requires**: `MOVIES_OUTPUT_PATH`

### Watchlist Calendar (📅)

Inner-joins your watchlist with current showtimes. Top chip-filter bar (theaters, genres, runtime buckets, weekend toggle, free-text search) + sidebar date range over three tabs:
- **By day** — horizontal poster rails grouped by date; one card per movie with all showtimes for that day listed below (time + theater), sorted by earliest showtime. When `STREAMING_SERVICES` is set, the rails split into **"Cinema-only this week"** (worth leaving the house for) followed by **"Also streaming on your services"** (you can stay in). The map and any aggregate counts still use the full set so pins aren't dropped.
- **Calendar** — ICS and CSV export for your filtered screenings (Google / Apple / Outlook compatible)
- **Map** — pydeck map of theaters with screenings in the current filter; marker size ∝ # screenings

**Requires**: `MOVIES_OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH` (+ `ALLOCINE_INPUT_PATH` for the map)

### Recommendations (🤖)

Chat interface powered by the [Hugging Face Inference API](https://huggingface.co/inference-api) (model configurable via `HF_MODEL`, defaults to `moonshotai/Kimi-K2-Instruct`). Ask questions like:

- "Which watchlist movies are showing this weekend?"
- "Based on my taste, what should I prioritise?"
- "What's showing at Cinéma X that I'd enjoy?"
- "What's on my streaming services tonight that fits my taste?" *(requires `STREAMING_SERVICES`)*

Power-user surface: prompt-suggestion chips, streaming spinner with transparent tool-call expanders, in-page pinned-recommendations column on the right (substring-match watchlist titles in each reply, then click to pin), Markdown conversation export.

The same assistant is reachable from any page via the global **`Cmd+K`** command palette (or the "✦ Ask AI" sidebar button). Both surfaces share `st.session_state['rec_messages']` so the conversation persists across them.

The page derives a taste profile from your Letterboxd ratings (top genres and directors by average rating) and sends only the matched watchlist-showtime rows to the model — no full parquets are transmitted. When the FR streaming-providers cache is populated, per-film flatrate availability is injected into the system prompt, and the model is rule-bound to only reference providers from that list (no hallucinated availability).

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
                          (moonshotai/Kimi-K2-Instruct)
                                      │
                               utils/data_loader.py       ← cached parquet readers
                               utils/allocine_search.py   ← theater lookup
                               utils/theater_manager.py   ← CSV append
```

## Project structure

```
cinema_dashboard/
├── app.py                        # Streamlit entry point — registers pages, injects CSS, mounts Cmd+K
├── orchestrate.py                # Lightweight CLI to refresh all data (consumes modules/scrapers.py)
├── .streamlit/
│   └── config.toml               # Cinema theme: dark + light, system-driven
├── assets/
│   └── styles.css                # Design tokens, movie cards, poster rails, chips, KPI cards, motion, focus rings, mobile media queries
├── modules/
│   ├── config.py                 # Centralised settings via pydantic-settings (BaseSettings)
│   └── scrapers.py               # Shared scraper command builders + staleness rules (single source of truth)
├── pipeline/                     # Dagster pipeline (alternative to orchestrate.py)
│   ├── assets.py                 # @asset definitions for showtimes + watchlist (consume modules/scrapers.py)
│   ├── resources.py              # ScraperConfig resource (ScraperConfig.from_settings)
│   └── definitions.py            # Dagster Definitions entry point
├── pages/
│   ├── 0_home.py                 # Home — hero "tonight" card, poster rails, KPI strip
│   ├── showtimes.py              # Showtimes page (chip filters, day rails, map, table)
│   ├── database.py               # Movies Database page (Overview / Discover / Tables)
│   ├── calendar.py               # Watchlist Calendar page (Calendar / Map / List, ICS export)
│   └── recommendations.py        # Recommendations chat page (calls utils/chat.render_chat)
├── utils/
│   ├── data_loader.py            # Cached parquet readers + watchlist↔showtimes join
│   ├── ui.py                     # Shared rendering helpers (movie cards, rails, hero card, KPIs, chips, ICS, runtime/rating formatting)
│   ├── geo.py                    # Theater geocoding (Nominatim + RateLimiter, cached parquet) + pydeck map renderer
│   ├── chat.py                   # Reusable HF chat assistant (build_chat_context + render_chat) shared by the page and Cmd+K dialog
│   ├── cmdk.py                   # Global Cmd+K command palette (st.dialog + streamlit-shortcuts)
│   ├── allocine_search.py        # Searches Paris theaters via the Allocine API
│   └── theater_manager.py        # Reads/appends to the theaters CSV
├── tests/
│   ├── conftest.py               # Shared fixtures + @st.cache_data no-op patch
│   └── evals/                    # LLM hallucination evals (opt-in via `-m evals`)
│       ├── goldens.py            # Bait prompts + allowed film/provider sets
│       ├── metrics.py            # FilmSetMembership + StreamingClaim DeepEval metrics
│       └── test_chat_evals.py    # Parameterized harness (hits live HF API)
└── .env                          # Local environment variables (not committed)
```

All pages share `utils/data_loader.py` for parquet I/O and the watchlist↔showtimes join. Centralising the loaders means Streamlit's `@st.cache_data` keys on a single qualified function name, so each parquet is read once across all pages within the cache TTL — navigating between pages is a cache hit.

All pages are read-only with respect to parquet data. The only file the dashboard ever **writes** is the theaters CSV (`ALLOCINE_INPUT_PATH`), and only when the user explicitly confirms adding a theater via the Recommendations chat.

## Setup

### Requirements

- Python 3.11+
- The two companion repos cloned as siblings: `../movies_management/` and `../Allocine-Showtimes-Scraping/`

### Installation

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv venv
source .venv/bin/activate
make install
```

Alternatively with pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ../movies_management
pip install -e ../Allocine-Showtimes-Scraping
```

`make install` installs dependencies from `pyproject.toml` for this project and both companion projects.

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
| `LETTERBOXD_USERNAME` | Your Letterboxd username — required for the orchestrator and Dagster pipeline |
| `LETTERBOXD_DAYS_TO_UPDATE` | Days before cached movie metadata is considered stale and refreshed (default: 365) |
| `HF_MODEL` | Hugging Face model ID for the Recommendations page (default: `moonshotai/Kimi-K2-Instruct`) |
| `HF_MAX_TOKENS` | Max tokens for model responses (default: 1024) |
| `HF_TEMPERATURE` | Sampling temperature; lower = more deterministic (default: 0.2) |
| `HF_TOP_P` | Nucleus sampling cutoff; lower = less creative drift (default: 0.8) |
| `TMDB_API_KEY` | *(optional)* TMDB v3 API key. Enables the FR streaming-availability cache (`data/streaming_providers.parquet`) refreshed by `orchestrate.py`. Free at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) |
| `STREAMING_SERVICES` | *(optional)* Comma-separated provider slugs you subscribe to (e.g. `mubi,netflix,canalplus,arte`). Enables streaming badges on movie cards (Home, Calendar), the Calendar cinema-only / also-streaming partition, the Database "Streaming on" column, and the Recommendations chat's awareness of FR availability. When unset, every streaming surface silently no-ops. |
| `ALLOCINE_DIR` | *(optional)* Absolute path to the `Allocine-Showtimes-Scraping` repo. Defaults to `../Allocine-Showtimes-Scraping` relative to this repo. |
| `MOVIES_DIR` | *(optional)* Absolute path to the `movies_management` repo. Defaults to `../movies_management` relative to this repo. |

### Running

```bash
streamlit run app.py
# or
make run
```

## Data refresh

### Option 1 — CLI (lightweight)

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

After the Allocine scrape succeeds, the orchestrator automatically runs a third step that expands `data_letterboxd.parquet` with Letterboxd metadata for every film found in the fresh `showtimes.parquet` — not only the user's watchlist and ratings. Films that cannot be resolved to a Letterboxd slug are written to `{MOVIES_OUTPUT_PATH}/unresolved_allocine.parquet`.

Output is timestamped and labelled per scraper:
```
2026-05-04 13:00:00 [INFO] [allocine] Fetching Le Champo...
2026-05-04 13:00:01 [INFO] [letterboxd] Fetching watchlist for adjm...
2026-05-04 13:01:30 [INFO] [allocine] Done.
2026-05-04 13:01:31 [INFO] [enrich] Enriching Letterboxd cache from showtimes...
2026-05-04 13:03:00 [INFO] [enrich] Done.
```

### Option 2 — Dagster UI

The `pipeline/` folder contains a Dagster pipeline with the same two scrapers as software-defined assets, manual jobs, and automatic cron-based materialisation.

```bash
pip install dagster dagster-webserver   # first time only
dagster dev -m pipeline.definitions    # opens UI at localhost:3000
```

Three jobs are available in the UI:
- `showtimes_job` — runs the Allocine scraper
- `watchlist_job` — runs the Letterboxd scraper
- `all_scrapers_job` — runs all three assets (showtimes, cache enrichment, watchlist)

Assets are also configured with `AutomationCondition` for automatic scheduling (showtimes: Tuesday 06:00, watchlist: Monday 06:00) when the Dagster daemon is running. The `letterboxd_cache_enriched` asset has `deps=["showtimes"]` and runs automatically after each showtimes materialisation.

You can also run each scraper manually:
```bash
cd ../movies_management && python main.py
cd ../Allocine-Showtimes-Scraping && python main.py
```

Streamlit cache TTL is **5 minutes**, shared across all pages (`DATA_TTL_SECONDS` in [`utils/data_loader.py`](utils/data_loader.py)). Conversation history on the Recommendations page is session-scoped and not affected by the cache.

## LLM evals

The Recommendations chat is rule-bound to only reference watchlist titles and FR streaming providers from the lists injected into its system prompt. To verify that the live model actually respects those rules, `tests/evals/` ships a small DeepEval-based regression suite of bait prompts (e.g. *"Recommend me Oppenheimer for tonight."*, *"Is Parasite on Disney+?"*). Two deterministic metrics flag violations:

- **`FilmSetMembershipMetric`** — fails if the output names a film outside the allowed set.
- **`StreamingClaimMetric`** — fails if the output ties a film to a provider not in the allowed `(film, provider)` set.

The suite is deselected from the default `pytest` run (every file is tagged `pytest.mark.evals` and `pyproject.toml` uses `addopts = "-m 'not evals'"`) because each case hits the live Hugging Face Inference API.

```bash
uv run pytest tests/evals/ -m evals                          # full suite
uv run pytest tests/evals/ -m evals -k outside_film_bait     # one golden
```

Requires `HF_API_KEY`; the suite skips itself when unset. To add a new failure mode, append a `Golden(...)` entry to `tests/evals/goldens.py` — keep the dataset tight and curated rather than sprawling.

## Troubleshooting

**"MOVIES_OUTPUT_PATH is not set"** — add it to `cinema_dashboard/.env`.

**"Watchlist data not found"** — run `python main.py` in `movies_management`.

**"Showtimes data not found"** — run `python main.py` in `Allocine-Showtimes-Scraping`.

**"No upcoming showtimes for your watchlist"** — either your watchlist is empty, no watchlist movies are currently showing, or the showtimes data is stale (re-run the scraper).

**`streamlit-calendar` not available** — the calendar page falls back to a table view. Install the package with `pip install streamlit-calendar`.

**Map shows no theaters** — addresses are geocoded once via Nominatim (rate-limited, free) and cached to `data/theaters_geo.parquet`. To force re-geocoding, delete the parquet. Theaters whose addresses Nominatim can't resolve are kept in tables but skipped on the map.

**`Cmd+K` doesn't open the assistant** — the keyboard shortcut depends on `streamlit-shortcuts`; if it's missing or the binding fails on your browser, the "✦ Ask AI" button in the sidebar opens the same dialog.

**Theme looks broken / fonts not loading** — `assets/styles.css` imports Inter and Playfair Display from Google Fonts. Browsers without internet access render the dashboard with system fallbacks; the layout still works.

**"HF_API_KEY is not set"** — add your Hugging Face token to `cinema_dashboard/.env`.

**"No upcoming showtimes for your watchlist"** (Recommendations page) — either no watchlist movies are currently showing, or the showtimes data is stale. Re-run both scrapers to refresh.

## Known limitations

- Only covers Allocine (French cinemas). Other regions require a different showtimes source.
- Watchlist-to-showtimes matching joins on the normalised TMDB French title (`french_title`) vs Allocine's French display title, confirmed by director overlap. Films whose French title differs significantly between the two sources may not be matched.
- Data is only as fresh as the last scraper run.
