# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`screening-room` is a **`uv` workspace monorepo** holding a personal cinema pipeline. It was created by
merging two formerly-separate repos (`movies_management`, `cinema_dashboard`) into one workspace, with their
git history preserved (via `git subtree`), plus two new shared packages.

```
screening-room/
├── pyproject.toml          # workspace root: [tool.uv.workspace], shared dev group, ruff/mypy config
├── uv.lock                 # ONE lock for the whole workspace (committed)
├── Makefile                # convenience wrappers (install/run/orchestrate/update) — NOT the gates
├── .github/workflows/ci.yml  # ONE pipeline for the whole workspace
├── packages/
│   ├── common/    src/common/     # AppSettings, configure_logging, validated parquet IO
│   └── contracts/ src/contracts/  # SHOWTIMES parquet schema (the integration contract)
├── movies_management/      # Letterboxd fetcher/enricher (CLI: main.py + modules/)
└── cinema_dashboard/       # Streamlit app (app.py + pages/ utils/ modules/ pipeline/ + orchestrate.py)
```

### Role in the wider pipeline

The third sibling, **`../Allocine-Showtimes-Scraping`**, is intentionally kept as a separate, standalone,
publishable repo (a reusable French-cinema scraper). It writes `showtimes.parquet`, consumed here by **both**
members: `cinema_dashboard/utils/data_loader.py` (the watchlist↔showtimes join) and
`movies_management/modules/allocine_enrichment.py` (cache expansion). The dashboard locates that external
checkout via the `ALLOCINE_DIR` env var (default: a sibling of this repo).

## Commands

```bash
uv sync --all-packages        # install the whole workspace into one shared .venv
cp .env.example .env          # ONE root .env feeds every member (fill in OUTPUT_PATH, keys, …)

# Run a member (reuse the shared venv with --no-sync to avoid re-resolving to one member)
uv run --no-sync --directory movies_management python main.py --username <user>
uv run --no-sync --directory cinema_dashboard  streamlit run app.py
uv run --no-sync --directory cinema_dashboard  python orchestrate.py   # refresh stale data

# Everyday shortcuts: the root Makefile wraps the four commands above
#   make install   → uv sync --all-packages
#   make run       → streamlit dashboard
#   make orchestrate → refresh stale data
#   make update    → git pull this repo + the external Allocine repo ($ALLOCINE_DIR)
# The quality gates below are deliberately NOT in the Makefile — CI owns them. Run them by hand.

# Lint & format (always after a code change) — config is single-sourced in the root pyproject
uv run ruff check . --fix && uv run ruff format .

# uv.lock must stay in sync with the pyprojects — CI's lint job runs this FIRST and a stale
# lock fails the build (the other jobs use `uv sync`, which silently re-resolves and would
# mask it). Guards against a Dependabot bump pinning a version the deepeval ceilings forbid.
# After any dependency change: `uv lock`, then commit uv.lock.
uv lock --check

# Type check (per area, mirroring CI)
uv run --no-sync mypy packages/common/src/common packages/contracts/src/contracts
uv run --no-sync --directory movies_management mypy main.py modules/
uv run --no-sync --directory cinema_dashboard  mypy app.py pages/ utils/ modules/ pipeline/ orchestrate.py

# Security: bandit on source; pip-audit on SHIPPED runtime deps only
uv run --no-sync bandit -r -ll packages/common/src packages/contracts/src \
  movies_management/main.py movies_management/modules \
  cinema_dashboard/app.py cinema_dashboard/orchestrate.py cinema_dashboard/modules \
  cinema_dashboard/pages cinema_dashboard/pipeline cinema_dashboard/utils
uv export --all-packages --no-dev --no-emit-workspace --format requirements-txt -o /tmp/req.txt
uv run --no-sync pip-audit -r /tmp/req.txt

# Tests (per member; each member owns its pytest config)
uv run --no-sync --directory packages/common    pytest
uv run --no-sync --directory packages/contracts  pytest
uv run --no-sync --directory movies_management   pytest --cov --cov-fail-under=90
uv run --no-sync --directory cinema_dashboard    pytest --cov --cov-fail-under=75   # -m 'not evals' by default

# Single test / file / pattern (drop --cov so the per-run gate doesn't fail on a subset):
uv run --no-sync --directory movies_management pytest tests/test_utils.py::test_name
uv run --no-sync --directory cinema_dashboard  pytest -k streaming
# The opt-in LLM eval suite (hits the live Gemini API, needs GEMINI_API_KEY):
uv run --no-sync --directory cinema_dashboard  pytest tests/evals/ -m evals
```

