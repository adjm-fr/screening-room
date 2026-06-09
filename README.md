# screening-room

A `uv` workspace for a personal cinema pipeline: fetch your Letterboxd watchlist + ratings, cross-reference
French cinema showtimes, and browse it all in a Streamlit dashboard. Two application members plus two
shared libraries, resolved by a single lockfile.

```
screening-room/
├── packages/
│   ├── common/         # shared settings base, logging setup, validated parquet IO
│   └── contracts/      # typed parquet schemas — the integration contract
├── movies_management/  # fetches Letterboxd watchlist + ratings, enriches via Letterboxd/TMDB, writes parquets
└── cinema_dashboard/   # Streamlit dashboard; reads the parquets, joins watchlist↔showtimes, renders
```

The third sibling, **Allocine-Showtimes-Scraping**, stays a standalone, publishable repo (a reusable
French-cinema scraper). It produces `showtimes.parquet`, consumed here by both members. Its output schema
is mirrored — and validated at read time — in `packages/contracts`.

## Setup

Requires Python 3.13+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --all-packages        # one shared .venv for the whole workspace
```

All members share a single `.env` at the workspace root — copy `.env.example` to `.env` and fill it in:

```bash
cp .env.example .env
```

Each member reads only the keys it declares and ignores the rest, so the one file holds the union of every
member's variables. The dashboard locates the standalone Allocine checkout via the `ALLOCINE_DIR` env var
(defaults to a sibling of this repo).

## Run

```bash
uv run --no-sync --directory movies_management python main.py --username <letterboxd-user>
uv run --no-sync --directory cinema_dashboard  streamlit run app.py
```

`--no-sync` reuses the shared venv from `uv sync --all-packages` without re-resolving to a single member.

> **Shortcut:** a root `Makefile` wraps these everyday commands — `make install`, `make run`,
> `make orchestrate`, and `make update` (pull this monorepo + the external Allocine repo). Run `make` on its
> own for the full list. The quality gates below are deliberately left out of it; CI owns them.

### Refresh the data

The dashboard reads parquets produced by the scrapers. `cinema_dashboard/orchestrate.py` refreshes them all in
one command — it runs the Letterboxd fetcher and the Allocine scraper in parallel and only re-runs a scraper
when its data is stale:

```bash
uv run --no-sync --directory cinema_dashboard python orchestrate.py            # refresh stale data only
uv run --no-sync --directory cinema_dashboard python orchestrate.py --force    # always re-run both scrapers
uv run --no-sync --directory cinema_dashboard python orchestrate.py --days 7   # scrape 7 days of showtimes (default 14)
uv run --no-sync --directory cinema_dashboard python orchestrate.py --reset    # pass --reset to the Allocine scraper
uv run --no-sync --directory cinema_dashboard python orchestrate.py --reset-db # pass --reset_database to movies_management
```

A Dagster-based equivalent lives in `cinema_dashboard/pipeline/` — see
[`cinema_dashboard/README.md`](cinema_dashboard/README.md) for running it via `dagster dev`.

## Quality gates (what CI runs)

```bash
uv run ruff check . --fix && uv run ruff format .
uv run --no-sync mypy packages/common/src/common packages/contracts/src/contracts
uv run --no-sync --directory movies_management mypy main.py modules/
uv run --no-sync --directory cinema_dashboard  mypy app.py pages/ utils/ modules/ pipeline/ orchestrate.py
uv run --no-sync bandit -r -ll packages/common/src packages/contracts/src \
  movies_management/main.py movies_management/modules \
  cinema_dashboard/app.py cinema_dashboard/orchestrate.py cinema_dashboard/modules \
  cinema_dashboard/pages cinema_dashboard/pipeline cinema_dashboard/utils
# pip-audit scans shipped runtime deps only — dev-only eval tooling is excluded
uv export --all-packages --no-dev --no-emit-workspace --format requirements-txt -o /tmp/req.txt
uv run --no-sync pip-audit -r /tmp/req.txt
uv run --no-sync --directory movies_management pytest --cov   # gate 90 (current ~98%)
uv run --no-sync --directory cinema_dashboard  pytest --cov   # gate 75 (current ~78%)
```

One root `.github/workflows/ci.yml` runs lint / typecheck / security / test for the whole workspace.

## Shared packages

- **`common`** — `AppSettings` + `make_settings_config` (each member's `Settings` subclasses these),
  `configure_logging` (used by every entry point), and `read_parquet_validated` / `write_parquet_validated`.
  The package `__init__` is intentionally pandas-free so `modules.config` stays cheap to import; import the
  parquet helpers from `common.parquet_io` directly.
- **`contracts`** — `SHOWTIMES` declares the columns consumed from `showtimes.parquet`. The dashboard's
  `load_showtimes` validates against it, so an upstream column rename fails loudly instead of silently
  emptying the watchlist↔showtimes join.

> **Single-lock note:** the dashboard's `deepeval` eval tooling caps `click<8.4.0`, so `movies_management`
> uses `click>=8.3,<9` (resolves to 8.3.x) to keep the one workspace lock satisfiable.
