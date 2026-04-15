# Letterboxd Movie Management System

A Python application that aggregates and enriches your Letterboxd movie data by combining user ratings and watchlist information with comprehensive movie metadata from the Letterboxd API.

## Overview

This system addresses the limitation that Letterboxd's API doesn't provide complete movie metadata in its user endpoints. The application:

1. **Fetches** your Letterboxd films and watchlist using your username
2. **Builds** a persistent cache of movie metadata (ratings, genres, directors, runtime, descriptions, etc.)
3. **Maintains** the cache by selectively refreshing aged entries
4. **Exports** enriched datasets combining your personal data with complete metadata

All data is stored locally in parquet format for efficient storage and analysis.

## Features

- 🎬 **Automatic metadata enrichment** - Combines user data with complete Letterboxd movie information
- ⚡ **Intelligent caching** - Stores metadata locally to minimize API calls and improve performance
- 🔄 **Smart refresh strategy** - Automatically updates movie data older than a configurable threshold
- 📊 **Structured output** - Generates clean parquet files ready for analysis
- 🧵 **Parallel processing** - Uses thread pools for concurrent API requests
- 📋 **Dual outputs** - Separate enriched files for ratings and watchlist data
- ✅ **Data validation** - Detects and prevents duplicate entries

## Installation

### Requirements
- Python 3.8+
- pip

### Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/movies_management.git
   cd movies_management
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

### Dependencies

- **pandas** - Data manipulation and parquet I/O
- **click** - Command-line interface
- **python-dotenv** - Environment variable management
- **letterboxdpy** - Letterboxd API client

See `requirements.txt` for pinned versions.

## Configuration

Create a `.env` file in the project root with the following variables:

```env
# Required
LETTERBOXD_USERNAME=your_letterboxd_username
OUTPUT_PATH=/path/to/output/directory

# Optional (default: 365)
LETTERBOXD_DAYS_TO_UPDATE=365
```

### Configuration Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LETTERBOXD_USERNAME` | Yes | — | Your Letterboxd username (public profile) |
| `OUTPUT_PATH` | Yes | — | Directory path where parquet files will be saved |
| `LETTERBOXD_DAYS_TO_UPDATE` | No | `365` | Number of days before cached movie metadata is refreshed |

## Usage

### Basic Run

Run the full pipeline:

```bash
python main.py
```

This will:
1. Fetch your Letterboxd films and watchlist
2. Identify all unique movies
3. Build/update the movie metadata cache
4. Refresh any cached entries older than `LETTERBOXD_DAYS_TO_UPDATE` days
5. Export enriched datasets

### Force Cache Refresh

To ignore cache age and refetch all movie metadata:

```bash
python main.py --get_letterboxd
```

## Output

The application generates three parquet files in your `OUTPUT_PATH`:

### 1. `data_letterboxd.parquet`
**Internal cache** of all movie metadata. Used for incremental updates.

**Columns:**
- `slug` - Letterboxd unique identifier
- `title` - Official movie title
- `release_year` - Year of release
- `runtime` - Duration in minutes
- `genres` - Comma-separated genre list
- `description` - Full plot description
- `tagline` - Movie tagline
- `letterboxd_avg_rating` - Letterboxd community average rating
- `directors` - Comma-separated director list
- `imdb_id` - IMDB identifier
- `tmdb_id` - TMDB identifier
- `letterboxd_url` - Link to Letterboxd page
- `imdb_url` - Link to IMDB page
- `tmdb_url` - Link to TMDB page
- `integration_date` - When metadata was fetched (used for refresh logic)

### 2. `ratings_with_letterboxd.parquet`
**Enriched user ratings** combining your ratings with full metadata.

**Additional columns** (beyond movie metadata):
- `user_rating` - Your rating (0-10 or null if unrated)
- `liked` - Whether you marked as liked

### 3. `watchlist_with_letterboxd.parquet`
**Enriched watchlist** combining your watchlist with full metadata.

Only contains movies on your watchlist with available metadata.

## Architecture

### Module Structure

```
movies_management/
├── main.py                           # Orchestration and configuration
├── letterboxd_data_management/       # Movie metadata fetching and caching
│   └── get_letterboxd_data.py        # Letterboxd API interactions
├── ratings_management/               # User ratings enrichment
│   └── get_ratings_infos.py          # Ratings data processing
├── watchlist_management/             # User watchlist enrichment
│   └── get_watchlist_infos.py        # Watchlist data processing
└── .env                              # Configuration file
```

### Data Flow

```
Letterboxd API
    ↓
User Data (films + watchlist)
    ↓
Extract slugs → Fetch metadata (parallel, cached)
    ↓
Movie Metadata Cache (parquet)
    ↓
Merge with user data (left join)
    ↓
Enrich & Validate
    ↓
Output files (ratings + watchlist)
```

### Key Design Decisions

1. **Caching** - Movie metadata is cached locally to minimize API calls. New movies are fetched, existing entries are reused.

2. **Intelligent Refresh** - Only movies older than `days_to_update` are refreshed, reducing API load while keeping data relatively fresh.

3. **Parallel Fetching** - Uses thread pool (10 workers) to fetch movies concurrently, improving performance for large libraries.

4. **Left Join Merges** - Watchlist and ratings use left joins to preserve all user entries even if metadata fetch fails.

5. **Data Validation** - Enforces no-duplicate-by-slug constraint to catch data quality issues early.

## Development

### Running Tests

```bash
pytest tests/
```

### Logging

The application uses Python's standard logging module. Logs are printed to stdout with timestamps and severity levels.

Format: `YYYY-MM-DD HH:MM:SS [LEVEL] module_name — message`

### Performance Notes

- Initial run: ~5-10 seconds per 100 movies (depends on API rate limits)
- Subsequent runs: <1 second (all cached)
- Full refresh with `--get_letterboxd`: ~5-10 seconds per 100 movies

Cache is stored as parquet for fast I/O and can handle thousands of movies efficiently.

## Troubleshooting

### "LETTERBOXD_USERNAME is not set"
Ensure your `.env` file is in the project root and contains the required variables.

### "Watchlist has duplicates"
This indicates data integrity issues in your Letterboxd profile. Check the error logs to identify affected movies.

### Slow performance
This is normal on initial runs with large libraries. Subsequent runs are much faster due to caching.

For very large libraries (10,000+ movies), consider increasing `LETTERBOXD_DAYS_TO_UPDATE` to reduce refresh frequency.

### API errors
The application gracefully handles transient API failures by skipping individual movies and logging errors. Check logs for which movies failed.

## Known Limitations

- Requires public Letterboxd profile (API limitation)
- Some movies may lack complete metadata on Letterboxd
- Genre/director data structure is inconsistent across movies
- Rating data may be sparse for new/obscure films

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Submit a pull request with clear description of changes

## License

[Specify your license here]

## Acknowledgments

- [letterboxdpy](https://github.com/jarmstrong2/letterboxdpy) - Python Letterboxd API client
- [pandas](https://pandas.pydata.org/) - Data manipulation library