CI (`.github/workflows/ci.yml`) runs four jobs for the whole workspace: lint (incl. `uv lock --check`),
typecheck, security, test.

## Shared packages

- **`common`** (`packages/common`) — the de-duplicated boilerplate that all members shared:
  - `settings.py`: `AppSettings(BaseSettings)` + `make_settings_config()` + `find_workspace_root()`. Each
    member's `modules/config.py` is `class Settings(AppSettings): model_config = make_settings_config(); ...`
    — no argument, so every member loads the **single workspace-root `.env`** (see Non-obvious behaviors).
  - `logging.py`: `configure_logging(level, *, quiet=...)` — wraps `basicConfig`, also sets the root level
    explicitly (so it takes effect under pytest's log capture), and quiets noisy network loggers. Used by
    `movies_management/main.py`, `cinema_dashboard/app.py`, and `orchestrate.py`.
  - `parquet_io.py`: `read_parquet_validated` / `write_parquet_validated` + `SchemaValidationError`.
- **`contracts`** (`packages/contracts`) — `SHOWTIMES` (a frozen `ParquetContract`) declares the 8 columns
  consumed from `showtimes.parquet`. Enforced at the seam: `data_loader.load_showtimes` reads via
  `read_parquet_validated(..., required_columns=SHOWTIMES.required_columns)`, so upstream drift fails loud.

## cinema_dashboard architecture

- **Entry point `app.py`** sets up logging, injects the CSS layer (`utils/ui.inject_css` — editorial
  typography, movie cards, poster rails, chips; called once globally, so page code can assume the classes
  exist), mounts the global `Cmd+K` palette, then routes via `st.navigation` to the five `pages/` files
  (home, database, calendar, streaming, recommendations).
- **Shared UI vocabulary lives in `utils/ui.py`** (`render_movie_card`, `render_poster_rail`,
  `render_hero_card`, chip/KPI/empty-state/freshness helpers). New movie displays should reuse these, not
  hand-roll `st.image`/HTML.
- **The Gemini chat assistant has two surfaces, one state.** `utils/chat.py` owns
  `build_chat_context()` + `render_chat()`; it is mounted full-page by `pages/recommendations.py` (prompt
  chips, pinned-recs column, export) and compact by `utils/cmdk.py` (the `Cmd+K` `st.dialog`, no pinned
  column). Both share `st.session_state["chat"]` (a `ChatState` dataclass) so the conversation persists
  across surfaces; the transcript + pinned recs are also persisted to `data/chat_state.json`
  (`CHAT_STATE_PATH`, patchable in tests) and reloaded on launch — corrupt/absent file falls back to a
  fresh state, and "Clear conversation" deletes the file. The model gets taste profile + showtimes +
  streaming availability as markdown context,
  plus a `search_theater` tool (one round of tool use). The system prompt is strictly **closed-set** — the
  model may only name films/providers present in the injected context — and any new tool must preserve that
  by construction (return rows drawn from the same context, never from outside it). The
  `- {title} — flatrate=…` streaming-context line format is pinned by the eval goldens: append new
  segments (e.g. `; free=…`), never reword the existing prefix.
- **Data flow:** `utils/data_loader.py` loads the parquets, validates `showtimes.parquet` against
  `contracts.SHOWTIMES`, and `build_watchlist_showtimes` produces `wl_shows` — the watchlist↔showtimes join
  every page consumes (one row per movie×showtime, carrying titles, directors, runtime, rating, genres,
  poster, theater, and the streaming list-columns — `utils/streaming.STREAMING_COLUMNS`, i.e. `flatrate`
  plus `free`).
- **Taste ranker lives in `utils/taste.py`** (all formulas + constants in one place). `build_affinity`
  derives signed, shrunk affinities per director/genre/theme/cast/country/language/decade from the
  ratings history (`_DIM_COLUMNS` + `WEIGHTS` are the single place new dimensions plug in; `_CARRY_COLUMNS`
  must mirror any dimension column the showtimes join strips, or "because" chips silently vanish on joined
  rows);
  `score_films` blends them into a stable 0–100 match value (fixed logistic, so a film's badge means the
  same thing every week); `explain` yields the positive contributors for the "✓ because" chips;
  `attach_match` joins scores onto candidate rows. Home's "Top matches this week" rail and the streaming
  rail ordering consume it. `data_loader.build_taste_profile` (the chat-prompt string) is a thin formatter
  over the same profile — its line prefixes ("Average rating given:", "Favourite genres:", …) are a
  contract pinned by `tests/test_data_loader.py` and the eval goldens: extend with new lines, don't reword.
- **Two orchestrators, both intentional.** `orchestrate.py` (CLI, staleness-aware, runs both scrapers in
  parallel) is the everyday path; `pipeline/` is a deliberate Dagster equivalent kept as an experiment
  (`dagster dev -m pipeline.definitions`) — it is not dead code, don't remove it.

## Non-obvious behaviors

- **One shared venv, not per-member.** `uv sync --all-packages` populates a single root `.venv` with every
  member's deps. For per-member runs use `uv run --no-sync --directory <member> ...` — `--no-sync` prevents
  uv from re-resolving the venv down to one member (which would uninstall the others' deps).
- **The single-lock click constraint.** `cinema_dashboard`'s `deepeval` dev tooling caps `click<8.4.0`,
  which clashed with movies' original `click==8.4.1`. Because that's a dev group vs another member's *core*
  pin, `[tool.uv] conflicts` can't resolve it; instead `movies_management` uses `click>=8.3,<9` (settles
  8.3.3). Don't re-pin movies' click to an exact 8.4.x — it breaks the workspace lock.
- **pyarrow is held at 24.x across the workspace.** pyarrow 25.0.0's bundled mimalloc segfaults on macOS
  (`EXC_BAD_ACCESS` in `mi_thread_init` → `mi_heap_main`, first Arrow allocation on a fresh Streamlit
  script-runner thread — crashed the dashboard on launch). All three member pins carry a `<25` ceiling and
  `.github/dependabot.yml` ignores `pyarrow >=25.0.0`; lift both together only after verifying a newer
  release actually runs the dashboard on macOS.
- **`common.__init__` is deliberately pandas-free.** It re-exports only settings + logging (cheap), because
  `modules.config` is on a very-hot import path. The parquet helpers (which import pandas) are imported from
  `common.parquet_io` directly by data loaders, not via the package root.
- **One shared workspace-root `.env`, loaded via `find_workspace_root()`.** `make_settings_config()` (no
  arg) walks up from `common/settings.py` to the `pyproject.toml` declaring `[tool.uv.workspace]` and reads
  that dir's `.env` (the lookup is `@cache`d). Resolution therefore anchors on the *installed* `common`
  package's location — correct for the normal one-checkout/one-venv setup; it only surprises if you run one
  checkout's code under another's venv (e.g. crossing git worktrees), which resolves to the *other* root's
  `.env`. `extra="ignore"` means the one file holds the union of every member's keys and each reads only what
  it declares. Tests point at a throwaway file via `Settings(_env_file=...)` or the optional
  `make_settings_config(tmp_path)` override. Corollary: a fresh git worktree has no `.env` (it's
  gitignored) — copy the main checkout's root `.env` into the worktree root to run the app from there.
- **`cinema_dashboard/assets/provider_display_names.json` is git-tracked but runtime-mutated.** Every
  `orchestrate.py` run auto-grows it when TMDB returns a new streaming provider, so a refresh leaves an
  uncommitted diff in whatever checkout it ran in. Git worktrees have independent working directories, so that
  diff does **not** propagate to sibling worktrees or the main checkout — commit the refresh on `main` and let
  branches pick it up, rather than expecting a worktree to see another checkout's uncommitted change.
- **Free streaming providers are never gated by `STREAMING_SERVICES`.**
  `utils/streaming.STREAMING_COLUMNS = ("flatrate", "free")` is the single source of truth for the
  provider list-columns consumers join on. Free platforms (Arte.tv, France.tv, …) are watchable by
  everyone regardless of subscriptions; TMDB's `rent`/`buy`/`ads` blocks are deliberately not tracked.
  Cache schema changes to `data/streaming_providers.parquet` ship **without** migration guards — force a
  refresh (`refresh_streaming_providers(..., force=True)`) or delete the file instead of adding fallbacks.
- **`refresh_letterboxd_data` merges via `DataFrame.update`, which silently ignores columns absent from
  the target frame.** Any new `data_letterboxd.parquet` column must be pre-seeded on the target
  (`data_df[col] = None`) before `update()`, or refreshed rows never gain it — no error, just missing
  data. Add a regression test when introducing cache columns.
- **`cast` and `trailer_url` are TMDB-sourced cache columns** in `data_letterboxd.parquet` (not from
  letterboxdpy): `cast` is the top-8 billed names `", "`-joined, `trailer_url` a YouTube link preferring FR
  over EN — fetched beside `_fetch_french_title` on the same client, `None` without a `tmdb_id`. Backfill
  is incremental: `main.py` adds missing-`cast` slugs to the refresh queue bounded by
  `letterboxd_refresh_limit` (1000/run), so a large cache converges over 2–3 runs; `--reset_database` is
  the escape hatch.
- **Showtimes datetimes are naive Paris wall-clock.** The Allocine scraper emits no timezone;
  `data_loader.future_showtimes` anchors "now" to `Europe/Paris` accordingly. Other contract quirks:
  `runtime` is a raw string (`"1h 52min"`), `director` may be `" | "`-joined, `release_year` is nullable
  — see `packages/contracts/src/contracts/showtimes.py`.
- **`OUTPUT_PATH` is shared by both members; there is no `MOVIES_OUTPUT_PATH`.** `movies_management` writes
  its parquets to `OUTPUT_PATH` (required field); `cinema_dashboard`'s `movies_output_path` reads the *same*
  key via `Field(validation_alias="OUTPUT_PATH")`. The dashboard's scraper-dir defaults are still computed
  from `_ROOT = Path(__file__).resolve().parents[1]`: `allocine_dir` points *outside* the monorepo
  (`_ROOT.parent.parent / "Allocine-Showtimes-Scraping"`, override with `ALLOCINE_DIR`), `movies_dir` is the
  in-repo sibling. `movies_management/config.py` no longer uses `_ROOT` at all.
- **`cinema_dashboard/modules/scrapers.py` is unchanged.** Its subprocess argv (`uv run python main.py`) is
  run with `cwd` set to the target member/repo, so cwd-based resolution works for both the in-repo movies
  member and the external Allocine repo. No `--package` needed given the shared venv.
- **pip-audit scopes to runtime deps (`--no-dev`).** The dev-only eval tooling tree
  (`deepeval → llama-index → pypdf`, etc.) carries many CVEs that don't affect anything that ships; scanning
  the runtime export keeps the gate meaningful. The shipped runtime deps are currently clean.
- **Coverage gates:** movies 90 (≈98% actual), dashboard 75 (≈82% actual), common 90 (100%). The dashboard
  ran no gate before the merge; 75 gives buffer over its real number.
- **The calendar page's export mirrors its on-screen filters.** `cinema_dashboard/pages/calendar.py`
  narrows one `filtered` frame through every control (the on-page "Only times I'm free" toggle plus the
  sidebar's date range, theater multiselect — empty selection = all theaters — runtime buckets, showtime
  time-of-day range slider, text search, and min rating), and the ICS/CSV export
  (`_build_ics_events(filtered)`) reads that *same* frame — so every filter flows into the download
  automatically. Add a new filter by narrowing `filtered` before the export block; don't rebuild the export
  off the unfiltered `wl_shows` or the two will silently diverge.
- **The free-time filter distinguishes "day off" from "unavailable".** `utils/availability.py` (Streamlit-
  free, unit-tested) computes `watchable = (weekend | FR holiday | day-off | weekday ≥ cutoff) & ~unavailable`.
  A *day off* is free all day (includes daytime screenings); an *unavailable* day (away/vacation) excludes the
  whole day and **overrides everything**, even weekends and holidays — don't merge the two pickers. Holidays
  come from the `holidays` PyPI package (a `cinema_dashboard` *runtime* dep, so pip-audit scans it); both date
  pickers are session-state multiselects over the current showtimes window, deliberately unpersisted (the data
  horizon is ~a week).
- **`build_watchlist_showtimes` strips taste metadata.** Its `_want_cols` whitelist drops `themes`/
  `mini_themes`, and `release_year` is lost to an `_x`/`_y` suffix collision (both sides carry it). That's
  why `taste.attach_match` scores the full-metadata *watchlist* and joins back onto `wl_shows` by `tmdb_id`
  (a solid key: ~0 dupes, ~2 nulls) — don't try to score `wl_shows` directly.
- **The watchlist↔showtimes join is title-matched, director-confirmed.** `build_watchlist_showtimes` matches
  the Allocine display title against **both** normalized watchlist titles — the TMDB `french_title` *and* the
  original `title` — because repertory screenings often run under the original title (VO) even when TMDB
  carries a French retitle (*Sudden Fear* vs *Le Masque arraché*; keying only the French form silently drops
  those screenings). It then keeps a row only when `_directors_overlap` positively confirms the director
  — a precision-first guard so a recurring/remade title (*Nosferatu*, *Les Misérables*) can't attach a wrong
  film's screenings. Confirmation is **token-subset containment**, not exact-key equality: a match holds when
  one director name's tokens are wholly contained in the other's, so cross-source name-form drift
  (`Kirk Jones (II)` vs `Kirk Jones`, `Akinola Davies` vs `Akinola Davies Jr.`, `Ringo Lam` vs
  `Ringo Lam Ling-Tung`) still matches while genuinely different directors are still rejected. Don't tighten
  this back to exact-key equality — that silently drops legitimately-screening films (the bug that motivated
  the containment relaxation). A missing/blank director on *either* side rejects the row.
- **Ratings are on a 0–5 scale, not 0–10.** Both `user_rating` (0.5–5.0, half-star steps) and
  `letterboxd_avg_rating` (community weighted average, ~1.2–4.7 in practice) are 0–5. `utils/ui.rating_to_hsl`
  takes a `scale_max` (default 10 for the 0–100 match heatmap) — the rating chips pass `scale_max=5.0`, and
  the "Min Letterboxd rating" sliders (database + calendar) cap at 5. Treating either column as /10 mis-scales
  the amber heatmap and lets the sliders reach unreachable values. On cards the user's own rating shows as a
  green chip (`chip--user-rating`, `hue=145`) beside the amber community average — Letterboxd's convention.
- **Taste constants are calibrated, not arbitrary.** The user is a harsh rater (mean ≈2.5/5, ~43% of
  ratings ≤2), so all affinity math centers on the *user's own mean* — never recentre on 2.5 or 3.0. The
  shrinkage k, dimension weights, and logistic τ in `utils/taste.py` were tuned against the real parquets;
  changing them shifts every badge. The `liked` column in `ratings_with_letterboxd.parquet` is all-zero
  (pulled from letterboxdpy but never populated) — don't build features on it.

## Conventions

- Python 3.13+, `uv` for everything. Line length 130, ruff rules `E/W/F/I/UP`, mypy `ignore_missing_imports`.
- ruff + mypy config live ONLY in the root `pyproject.toml`. Each member keeps its own
  `[tool.pytest.ini_options]` (pythonpath/markers/asyncio/filterwarnings differ) and `filterwarnings=["error"]`.
- The shared dev toolchain (ruff, mypy, bandit, pytest*, pip-audit, ipykernel) is the root `dev` group; only
  `cinema_dashboard` carries member-specific dev deps (the `deepeval`/`langchain`/`llama-index` eval tooling).
- `uv.lock` and this `CLAUDE.md` are committed (single reproducible workspace lock, and shared guidance for
  every worktree/checkout); `.env` stays gitignored (secrets).

## Testing patterns

- Tests run **per member** from each member's directory (their pytest configs set `pythonpath=["."]`).
- `cinema_dashboard/tests/conftest.py` patches `st.cache_data` to a no-op before imports so coverage can see
  inside decorated functions; `deepeval` is imported by `tests/evals/` (incl. the default-suite
  `test_metrics.py`), which is why it stays in the workspace lock.
- `movies_management` and `packages/*` use plain pytest; `asyncio_mode="auto"` where async tests exist.
- **`pages/*.py` call `main()` unconditionally at import time** (the Streamlit multipage convention —
  `st.Page` executes each file's source). To import a page module in a test, patch
  `modules.config.settings.movies_output_path` to `None` *before the first import* so `main()` hits its
  early return instead of running against the real on-disk parquets (see `tests/test_database.py`).
- **Coverage counts only imported modules** (`pytest --cov` has no source argument), which is why the
  import-time `pages/*.py` don't drag the gate down and why thin CLI entry points beside `orchestrate.py`
  stay outside the report — put testable logic in `utils/` and keep entry points thin.
