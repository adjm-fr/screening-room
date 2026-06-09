# cinema-monorepo

A `uv` workspace for the local cinema pipeline. Two application members plus shared libraries:

```
cinema-monorepo/
├── packages/
│   ├── common/      # shared settings base, logging, parquet IO, retry, concurrency
│   └── contracts/   # typed parquet schemas (the integration contract)
├── movies_management/   # fetches Letterboxd watchlist + ratings, enriches, writes parquets
└── cinema_dashboard/    # Streamlit dashboard; reads the parquets and renders showtimes
```

The third sibling, **Allocine-Showtimes-Scraping**, stays a standalone, publishable repo. It produces
`showtimes.parquet`, consumed here by both members. Its output schema is mirrored (and test-enforced) in
`packages/contracts`.

## Usage

```bash
uv sync --package movies-management        # install the light scraper env
uv sync --package cinema-dashboard         # install the heavy dashboard env (one shared .venv)

uv run --package movies-management python main.py --username <user>
uv run --package cinema-dashboard streamlit run app.py

# Quality gates (whole workspace)
uv run ruff check . --fix && uv run ruff format .
uv run mypy <targets>
uv run bandit -r <targets> -ll && uv run pip-audit
uv run pytest movies_management/tests cinema_dashboard/tests --cov
```

> The dashboard's `deepeval` eval tooling caps `click<8.4.0`, so `movies_management` uses `click>=8.3,<9`
> (resolves to 8.3.x) to keep the single workspace lock satisfiable.
