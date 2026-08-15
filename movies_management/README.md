# Letterboxd Movie Management System

A Python application that aggregates and enriches your Letterboxd movie data by combining user ratings and watchlist information with comprehensive movie metadata from the Letterboxd API.

> **Part of the [`screening-room`](../README.md) workspace.** Install and run from the workspace root —
> see the root README for setup. Commands below assume you're at the workspace root and use
> `uv run --no-sync --directory movies_management …` to target this member.

## Overview

This system addresses the limitation that Letterboxd's API doesn't provide complete movie metadata in its user endpoints. The application:

1. **Fetches** your Letterboxd films and watchlist using your username
2. **Builds** a persistent cache of movie metadata (ratings, genres, directors, runtime, descriptions, etc.)
3. **Maintains** the cache by selectively refreshing aged entries
4. **Exports** enriched datasets combining your personal data with complete metadata

All data is stored locally in parquet format for efficient storage and analysis.

## Features

- 🎬 **Automatic metadata enrichment** - Combines user data with comprehensive Letterboxd movie information
- ⚡ **Intelligent caching** - Stores metadata locally to minimize API calls and improve performance
- 🔄 **Smart refresh strategy** - Automatically updates movie data older than a configurable threshold
- 📊 **Rich data extraction** - Captures genres, themes, crew roles, studios, territories, languages, and more
- 🧵 **Parallel processing** - Uses thread pools for concurrent API requests
- 📋 **Dual outputs** - Separate enriched files for ratings and watchlist data
- ✅ **Data validation** - Detects and prevents duplicate entries
- 🎨 **Media assets** - Includes poster and banner URLs for visual applications

## Installation

