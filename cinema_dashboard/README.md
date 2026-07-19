# Cinema Dashboard

A Streamlit dashboard that merges Letterboxd watchlist data with French cinema showtimes.

> **Part of the [`screening-room`](../README.md) workspace.** Install and run from the workspace root —
> see the root README for setup. Commands below assume you're at the workspace root and use
> `uv run --no-sync --directory cinema_dashboard …` to target this member.

## Overview

Cinema Dashboard is the orchestration layer for a three-project pipeline:

| Project | Role |
|---------|------|
| `movies_management` | Fetches and caches Letterboxd ratings + watchlist as parquet files |
| `Allocine-Showtimes-Scraping` | Scrapes French cinema showtimes to `showtimes.parquet` |
| `cinema_dashboard` *(this member)* | Reads both parquets and visualises the combined data |

The dashboard is mostly read-only — it reads parquet files written by the other two projects. The one exception is the Recommendations page, which can append new theaters to the theaters CSV (`ALLOCINE_INPUT_PATH`) when the user confirms adding one via the chat.

## Pages

### Home (🏠)

Lead-with-the-answer overview hub: a hero card for tonight's next watchlist screening, horizontal poster rails ("screening next on your watchlist", "available on streaming platforms", "top matches this week"), and a small KPI strip at the bottom. Uses the cinema theme + Inter/Playfair editorial typography. Renders a designed empty state with CTA when no upcoming watchlist screenings exist.

The "top matches this week" rail ranks this week's watchlist screenings against a taste profile induced from your ratings history (`utils/taste.py`): each rated director, genre, theme, actor, country, language, and decade gets a signed, shrunk affinity centered on *your* average rating; candidate films blend those affinities through fixed weights plus a small Letterboxd-rating prior, mapped to a stable 0–100 match value. Cards show a "◎ {n}% match" badge (amber heatmap) and up to two "✓ because" chips naming the strongest positive contributors.

The "available on streaming platforms" rail is drawn from the full watchlist (not the cinema join), ranked by taste match (Letterboxd rating as tie-break, and as fallback before any films are rated). A film counts as "available" when it's on a subscribed provider in `STREAMING_SERVICES` (or on any provider when that's unset) **or** on a no-cost provider (Arte.tv, France.tv, …) — free platforms always count, regardless of `STREAMING_SERVICES`.

Every card shows a small badge row: subscribed services carrying the film (filled `chip--streaming`) when `STREAMING_SERVICES` is configured, plus a distinct dashed `chip--streaming-free` badge — labelled with the word "free" so the distinction isn't color-only — for any no-cost provider, unconditionally.

**Requires**: `OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH`

### Movies Database (📊)

Three calmer tabs in place of the old chart wall:
- **Overview** — Genre × avg rating chart (rated films only) + micro-card insights (runtime distribution sparkline, top directors chip cloud, top themes chip cloud). A caption below the title clarifies the stats are based on your rated films count.
- **Discover** — chip filters (genre, director multiselect with live search, min-rating slider) over a poster rail of matching films. Each card shows your own star rating as a green chip (Letterboxd convention) next to the amber Letterboxd community average; both ratings are on the same 0–5 scale.
- **Tables** — raw dataframes with poster, IMDB, TMDB, and Letterboxd link columns. A "Streaming on" column lists, per film, the subscribed services currently carrying it (when `STREAMING_SERVICES` is set) plus every no-cost provider suffixed `(free)` (e.g. `netflix, arte-tv (free)`) — free platforms always show, subscription-gated ones don't.

**Requires**: `OUTPUT_PATH`

### Watchlist Showtimes (📅)

Inner-joins your watchlist with current showtimes. The join matches Allocine's display title against both normalized watchlist titles — the TMDB French retitle *and* the original title, since repertory screenings often run in VO (*Sudden Fear* screens as such even though TMDB calls it *Le Masque arraché*) — and then **confirms each match by director**, so a recurring or remade title (e.g. *Nosferatu*) can't attach the wrong film's screenings. Director confirmation uses token-subset containment — one director name's tokens being wholly contained in the other's — so name-form drift between Allocine and TMDB (`Kirk Jones (II)` vs `Kirk Jones`, `Akinola Davies` vs `Akinola Davies Jr.`, `Ringo Lam` vs `Ringo Lam Ling-Tung`) still matches while genuinely different directors are still rejected. The page top carries a single control — the **"Only times I'm free"** toggle — while every other filter (date range, theater multi-select dropdown, runtime buckets, "showtime between" time-of-day range slider, free-text search, min rating) lives in the sidebar. The theater options stay hidden inside the dropdown: an empty selection means *all theaters*.

