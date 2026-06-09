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
- 📊 **Rich data extraction** - Captures genres, themes, crew roles, studio info, country, language, and more
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
| `TMDB_API_KEY` | No | — | TMDB API key for French title enrichment (`french_title` column). Pipeline runs without it; French title stays `null` |

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
4. Refresh any cached entries older than `LETTERBOXD_DAYS_TO_UPDATE` days
5. Export enriched datasets

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

1. Reads all unique `(title, original_title, director, release_year)` tuples from the showtimes parquet
2. Resolves each to a Letterboxd slug via Letterboxd search (with year + director post-filtering); films that don't resolve are dropped from downstream processing
3. Fetches and appends metadata for new slugs to `data_letterboxd.parquet` (idempotent — already-cached slugs are skipped)
4. Writes tuples that could not be resolved to `{OUTPUT_PATH}/unresolved_allocine.parquet` for visibility

`cinema_dashboard`'s `orchestrate.py` calls this automatically after each Allocine scrape.

## Output

The application generates three parquet files in your `OUTPUT_PATH`:

### 1. `data_letterboxd.parquet`
**Internal cache** of all movie metadata. Used for incremental updates.

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
- `letterboxd_avg_rating` - Letterboxd community average rating (0-10)

**Media:**
- `poster_url` - URL to movie poster image
- `banner_url` - URL to movie banner image

**Genres & Themes:**
- `genres` - Comma-separated primary genres (e.g., "Drama, Sci-Fi")
- `themes` - Comma-separated Letterboxd themes (e.g., "Time Travel, Alternate History")
- `mini_themes` - Comma-separated Letterboxd mini-themes (more specific classifications)

**Crew:**
- `directors` - Comma-separated director names
- `producers` - Comma-separated producer names
- `writers` - Comma-separated writer names

**Dynamic Details Columns:**
- `studio` - Production studio(s)
- `country` - Country/countries of origin
- `language` - Primary language(s)
- *(Other detail types may be present based on Letterboxd data)*

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
- `user_rating` - Your rating (0-10 or null if unrated)
- `liked` - Whether you marked as liked (boolean)

**All movie metadata columns** from `data_letterboxd.parquet` (see above), including:
- Identifiers (slug, movie_id, imdb_id, tmdb_id, URLs)
- Core info (title, original_title, release_year, runtime, tagline, description, rating)
- Media (poster_url, banner_url)
- Classification (genres, themes, mini_themes)
- Crew (directors, producers, writers)
- Details (studio, country, language, etc.)

### 3. `watchlist_with_letterboxd.parquet`
**Enriched watchlist** combining your watchlist with full metadata.

Contains all columns from `data_letterboxd.parquet` (see above) for movies on your watchlist:
- Identifiers, core info, media, classification, crew, and details

*Note: Only contains movies where metadata was successfully fetched from Letterboxd.*

### 4. `unresolved_allocine.parquet` *(optional)*
Written when `--enrich-from-allocine` is used. Contains `(movie, original_title, director, release_year)` tuples from the showtimes file that could not be resolved to a Letterboxd slug. Useful for diagnosing match failures. Empty when all films resolved successfully.

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

2. **Intelligent Refresh** - Only movies older than `days_to_update` are refreshed, reducing API load while keeping data relatively fresh.

3. **Parallel Fetching** - Uses thread pool (10 workers) to fetch movies concurrently, improving performance for large libraries.

4. **Unified DataFrame** - Ratings and watchlist rows are stacked into one DataFrame before any API calls. A single enrichment join produces both outputs, avoiding redundant merges.

5. **Data Validation** - Enforces no-duplicate-by-slug constraint across both sources before fetching metadata, catching data quality issues early.

6. **Rich Metadata Extraction** - Extracts comprehensive data from Letterboxd including:
   - **Genre classification** - Separates genres, themes, and mini-themes based on Letterboxd's classification system
   - **Crew roles** - Extracts directors, producers, and writers separately for flexibility
   - **Dynamic detail columns** - Automatically captures studio, country, language, and other attributes as separate columns
   - **Media assets** - Includes poster and banner URLs for visual integration

7. **Flexible Detail Handling** - Uses `**details_by_type` to dynamically expand Letterboxd detail data, so new detail types are automatically captured without code changes

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
- Some movies may lack complete metadata on Letterboxd (e.g., missing crew or details)
- Detail types are dynamic based on Letterboxd's available data; not all movies will have all detail columns populated
- Rating data may be sparse for new/obscure films
- Poster and banner URLs may be unavailable for some movies

## Acknowledgments

- [letterboxdpy](https://github.com/jarmstrong2/letterboxdpy) - Python Letterboxd API client
- [pandas](https://pandas.pydata.org/) - Data manipulation library
- [httpx](https://www.python-httpx.org/) - Async HTTP client
- [tenacity](https://tenacity.readthedocs.io/) - Retry/backoff library
