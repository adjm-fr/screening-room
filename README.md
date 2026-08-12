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

### Git hooks

Optional but recommended — catches lint, type, secret and commit-message problems before CI does:

```bash
make hooks                    # installs prek + wires up the three hook stages
```

[`prek`](https://prek.j178.dev) runs `.pre-commit-config.yaml`; it's a single binary and uses `uv` for hook
environments. The config is plain pre-commit format, so classic `pre-commit` works too.

| Stage | Runs | Cost |
| --- | --- | --- |
| `pre-commit` | ruff check + format, `uv lock --check`, file hygiene, gitleaks | ~0.5s |
| `commit-msg` | Conventional Commits check | instant |
| `pre-push` | mypy (per area touched), bandit | ~13s cold, <1s warm |

Tests (~40s) and `pip-audit` (needs network) stay in CI only. ruff, mypy and bandit run as `local` hooks
against the workspace venv rather than pinned hook mirrors, so they can't drift from the versions CI uses.
`ty` is not a hook: it's advisory in CI, and a hook has no non-blocking mode.

> **Worktrees:** hooks install into the shared git dir, so one `make hooks` covers every worktree — but the
> `local` hooks need a venv. Run `make install` in a new worktree (alongside copying `.env`) or they'll fail
> to spawn. Use `git commit --no-verify` to bypass in a pinch.

## Run

```bash
uv run --no-sync --directory movies_management python main.py --username <letterboxd-user>
uv run --no-sync --directory cinema_dashboard  streamlit run app.py
```

`--no-sync` reuses the shared venv from `uv sync --all-packages` without re-resolving to a single member.

> **Shortcut:** a root `Makefile` wraps these everyday commands — `make install`, `make hooks`, `make run`,
> `make orchestrate`, and `make update` (pull this monorepo + the external Allocine repo). Run `make` on its
> own for the full list. The quality gates below are deliberately left out of it; CI owns them. (`make hooks`
> only *installs* the git hooks, it doesn't run the gates, so that rule still holds.)

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

`make orchestrate` forwards its `ARGS` variable to that command, so every flag above is reachable from the
shortcut too — `make orchestrate ARGS="--force"`, `make orchestrate ARGS="--days 7 --reset"`.

A Dagster-based equivalent lives in `cinema_dashboard/pipeline/` — see
[`cinema_dashboard/README.md`](cinema_dashboard/README.md) for running it via `dagster dev`.

## Quality gates (what CI runs)

```bash
# CI's lint job runs this FIRST — a lock out of sync with the pyprojects fails the build.
# The other jobs use `uv sync`, which silently re-resolves and would mask the drift.
uv lock --check
uv run ruff check . --fix && uv run ruff format .
uv run --no-sync mypy packages/common/src/common packages/contracts/src/contracts
uv run --no-sync --directory movies_management mypy main.py modules/
uv run --no-sync --directory cinema_dashboard  mypy app.py config.py core/ sources/ integrations/ chat/ ui/ pages/ pipeline/ orchestrate.py backtest.py
# ty runs beside mypy, non-blocking (`continue-on-error`) while it is pre-1.0 —
# mypy stays the gate. One invocation covers every area the three above do, in ~0.2s.
uv run --no-sync ty check \
  packages/common/src/common packages/contracts/src/contracts \
  movies_management/main.py movies_management/modules \
  cinema_dashboard/app.py cinema_dashboard/config.py cinema_dashboard/core \
  cinema_dashboard/sources cinema_dashboard/integrations cinema_dashboard/chat \
  cinema_dashboard/ui cinema_dashboard/pages cinema_dashboard/pipeline \
  cinema_dashboard/orchestrate.py cinema_dashboard/backtest.py
uv run --no-sync bandit -r -ll packages/common/src packages/contracts/src \
  movies_management/main.py movies_management/modules \
  cinema_dashboard/app.py cinema_dashboard/config.py cinema_dashboard/orchestrate.py cinema_dashboard/backtest.py \
  cinema_dashboard/core cinema_dashboard/sources cinema_dashboard/integrations cinema_dashboard/chat \
  cinema_dashboard/ui cinema_dashboard/pages cinema_dashboard/pipeline
# pip-audit scans shipped runtime deps only — dev-only eval tooling is excluded
uv export --all-packages --no-dev --no-emit-workspace --format requirements-txt -o /tmp/req.txt
uv run --no-sync pip-audit -r /tmp/req.txt
uv run --no-sync --directory movies_management pytest --cov   # gate 90 (current ~98%)
uv run --no-sync --directory cinema_dashboard  pytest --cov   # gate 75 (current ~82%)
```

One root `.github/workflows/ci.yml` runs lint / typecheck / security / test for the whole workspace.

## Shared packages

- **`common`** — `AppSettings` + `make_settings_config` (each member's `Settings` subclasses these),
  `configure_logging` (used by every entry point — pass `secrets=secret_values(settings)` and it installs a
  formatter that scrubs those API keys out of every log line, traceback included), `reveal` (unwraps a
  `SecretStr` API key at the wire boundary), and `read_parquet_validated` / `write_parquet_validated`.
  API keys are `SecretStr` fields so they can't be printed by accident; the formatter is what stops them
  leaking through URLs embedded in third-party error messages. The two protect different surfaces, and
  `secret_values` derives the scrub list from the model so a newly declared key is covered automatically.
  The package `__init__` is intentionally pandas-free so `cinema_dashboard/config.py` stays cheap to import; import the
  parquet helpers from `common.parquet_io` directly.
- **`contracts`** — `SHOWTIMES` declares the columns consumed from `showtimes.parquet`. The dashboard's
  `load_showtimes` validates against it, so an upstream column rename fails loudly instead of silently
  emptying the watchlist↔showtimes join.

> **Single-lock note:** the dashboard's `deepeval` eval tooling caps `click<8.4.0`, so `movies_management`
> uses `click>=8.3,<9` (resolves to 8.3.x) to keep the one workspace lock satisfiable.