The free-time toggle (which replaced the old weekend toggle) narrows to screenings you can actually attend: weekends, French public holidays (via the `holidays` library), days you mark off, or weekdays at/after an editable cutoff (default 19:00). Turning it on reveals the cutoff time picker plus two date multi-selects over the upcoming showtime dates — **Days off (free all day)**, which includes that day's daytime screenings, and **Unavailable (away)**, which excludes the whole day and overrides everything else (even a weekend or holiday). The three tabs:
- **By day** — horizontal poster rails grouped by date under **"Cinema-only this week"**; one card per movie with all showtimes for that day listed below (time + theater), sorted by earliest showtime. Streaming availability isn't shown here — see the dedicated Streaming page.
- **Calendar** — ICS and CSV export for your filtered screenings (Google / Apple / Outlook compatible); the export always reflects every filter applied above, including the time-of-day range and the free-time toggle
- **Map** — pydeck map of theaters with screenings in the current filter; marker size ∝ # screenings

**Requires**: `OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH` (+ `ALLOCINE_INPUT_PATH` for the map)

### Streaming (📺)

One horizontal poster rail per FR streaming provider, populated from the TMDB watch-providers cache. Films are taken from your full watchlist (not only those with upcoming showtimes), sorted by Letterboxd average rating per rail. A multi-select chip filter at the top (with an inclusive *All* sentinel) lets you focus on one or more providers using human-readable provider names (e.g. *Canal+*, *MUBI*). The slug → pretty-name map is persisted at `assets/provider_display_names.json` and auto-grows every time `orchestrate.py` refreshes the cache and TMDB returns a new provider.