This member is installed as part of the workspace — there's no per-member install. From the
**workspace root** (requires Python 3.13+ and [`uv`](https://docs.astral.sh/uv/)):

```bash
uv sync --all-packages   # one shared .venv for every member
```

See the [root README](../README.md) for full workspace setup.

### Dependencies

- **pandas** - Data manipulation and parquet I/O
- **click** - Command-line interface
- **pydantic** - Validates TMDB API responses (`modules/tmdb.py`) so a payload shape change is caught, not silently swallowed
- **pydantic-settings** - Typed, validated environment variable management (auto-loads `.env`)
- **letterboxdpy** - Letterboxd API client
- **httpx** - Async HTTP client for TMDB enrichment
- **tenacity** - Retry/backoff for transient API failures

See `pyproject.toml` for pinned versions.

## Configuration

All members share one `.env` at the **workspace root** (copy `.env.example` to `.env` there). The keys this
member reads:

```env
# Required
OUTPUT_PATH=/path/to/output/directory

# Optional (default: 365)
LETTERBOXD_DAYS_TO_UPDATE=365
```

### Configuration Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OUTPUT_PATH` | Yes | — | Directory path where parquet files will be saved |
| `LETTERBOXD_DAYS_TO_UPDATE` | No | `365` | Number of days before cached movie metadata is refreshed |
| `LETTERBOXD_REFRESH_LIMIT` | No | `1000` | Max stale movies to refresh per run (raise to lift the cap) |
| `TMDB_API_KEY` | **Yes** | — | TMDB is the sole producer of `french_title`, `cast`, the crew columns (`directors`/`producers`/`writers`/`composers`), `trailer_url`, and — since the territories+genres migration completed — `studio`/`country`/`origin_country`/`language`/`original_language`/`genres`/`keywords`. `Settings` fails fast at startup (`pydantic.ValidationError`) without it; a null `directors` would otherwise gut the taste ranker's highest-weighted dimension *and* the dashboard's showtimes matching |

## Usage

### Basic Run

Run the full pipeline (from the workspace root):

```bash
uv run --no-sync --directory movies_management python main.py --username your_letterboxd_username
```

This will:
1. Fetch your Letterboxd films and watchlist
2. Identify all unique movies
3. Build/update the movie metadata cache
4. Refresh any cached entries older than `LETTERBOXD_DAYS_TO_UPDATE` days (see below)
5. Export enriched datasets

**Age is the only refresh trigger.** A run re-fetches a cached row solely because its `integration_date` has aged past `LETTERBOXD_DAYS_TO_UPDATE`, capped at `LETTERBOXD_REFRESH_LIMIT` slugs per run. Rows are never re-queued because a column is null.

Backfilling a newly added column onto rows cached before it existed is therefore a **separate, ad-hoc job** — a one-off script targeting the affected slugs, or `--reset_database` for a full rebuild. Keeping it out of the normal run is deliberate: a null-column signal cannot distinguish "not fetched yet" from "legitimately empty" (`composers` is null for ~26% of films with no original score, `trailer_url` for ~45%), so it would re-queue a large slice of the cache on every run and consume the refresh budget forever.

### Force Cache Refresh

To delete the metadata cache and rebuild it from scratch:

```bash
uv run --no-sync --directory movies_management python main.py --username your_letterboxd_username --reset_database
```

### Expand the cache from Allocine showtimes

After the Allocine scraper has run, expand `data_letterboxd.parquet` to include metadata for **every film currently playing in Paris**, not only the films you have rated or watchlisted:

```bash
uv run --no-sync --directory movies_management python main.py --enrich-from-allocine /path/to/showtimes.parquet
```

This mode can be run standalone (no `--username` needed) or combined with `--username` in one call. The enrichment:

1. Reads the showtimes parquet via `common.read_parquet_validated` against `contracts.SHOWTIMES` (the
   same contract `cinema_dashboard/sources/loader.py` enforces on its side of this file), then takes all
   unique `(title, original_title, director, release_year)` tuples from it
2. Resolves each to a Letterboxd slug via Letterboxd search (with year + director post-filtering); films that don't resolve are dropped from downstream processing
3. Fetches and appends metadata for new slugs to `data_letterboxd.parquet` (idempotent — already-cached slugs are skipped)
4. Writes tuples that could not be resolved to `{OUTPUT_PATH}/unresolved_allocine.parquet` for visibility

`cinema_dashboard`'s `orchestrate.py` calls this automatically after each Allocine scrape.

## Output

The application generates three parquet files in your `OUTPUT_PATH`:

### 1. `data_letterboxd.parquet`
**Internal cache** of all movie metadata. Used for incremental updates.

> 📋 The sections below describe what each column *means*. For where each one comes
> from — Letterboxd DOM element, TMDB endpoint, or computed locally — plus measured
> coverage and the checklist for adding a column, see
> **[`CACHE_COLUMNS.md`](CACHE_COLUMNS.md)**.

Every write is validated against the workspace's `contracts.DATA_LETTERBOXD` contract
(`common.write_parquet_validated`) — writing a frame missing any of the columns below raises
`SchemaValidationError` instead of silently shipping a malformed cache. Every column is part of that
contract, including the five territory columns, which are seeded on every row whichever producer fills them; the two reads that reuse an existing cache to skip
already-fetched slugs (in `get_letterboxd_data` and `enrich_cache_from_showtimes`) are deliberately left
unvalidated, since both sit inside a `try`/`except` whose except-branch means "no usable cache — start
fresh," and a validation error there would otherwise trigger a full, silent cache rebuild.

**Identifier Columns:**
- `slug` - Letterboxd unique identifier
- `movie_id` - Letterboxd internal movie ID
- `letterboxd_url` - Link to Letterboxd page
- `imdb_id` - IMDB identifier
- `tmdb_id` - TMDB identifier
- `imdb_url` - Link to IMDB page
- `tmdb_url` - Link to TMDB page

**Core Information:**
- `title` - Official movie title
- `french_title` - French title from TMDB (`language=fr-FR`); `null` when `TMDB_API_KEY` is unset or TMDB has no French entry
- `original_title` - Original title in native language (if different)
- `release_year` - Year of release
- `runtime` - Duration in minutes
- `tagline` - Movie tagline/slogan
- `description` - Full plot description
- `letterboxd_avg_rating` - Letterboxd community weighted-average rating (0-5)

**Media:**
- `poster_url` - URL to movie poster image
- `banner_url` - URL to movie banner image
- `trailer_url` - Official YouTube trailer link from TMDB (French preferred, then English, then any other language); `null` when `TMDB_API_KEY` is unset or TMDB has no matching official trailer

**Genres, themes & keywords:**
- `genres` - Comma-separated primary genres (e.g., "Drama, Science Fiction") — TMDB's closed 19-term vocabulary, which Letterboxd's own genre list turned out to already re-serve almost exactly (98.18% exact set match measured across the cache)
- `keywords` - TMDB's open, crowd-maintained tag space (e.g., "dual identity, paris") — not a replacement for the two theme columns below; the vocabularies intersect on only 8 terms
- `themes` - Comma-separated Letterboxd themes (e.g., "Time Travel, Alternate History")
- `mini_themes` - Comma-separated Letterboxd mini-themes (more specific classifications)

**Crew & cast** — all five come from TMDB's `credits` block in a single request that also carries the trailer (not from Letterboxd), and are `null` when `TMDB_API_KEY` is unset or the film has no `tmdb_id`:
- `directors` - Comma-separated director names (TMDB job `Director`)
- `producers` - Comma-separated producer names (TMDB job `Producer` only — narrower than Letterboxd's list, which also included line/associate/executive producers)
- `writers` - Comma-separated writer names (TMDB jobs `Writer` and `Screenplay`; source-material credits like `Novel`/`Story` are excluded, matching Letterboxd's split)
- `composers` - Comma-separated composer names (TMDB job `Original Music Composer` only). Populated for ~74% of films; the looser `Music` job would reach ~86% but also credits source music on films with no original score, so it is excluded. Co-composed scores are comma-joined.
- `cast` - Top 8 billed cast names, comma-separated (leads only, kept short to keep the taste signal clean)

> ⚠️ `directors` is the taste ranker's highest-weighted dimension and is what confirms the watchlist↔showtimes join in `cinema_dashboard`. Running without `TMDB_API_KEY` leaves it null and degrades both.

Both TMDB responses (`/movie/{id}?language=fr-FR` and `/movie/{id}?append_to_response=credits,videos,keywords`) are parsed through Pydantic
models in `modules/tmdb.py` before any field is read. A malformed payload — one whose *shape* no longer matches
what these fetchers expect, e.g. TMDB changing a field's type — is logged at `logger.warning` (with the
`tmdb_id` and the validation error) instead of the `logger.debug` every other failure path uses (missing
`tmdb_id`/`TMDB_API_KEY`, a non-200 response, exhausted retries). **A `WARNING` from `get_letterboxd_data` at
runtime therefore means TMDB's response shape drifted, not that a film is missing data** — the latter is
normal and expected (most films have no French retitle, no trailer, or no composer) and stays silent at
`debug`. Either way the affected column still comes back `null`/empty for that film; the warning never turns
into a raised exception, so one malformed payload never aborts the batch.

**Territories & provenance — all TMDB:**
- `studio` - Production studio(s), from TMDB `production_companies`
- `country` - The full co-production territory list as display names, from TMDB `production_countries`
- `origin_country` - The nationality of the production as **bare ISO 3166-1 codes**, from TMDB
  `origin_country` — a narrower list than `country`, not a re-spelling of it
- `language` - Spoken languages as English names, from TMDB `spoken_languages`
- `original_language` - The ISO 639-1 code of the original language, from TMDB `original_language`
- *(Any other Letterboxd detail type still expands into its own column automatically)*

**Metadata:**
- `integration_date` - When metadata was fetched (used for refresh logic)
- `source` - Provenance of the row, by the pipeline that ingested it:
  - `ratings` / `watchlist` — written by the Letterboxd user-data pipeline. On every
    `--username` run, `assign_cache_source` reconciles these across the whole cache from
    the current user's rated/watchlisted slugs (ratings wins if a slug is in both).
  - `allocine_showtimes` — written **only** by the Allocine enrichment pipeline
    (`enrich_cache_from_showtimes`) when it adds a film found in the showtimes parquet.
    It is that pipeline's own stamp, never a generic default; the reconciler never
    produces it.

  The fetch helpers (`get_letterboxd_data` / `refresh_letterboxd_data`) no longer
  persist the cache — each caller (`main.py`, `enrich_cache_from_showtimes`) assigns
  `source` and performs the single write.

### 2. `ratings_with_letterboxd.parquet`
**Enriched user ratings** combining your ratings with full metadata.

**User Data Columns:**
- `user_rating` - Your star rating (0.5-5 in half-star steps, or null if unrated)
- `liked` - Whether you marked as liked (boolean)

**All movie metadata columns** from `data_letterboxd.parquet` (see above), including:
- Identifiers (slug, movie_id, imdb_id, tmdb_id, URLs)
- Core info (title, original_title, release_year, runtime, tagline, description, rating)
- Media (poster_url, banner_url)
- Classification (genres, themes, mini_themes)
- Crew (directors, producers, writers, composers)
- Territories (studio, country, origin_country, language, original_language)

### 3. `watchlist_with_letterboxd.parquet`
**Enriched watchlist** combining your watchlist with full metadata.

Contains all columns from `data_letterboxd.parquet` (see above) for movies on your watchlist:
- Identifiers, core info, media, classification, crew, and details

*Note: Only contains movies where metadata was successfully fetched from Letterboxd.*

### 4. `unresolved_allocine.parquet` *(optional)*
Written when `--enrich-from-allocine` is used. Contains `(movie, original_title, director, release_year)` tuples from the showtimes file that could not be resolved to a Letterboxd slug. Useful for diagnosing match failures. Empty when all films resolved successfully. Consumed downstream by `cinema_dashboard`'s Dagster pipeline metadata (`pipeline/assets.py`) and, since the "unmatched films" surface shipped, by the Movies Database page's Unmatched tab (`sources.loader.load_unresolved_allocine` / `build_unresolved_showtimes`) — a missing file there is read as "nothing unresolved," not an error.

## Architecture

### Module Structure

```
movies_management/
├── main.py                           # CLI entry point and orchestration
├── modules/
│   ├── config.py                     # Centralised settings (pydantic-settings BaseSettings)
│   ├── utils.py                      # Data transformation helpers
│   ├── get_letterboxd_data.py        # Letterboxd API interactions and caching
│   └── allocine_enrichment.py        # Allocine → Letterboxd slug resolution and cache expansion
```

> Environment variables live in a single shared `.env` at the **workspace root**, not in this folder. See [Configuration](#configuration).

### Data Flow

```
Letterboxd API
    ↓
User Data (films + watchlist)
    ↓
Build unified DataFrame with source column (ratings | watchlist)
    ↓
Fetch / update metadata cache (parallel, cached)
    ↓
Enrich unified DataFrame with metadata (single left join)
    ↓
Split by source → Output files (ratings + watchlist)
```

### Key Design Decisions

1. **Caching** - Movie metadata is cached locally to minimize API calls. New movies are fetched, existing entries are reused.

2. **Intelligent Refresh** - Age is the only trigger: `find_stale_slugs` selects cached rows whose `integration_date` is older than `LETTERBOXD_DAYS_TO_UPDATE`, bounded by the per-run `LETTERBOXD_REFRESH_LIMIT` cap — reducing API load while keeping data relatively fresh. A null column deliberately never re-queues a row, because a null here is ambiguous ("not fetched yet" vs. legitimately empty — a film with no original score has no `composers`) and would re-queue a large slice of the cache every run, forever. Backfilling a newly added column onto older rows is an ad-hoc script's job, or `--reset_database`; see [`CACHE_COLUMNS.md`](CACHE_COLUMNS.md#refresh--backfill--which-source-re-runs).

3. **Parallel Fetching** - `asyncio` with a semaphore bounding 20 slugs in flight. The blocking Letterboxd scrape runs per slug via `asyncio.to_thread`; the two TMDB lookups for a movie run concurrently in a nested `TaskGroup` over one shared `httpx.AsyncClient`, so connections are pooled across the whole batch. One `append_to_response=credits,videos,keywords` request fills fifteen columns at once (`cast`, the four crew columns, `trailer_url`, the five territory columns and `genres`/`keywords`, all read off the base payload or an appended block); the other exists only because `language=fr-FR` is needed for `french_title` and would localise person names if applied to the credits (see [`CACHE_COLUMNS.md`](CACHE_COLUMNS.md#quirks-that-bite)).

4. **Unified DataFrame** - Ratings and watchlist rows are stacked into one DataFrame before any API calls. A single enrichment join produces both outputs, avoiding redundant merges.

5. **Data Validation** - Enforces no-duplicate-by-slug constraint across both sources before fetching metadata, catching data quality issues early.

6. **Rich Metadata Extraction** - Extracts comprehensive data from Letterboxd including:
   - **Genre classification** - Separates genres, themes, and mini-themes based on Letterboxd's classification system
   - **Territory columns** - `studio`, `country` and `language` come from Letterboxd's details tab by default, or from TMDB under the `territories` group, which also fills `origin_country`/`original_language`
   - **Taxonomy columns** - `genres` comes from Letterboxd by default or from TMDB under the `genres` group, which also fills `keywords` (TMDB's open tag vocabulary — *not* a replacement for `themes`/`mini_themes`, which stay Letterboxd's in both positions)
   - **Media assets** - Includes poster and banner URLs for visual integration

   Crew roles (directors, producers, writers, composers) are **not** taken from Letterboxd — they come from TMDB's `/credits`, one job filter per column. See the crew & cast section above.

7. **Flexible Detail Handling** - Uses `**details_by_type` to dynamically expand any Letterboxd detail type into its own column, so a new type is captured without code changes. Under the `territories` group the three types TMDB then owns (`studio`/`country`/`language`) are filtered out first — the expansion runs last and would otherwise overwrite the TMDB values (see [`CACHE_COLUMNS.md`](CACHE_COLUMNS.md#quirks-that-bite))

## Development

### Running Tests

From the workspace root:

```bash
uv run --no-sync --directory movies_management pytest --cov --cov-fail-under=90
```

### Logging

The application uses Python's standard logging module. Logs are printed to stdout with timestamps and severity levels.

Format: `YYYY-MM-DD HH:MM:SS [LEVEL] module_name — message`

### Performance Notes

- Initial run: ~5-10 seconds per 100 movies (depends on API rate limits)
- Subsequent runs: <1 second (all cached)
- Full rebuild with `--reset_database`: ~5-10 seconds per 100 movies

Cache is stored as parquet for fast I/O and can handle thousands of movies efficiently.

## Troubleshooting

### "Missing option '--username'"
Pass your Letterboxd username as a CLI argument: `uv run --no-sync --directory movies_management python main.py --username your_username`.

### "Duplicate slugs found across ratings and watchlist"
A movie appears in both your ratings and watchlist, which Letterboxd normally prevents. Check the listed slugs and clean up your Letterboxd profile.

### Slow performance
This is normal on initial runs with large libraries. Subsequent runs are much faster due to caching.

For very large libraries (10,000+ movies), consider increasing `LETTERBOXD_DAYS_TO_UPDATE` to reduce refresh frequency.

### API errors
The application gracefully handles transient API failures by skipping individual movies and logging errors. Check logs for which movies failed.

## Known Limitations

- Requires public Letterboxd profile (API limitation)
- Some movies may lack complete metadata on Letterboxd (e.g., missing details)
- Crew and cast come from TMDB, so a film with no `tmdb_id` (or a run without `TMDB_API_KEY`) has none
- Detail types are dynamic based on Letterboxd's available data; not all movies will have all detail columns populated
- Rating data may be sparse for new/obscure films
- Poster and banner URLs may be unavailable for some movies

## Acknowledgments

- [letterboxdpy](https://github.com/jarmstrong2/letterboxdpy) - Python Letterboxd API client
- [pandas](https://pandas.pydata.org/) - Data manipulation library
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- [tenacity](https://tenacity.readthedocs.io/) - Retry/backoff library