Rails cover two kinds of availability: subscription (`flatrate`) providers, limited to your `STREAMING_SERVICES` when set (every flatrate provider TMDB returns when it's unset), and no-cost `free` providers (e.g. Arte.tv, France.tv) — free platforms always get a rail, regardless of `STREAMING_SERVICES`, since they're watchable by everyone. The chip filter operates over the union of both. The page is explicitly FR-scoped — availability comes from TMDB's France region, and only `flatrate`/`free` are tracked (rent/buy/ads listings are intentionally not surfaced).

**Requires**: `OUTPUT_PATH` (+ `TMDB_API_KEY` set when running `orchestrate.py` so the cache is populated)

### Recommendations (🤖)

Chat interface powered by the [Gemini API](https://ai.google.dev/) via the native `google-genai` SDK (model configurable via `GEMINI_MODEL`, defaults to `gemini-3.1-flash-lite`). Ask questions like:

- "Which watchlist movies are showing this weekend?"
- "Based on my taste, what should I prioritise?"
- "What's showing at Cinéma X that I'd enjoy?"
- "What's on my streaming services tonight that fits my taste?" *(requires `STREAMING_SERVICES`)*

Power-user surface: prompt-suggestion chips, streaming spinner with transparent tool-call expanders, in-page pinned-recommendations column on the right (substring-match watchlist titles in each reply, then click to pin), Markdown conversation export.

The same assistant is reachable from any page via the global **`Cmd+K`** command palette (or the "✦ Ask AI" sidebar button). Both surfaces share a single `st.session_state['chat']` (a `ChatState` dataclass) so the conversation persists across them. The transcript and pinned recommendations are also persisted to `data/chat_state.json` (gitignored, beside the streaming/geo caches) and reloaded on the next launch, so they survive app restarts — saved after each assistant reply and pin change; **🗑 Clear conversation** deletes the file. A corrupt or missing file falls back to a fresh conversation.

The page derives a taste profile from your Letterboxd ratings (favourite *and least favourite* genres, themes, directors, plus favourite actors and eras, ranked by the signed affinities in `utils/taste.py`) and sends only the matched watchlist-showtime rows to the model — no full parquets are transmitted. Because ratings follow a tier ladder rather than a conventional satisfaction scale (2.5–3/5 already means a good film, 3.5+/5 a must-watch), the profile carries a `Rating scale:` legend line and the system prompt is reminded not to read the ~2.5/5 average as dissatisfaction. When the FR streaming-providers cache is populated, per-film availability is injected into the system prompt as `flatrate={a, b}` (subscription providers) plus, when the film also has one, `; free={c}` (no-cost providers) — and the model is rule-bound to only reference providers from those lists (no hallucinated availability).

#### Auto-adding theaters

If you mention a theater that isn't already tracked, the model automatically searches Allocine for matching Paris cinemas (via tool use). You'll see "Add" buttons for each match — clicking one appends the theater to your theaters CSV (`ALLOCINE_INPUT_PATH`) as `theater_id,theater_name,address`. The next `orchestrate.py` run detects the changed CSV and re-scrapes Allocine automatically (no `--force` needed) to fetch the new theater's showtimes.

The page also backfills missing addresses for existing CSV entries on first load, using the Allocine API cache.

**Requires**: `OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH` + `ALLOCINE_INPUT_PATH` + `GEMINI_API_KEY`

## Architecture

```
movies_management          Allocine-Showtimes-Scraping
        │                             │    ▲
        │  watchlist_with_letterboxd  │    │ theaters.csv (append)
        │  ratings_with_letterboxd    │  showtimes.parquet
        │  data_letterboxd            │
        └─────────────┬───────────────┘
                      │      + TMDB watch-providers FR (in-process refresh)
                      │        → data/streaming_providers.parquet
                      │
              cinema_dashboard
   ┌──────┬──────────┬──────┴──────────────┬───────────┬─────────────────┐
  Home  Database  Watchlist Showtimes  Streaming  Recommendations
                                                         │
                                                  Gemini API
                                             (google-genai SDK)
                                                         │
                               utils/data_loader.py       ← cached parquet readers
                               utils/streaming.py         ← TMDB FR providers cache
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
│   ├── styles.css                # Design tokens, movie cards, poster rails, chips, KPI cards, motion, focus rings, mobile media queries
│   └── provider_display_names.json  # Slug → pretty-name catalogue (auto-grown by refresh_streaming_providers)
├── modules/
│   ├── config.py                 # Centralised settings via pydantic-settings (BaseSettings)
│   └── scrapers.py               # Shared scraper command builders + staleness rules (single source of truth)
├── pipeline/                     # Dagster pipeline (alternative to orchestrate.py)
│   ├── assets.py                 # @asset definitions for showtimes + watchlist (consume modules/scrapers.py)
│   ├── resources.py              # ScraperConfig resource (ScraperConfig.from_settings)
│   └── definitions.py            # Dagster Definitions entry point
├── pages/
│   ├── 0_home.py                 # Home — hero "tonight" card, poster rails, KPI strip
│   ├── database.py               # Movies Database page (Overview / Discover / Tables)
│   ├── calendar.py               # Watchlist Showtimes page (theater dropdown, runtime/time-of-day/free-time/search filters, day rails, map, ICS export)
│   ├── streaming.py              # Streaming page — one poster rail per FR provider
│   └── recommendations.py        # Recommendations chat page (calls utils/chat.render_chat)
├── utils/
│   ├── data_loader.py            # Cached parquet readers + watchlist↔showtimes join + attach_streaming
│   ├── taste.py                  # Taste ranker — affinity profile, 0–100 match scorer, "because" explanations
│   ├── streaming.py              # TMDB FR watch-providers cache + display-name catalogue loader/updater
│   ├── ui.py                     # Shared rendering helpers (movie cards, rails, hero card, KPIs, chips, ICS, runtime/rating formatting)
│   ├── availability.py           # Free-time mask (weekend/holiday/day-off/after-cutoff, minus unavailable days)
│   ├── geo.py                    # Theater geocoding (Nominatim + RateLimiter, cached parquet) + pydeck map renderer
│   ├── chat.py                   # Reusable Gemini chat assistant (build_chat_context + render_chat) shared by the page and Cmd+K dialog
│   ├── cmdk.py                   # Global Cmd+K command palette (st.dialog + streamlit-shortcuts)
│   ├── allocine_search.py        # Searches Paris theaters via the Allocine API
│   └── theater_manager.py        # Reads/appends to the theaters CSV
├── tests/
│   ├── conftest.py               # Shared fixtures + @st.cache_data no-op patch
│   ├── test_*.py                 # Unit tests for data_loader, taste, ui, chat, streaming, database, geo, scrapers, config, allocine_search
│   └── evals/                    # LLM hallucination evals (opt-in via `-m evals`)
│       ├── goldens.py            # Bait prompts + allowed film/provider sets
│       ├── metrics.py            # FilmSetMembership + StreamingClaim DeepEval metrics
│       ├── test_metrics.py       # Unit tests for the metric regex (no Gemini calls)
│       └── test_chat_evals.py    # Parameterized harness (hits live Gemini API)
```

> Environment variables live in a single shared `.env` at the **workspace root**, not in this folder. See [Configuration](#configuration).

All pages share `utils/data_loader.py` for parquet I/O and the watchlist↔showtimes join. Centralising the loaders means Streamlit's `@st.cache_data` keys on a single qualified function name, so each parquet is read once across all pages within the cache TTL — navigating between pages is a cache hit.

All pages are read-only with respect to parquet data. The only file the dashboard ever **writes** is the theaters CSV (`ALLOCINE_INPUT_PATH`), and only when the user explicitly confirms adding a theater via the Recommendations chat.

## Setup

Setup is workspace-wide — install the whole workspace once from the **workspace root** rather than this
folder. See the [root README](../README.md) for details. In short:

```bash
uv sync --all-packages   # one shared .venv for every member (run at the workspace root)
```

The dashboard reaches its data sources through the workspace: `movies_management` is a sibling member,
and the standalone `Allocine-Showtimes-Scraping` repo is located via the `ALLOCINE_DIR` env var (default:
a sibling of the workspace root).

### Configuration

All members share one `.env` at the **workspace root** (`cp .env.example .env` there). Each member reads
only the keys it declares. The keys this member uses:

| Variable | Description |
|----------|-------------|
| `OUTPUT_PATH` | Directory containing the three `*_letterboxd.parquet` files from `movies_management` |
| `ALLOCINE_OUTPUT_PATH` | Path to `showtimes.parquet` written by `Allocine-Showtimes-Scraping` |
| `ALLOCINE_INPUT_PATH` | Path to the theaters CSV read by `Allocine-Showtimes-Scraping` — also written to when adding a theater via the Recommendations chat |
| `GEMINI_API_KEY` | Gemini API key (free tier: 15 RPM, 250K TPM) — required for the Recommendations page. Create one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `LETTERBOXD_USERNAME` | Your Letterboxd username — required for the orchestrator and Dagster pipeline |
| `LETTERBOXD_DAYS_TO_UPDATE` | Days before cached movie metadata is considered stale and refreshed (default: 365) |
| `GEMINI_MODEL` | Gemini model ID for the Recommendations page (default: `gemini-3.1-flash-lite`) |
| `GEMINI_MAX_TOKENS` | Max output tokens for model responses (default: 1024) |
| `GEMINI_TEMPERATURE` | Sampling temperature; lower = more deterministic (default: 0.2) |
| `GEMINI_TOP_P` | Nucleus sampling cutoff; lower = less creative drift (default: 0.8) |
| `TMDB_API_KEY` | *(optional)* TMDB v3 API key. Enables the FR streaming-availability cache (`data/streaming_providers.parquet`, both `flatrate` and no-cost `free` providers) refreshed by `orchestrate.py`. Free at [themoviedb.org/settings/api](https://www.themoviedb.org/settings/api) |
| `STREAMING_SERVICES` | *(optional)* Comma-separated **subscription** provider slugs you pay for (e.g. `mubi,netflix,canalplus`). Gates which `flatrate` providers show as streaming badges on the Home page's movie cards, the Database "Streaming on" column, and the Recommendations chat's awareness of FR availability. No-cost providers (Arte.tv, France.tv, …) always surface regardless of this setting — they're watchable by everyone. When unset, flatrate surfaces fall back to "any provider"; free surfaces are unaffected either way. |
| `ALLOCINE_DIR` | *(optional)* Absolute path to the `Allocine-Showtimes-Scraping` repo. Defaults to `../Allocine-Showtimes-Scraping` relative to this repo. |
| `MOVIES_DIR` | *(optional)* Absolute path to the `movies_management` repo. Defaults to `../movies_management` relative to this repo. |

### Running

From the workspace root:

```bash
uv run --no-sync --directory cinema_dashboard streamlit run app.py
```

## Data refresh

### Option 1 — CLI (lightweight)

Use `orchestrate.py` to refresh all data in one command. It runs both scrapers in parallel and only re-runs a scraper if its data is stale (run from the workspace root):

```bash
uv run --no-sync --directory cinema_dashboard python orchestrate.py            # refresh stale data only
uv run --no-sync --directory cinema_dashboard python orchestrate.py --force    # always re-run both scrapers
uv run --no-sync --directory cinema_dashboard python orchestrate.py --days 7   # scrape 7 days of showtimes instead of 14
uv run --no-sync --directory cinema_dashboard python orchestrate.py --reset    # pass --reset to Allocine scraper (clears tmp cache)
uv run --no-sync --directory cinema_dashboard python orchestrate.py --reset-db # pass --reset_database to movies_management
```

**Staleness rules:**
- `showtimes.parquet` — stale if last written before the most recent Tuesday (French cinemas publish the new week's programme on Tuesdays), **or** if the theaters CSV (`ALLOCINE_INPUT_PATH`) has been modified since the parquet was last written (a theater was added/removed, so the showtimes no longer cover the current set). Adding a theater via the Recommendations chat therefore triggers a re-scrape on the next run, even mid-week.
- `watchlist_with_letterboxd.parquet` — stale if older than 7 days

After the Allocine scrape succeeds, the orchestrator automatically runs a third step that expands `data_letterboxd.parquet` with Letterboxd metadata for every film found in the fresh `showtimes.parquet` — not only the user's watchlist and ratings. Films that cannot be resolved to a Letterboxd slug are written to `{OUTPUT_PATH}/unresolved_allocine.parquet`.

Output is timestamped and labelled per scraper:
```
2026-05-04 13:00:00 [INFO] [allocine] Fetching Le Champo...
2026-05-04 13:00:01 [INFO] [letterboxd] Fetching watchlist for adjm...
2026-05-04 13:01:30 [INFO] [allocine] Done.
2026-05-04 13:01:31 [INFO] [enrich] Enriching Letterboxd cache from showtimes...
2026-05-04 13:03:00 [INFO] [enrich] Done.
```

### Option 2 — Dagster UI

The `pipeline/` folder contains a Dagster pipeline with the same two scrapers as software-defined assets, manual jobs, and automatic cron-based materialisation. `dagster` and `dagster-webserver` ship as dashboard dependencies, so `uv sync --all-packages` already installs them — no extra install step. Launch the UI from the workspace root:

```bash
uv run --no-sync --directory cinema_dashboard dagster dev -m pipeline.definitions   # opens UI at localhost:3000
```

Three jobs are available in the UI:
- `showtimes_job` — runs the Allocine scraper
- `watchlist_job` — runs the Letterboxd scraper
- `all_scrapers_job` — runs all three assets (showtimes, cache enrichment, watchlist)

Assets are also configured with `AutomationCondition` for automatic scheduling (showtimes: Tuesday 06:00, watchlist: Monday 06:00) when the Dagster daemon is running. The `letterboxd_cache_enriched` asset has `deps=["showtimes"]` and runs automatically after each showtimes materialisation.

You can also run each scraper manually. `movies_management` is a workspace member; the Allocine scraper is the standalone sibling repo (located via `ALLOCINE_DIR`):
```bash
uv run --no-sync --directory movies_management python main.py --username <letterboxd-user>
uv run --directory ../Allocine-Showtimes-Scraping python main.py
```

Streamlit cache TTL is **5 minutes**, shared across all pages (`DATA_TTL_SECONDS` in [`utils/data_loader.py`](utils/data_loader.py)). Conversation history on the Recommendations page is session-scoped and not affected by the cache.

## LLM evals

The Recommendations chat is rule-bound to only reference watchlist titles and FR streaming providers from the lists injected into its system prompt. To verify that the live model actually respects those rules, `tests/evals/` ships a small DeepEval-based regression suite of bait prompts (e.g. *"Recommend me Oppenheimer for tonight."*, *"Is Parasite on Disney+?"*, *"Surprise me with a Bong Joon-ho-style movie"*). Two deterministic metrics flag violations:

- **`FilmSetMembershipMetric`** — fails if the output names a film outside the allowed set.
- **`StreamingClaimMetric`** — fails if the output ties a film to a provider not in the allowed `(film, provider)` set. The post-mention scan window is truncated at the next allowed-film mention so providers attributed to a later film in the same sentence don't falsely pin onto the current one.

Both metrics ignore mentions inside a **refusal context** (*"I can't recommend Oppenheimer"*, *"Past Lives isn't on Netflix"*) so a principled denial doesn't count as a hallucination. The refusal logic is regex-based and unit-tested separately in `tests/evals/test_metrics.py`, which runs in the default `pytest` suite and does **not** hit the Gemini API.

The system prompt also enforces a **refusal flow**: when the user asks about a film, director, or provider not in the provided lists, the model must respond in 1–2 sentences, acknowledge it isn't in the watchlist/streaming list, and **ask** whether the user wants a recommendation from what is available — without auto-dumping the watchlist.

The suite is deselected from the default `pytest` run (every file is tagged `pytest.mark.evals` and `pyproject.toml` uses `addopts = "-m 'not evals'"`) because each case hits the live Gemini API.

```bash
uv run --no-sync --directory cinema_dashboard pytest tests/evals/ -m evals                       # full suite
uv run --no-sync --directory cinema_dashboard pytest tests/evals/ -m evals -k outside_film_bait  # one golden
```

Requires `GEMINI_API_KEY`; the suite skips itself when unset. To add a new failure mode, append a `Golden(...)` entry to `tests/evals/goldens.py` — keep the dataset tight and curated rather than sprawling.

## Troubleshooting

**"OUTPUT_PATH is not set"** — add it to the workspace-root `.env`.

**"Watchlist data not found"** — run `uv run --no-sync --directory movies_management python main.py --username <user>` from the workspace root.

**"Showtimes data not found"** — run the Allocine scraper: `uv run --directory ../Allocine-Showtimes-Scraping python main.py`.

**"No upcoming showtimes for your watchlist"** — either your watchlist is empty, no watchlist movies are currently showing, or the showtimes data is stale (re-run the scraper).

**Map shows no theaters** — addresses are geocoded once via Nominatim (rate-limited, free) and cached to `data/theaters_geo.parquet`. To force re-geocoding, delete the parquet. Theaters whose addresses Nominatim can't resolve are kept in tables but skipped on the map.

**`Cmd+K` doesn't open the assistant** — the keyboard shortcut depends on `streamlit-shortcuts`; if it's missing or the binding fails on your browser, the "✦ Ask AI" button in the sidebar opens the same dialog.

**Theme looks broken / fonts not loading** — `assets/styles.css` imports Inter and Playfair Display from Google Fonts. Browsers without internet access render the dashboard with system fallbacks; the layout still works.

**"GEMINI_API_KEY is not set"** — add your Gemini API key to the workspace-root `.env`. Create one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

**"No upcoming showtimes for your watchlist"** (Recommendations page) — either no watchlist movies are currently showing, or the showtimes data is stale. Re-run both scrapers to refresh.

## Known limitations

- Only covers Allocine (French cinemas). Other regions require a different showtimes source.
- Watchlist-to-showtimes matching joins Allocine's display title against the normalised TMDB French title (`french_title`) *and* the original title, **confirmed by director overlap**. A title match is kept only when both sources agree on at least one director, so films whose Allocine display title matches neither watchlist title — or whose director metadata is missing on either side — may not be matched. This is a deliberate precision-first trade-off: showing a wrong film's screening (e.g. the wrong "Nosferatu") is worse than missing one.
- Data is only as fresh as the last scraper run.
