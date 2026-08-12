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
│   └── contracts/ src/contracts/  # SHOWTIMES / DATA_LETTERBOXD parquet schemas (the integration contract)
├── movies_management/      # Letterboxd fetcher/enricher (CLI: main.py + modules/)
└── cinema_dashboard/       # Streamlit app (app.py config.py + core/ sources/ integrations/ chat/ ui/ pages/ pipeline/ + orchestrate.py)
```

`movies_management/modules/` and `cinema_dashboard`'s layered packages are deliberately different shapes, not an
oversight: in `movies_management`, `modules/` *is* the implementation package (one CLI, one flat module tree). In
`cinema_dashboard`, the equivalent code was one 3.7k-line `utils/` plus a two-file `modules/`, split by
responsibility into `core/` (Streamlit-free domain logic), `sources/` (cached parquet readers/joins), `integrations/`
(external systems), `chat/` (the LLM assistant), and `ui/` (Streamlit rendering) — see "cinema_dashboard
architecture" below.

### Role in the wider pipeline

The third sibling, **`../Allocine-Showtimes-Scraping`**, is intentionally kept as a separate, standalone,
publishable repo (a reusable French-cinema scraper). It writes `showtimes.parquet`, consumed here by **both**
members: `cinema_dashboard/sources/loader.py` (the watchlist↔showtimes join) and
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
#   make hooks     → install the git hooks (prek); see "Git hooks" below
#   make run       → streamlit dashboard
#   make orchestrate → refresh stale data (ARGS="--force" / "--days 7 …" passes flags through)
#   make update    → git pull this repo + the external Allocine repo ($ALLOCINE_DIR)
# The quality gates below are deliberately NOT in the Makefile — CI owns them. Run them by hand.
# (`make hooks` only *installs* hooks; it doesn't run gates, so that rule holds.)

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
uv run --no-sync --directory cinema_dashboard  mypy app.py config.py core/ sources/ integrations/ chat/ ui/ pages/ pipeline/ orchestrate.py backtest.py

# ty (Astral) runs beside mypy, NON-BLOCKING in CI while it is pre-1.0 — mypy is still
# the gate. One invocation covers every area the three above do (~0.2s vs mypy's ~22s).
uv run --no-sync ty check \
  packages/common/src/common packages/contracts/src/contracts \
  movies_management/main.py movies_management/modules \
  cinema_dashboard/app.py cinema_dashboard/config.py cinema_dashboard/core \
  cinema_dashboard/sources cinema_dashboard/integrations cinema_dashboard/chat \
  cinema_dashboard/ui cinema_dashboard/pages cinema_dashboard/pipeline \
  cinema_dashboard/orchestrate.py cinema_dashboard/backtest.py

# Security: bandit on source; pip-audit on SHIPPED runtime deps only
uv run --no-sync bandit -r -ll packages/common/src packages/contracts/src \
  movies_management/main.py movies_management/modules \
  cinema_dashboard/app.py cinema_dashboard/config.py cinema_dashboard/orchestrate.py cinema_dashboard/backtest.py \
  cinema_dashboard/core cinema_dashboard/sources cinema_dashboard/integrations cinema_dashboard/chat \
  cinema_dashboard/ui cinema_dashboard/pages cinema_dashboard/pipeline
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
# The opt-in LLM eval suite (hits the live Gemini API, needs GEMINI_API_KEY).
# Add --judge to also run the LLM-as-judge metrics (Faithfulness/AnswerRelevancy).
uv run --no-sync --directory cinema_dashboard  pytest tests/evals/ -m evals

# Re-validate the taste constants against the real ratings (see "Taste ranker" below).
# No flag: metrics for the constants as they stand. --sweep: grid-search candidates.
uv run --no-sync --directory cinema_dashboard  python backtest.py
uv run --no-sync --directory cinema_dashboard  python backtest.py --sweep
```

CI (`.github/workflows/ci.yml`) runs four jobs for the whole workspace: lint (incl. `uv lock --check`),
typecheck (mypy blocking + ty advisory), security, test.

## Shared packages

- **`common`** (`packages/common`) — the de-duplicated boilerplate that all members shared:
  - `settings.py`: `AppSettings(BaseSettings)` + `make_settings_config()` + `find_workspace_root()`. Each
    member's `Settings` class (`movies_management/modules/config.py`, `cinema_dashboard/config.py`) is
    `class Settings(AppSettings): model_config = make_settings_config(); ...`
    — no argument, so every member loads the **single workspace-root `.env`** (see Non-obvious behaviors).
  - `logging.py`: `configure_logging(level, *, quiet=...)` — wraps `basicConfig`, also sets the root level
    explicitly (so it takes effect under pytest's log capture), and quiets noisy network loggers. Used by
    `movies_management/main.py`, `cinema_dashboard/app.py`, and `orchestrate.py`.
    **Secrets are scrubbed at the output boundary, not per call site.** `configure_logging` takes
    `secrets=[...]` and installs a `RedactingFormatter` on every root handler, so no logging call
    anywhere has to remember anything. It exists because TMDB takes its credential as a query
    parameter and httpx embeds the full request URL in `HTTPStatusError`'s string form, so a bare
    `logger.warning("%s", exc)` wrote the live `TMDB_API_KEY` to the log — it did, at DEBUG in
    `movies_management/modules/get_letterboxd_data.py` and at WARNING (the default level) in
    `cinema_dashboard/sources/streaming.py`. Each entry point passes its own keys
    (`main.py`, `app.py`, `orchestrate.py`); `None`/empty entries are ignored.
    Two alternatives were measured and rejected: calling `redact()` at each log site (a rule to
    remember forever, and it covers none of the loggers we don't own), and a custom exception with a
    masked `__str__`/`__repr__` (**leaks through the `__cause__` chain** — `raise Wrapper from exc`
    plus `logger.exception` renders the original traceback with the key in it, unless you use
    `from None` and throw away the debugging context). Formatting the rendered string covers the
    message, the traceback, and third-party loggers (`httpx`, `tenacity`) alike. `redact(value,
    *secrets)` remains the underlying primitive; you rarely need it directly. Residual gap: this
    covers logging only, not an exception escaping to `sys.excepthook` — nothing propagates that far
    here, and the source-level fix would be a header credential, which TMDB only supports via a
    different (v4) token.
  - **API keys are `SecretStr` fields, and that is a *different* protection from the formatter —
    keep both.** `TMDB_API_KEY`/`GEMINI_API_KEY` are `SecretStr` on both members' `Settings`, so
    `str`/`repr`/f-string render `"**********"` and the credential can't be printed by accident.
    It would *not* have caught the leak above: authenticating requires `.get_secret_value()`, and
    from that moment the plain key sits inside the request URL that httpx embeds in its errors. The
    two cover different surfaces — the credential object, and third-party text that embeds it.
    That typing is also what makes the scrubbing self-maintaining: entry points pass
    `secrets=secret_values(settings)`, which walks the model and collects every non-empty
    `SecretStr`, so **declaring a field as `SecretStr` is the only thing to remember.** A
    hand-written list per entry point was the weak link — add a fourth credential and nothing
    fails or warns, its value just starts appearing in the logs.
    `common.reveal(secret)` unwraps the optional ones at the wire boundary (`app.py`,
    `orchestrate.py`, `chat/ui.py`, `pipeline/resources.py`). **Never `str()` a `SecretStr` to pass it on** —
    that yields the literal mask and buys a silent 401 instead of a crash; `pipeline/resources.py`
    does it correctly beside four neighbours that *are* plain `str()` calls. `bool()` still tracks
    emptiness, so the `if not settings.tmdb_api_key` guards kept working across the switch.
  - `parquet_io.py`: `read_parquet_validated` / `write_parquet_validated` + `SchemaValidationError`.
- **`contracts`** (`packages/contracts`) — frozen `ParquetContract`s declaring the columns each consumer
  depends on, enforced at every producer/consumer seam via `read_parquet_validated` /
  `write_parquet_validated`:
  - `SHOWTIMES` — the 8 columns consumed from `showtimes.parquet` (produced by the standalone Allocine
    scraper). Read validated on **both** sides that consume it: `cinema_dashboard/sources/loader.py`'s
    `load_showtimes` and `movies_management/modules/allocine_enrichment.py`'s
    `enrich_cache_from_showtimes`.
  - `DATA_LETTERBOXD` — the stable-core columns of `data_letterboxd.parquet` (produced by
    `movies_management`, consumed by `cinema_dashboard/sources/loader.py`, `core/movie.py`,
    `sources/discover.py`). `studio`/`country`/`language` are deliberately excluded — `_fetch_movie`
    expands them dynamically via `**details_by_type` from whatever Letterboxd detail types a film
    happens to carry, so they aren't guaranteed on every row. Write-validated at both cache writers:
    `movies_management/main.py` (the user-data pipeline) and `allocine_enrichment.py`'s
    `enrich_cache_from_showtimes`. **Two cache reads are deliberately left unvalidated** — the
    "no existing cache, start fresh" branches in `get_letterboxd_data.get_letterboxd_data` and
    `allocine_enrichment.enrich_cache_from_showtimes`, each inside a `try/except` whose except-branch
    means exactly that. If validation raised there it would be swallowed by the `except` and silently
    rebuild the entire multi-thousand-film cache from scratch — a catastrophic, expensive, silent
    failure; the enforcement point is the write, not these reads. `ratings_with_letterboxd.parquet` /
    `watchlist_with_letterboxd.parquet` (`movies_management/modules/utils.py`'s `save_parquet`) have no
    contract yet — a follow-up, not covered here.

## cinema_dashboard architecture

- **Entry point `app.py`** sets up logging, injects the CSS layer (`ui.theme.inject_css` — editorial
  typography, movie cards, poster rails, chips; called once globally, so page code can assume the classes
  exist), mounts the global `Cmd+K` palette, then routes via `st.navigation` to the six `pages/` files
  (home, database, calendar, paris, streaming, recommendations).
- **Routing has a second layer: `?movie=<slug>` overlays the movie detail page.** `app.py` reads
  `st.query_params[ui.MOVIE_QUERY_PARAM]` after `st.navigation` and, when present (even empty — a
  truncated link shows the "no film at this link" empty state), calls `pages/movie.py:main(slug)`
  *instead of* `pg.run()`. `pages/movie.py` is therefore the one page file that does **not** call `main()`
  at import time: it is imported by `app.py`, so a module-level call would only ever fire once per
  process. It can't be an `st.Page` either — `StreamlitPage.run()` only works on the page `st.navigation`
  itself returns, and the overlay shares its URL path with every section. `pages/__init__.py` exists only
  so mypy resolves that file as `pages.movie` (matching the import) rather than twice under two names.
- **Every movie rendered anywhere is a link to its detail page.** `ui.cards._movie_card_html` and
  `render_hero_card` emit an anchor to `movie_href(slug)` whenever the row carries a slug (`row_slug`
  accepts both the `slug` and `letterboxd_slug` spellings), so home/streaming/discover rails, the calendar
  agenda's rows and chat's pinned recs became clickable without touching their call sites — keep new surfaces on
  these renderers and they stay linked. The card uses **one** anchor (the title) stretched over the card by
  a `::after` overlay, because a card already contains a real `<a>` (the trailer chip) and nesting anchors
  is invalid HTML; the hero's overlay anchor is a sibling of `.hero-body` (which is positioned, so an
  `::after` opened inside it would only cover the text). `pages/database.py`'s Tables tab links through a
  `detail_url` `LinkColumn` instead. `_compact_movie_card_html` / `render_compact_movie_card` is the
  horizontal variant for narrow columns (44px thumbnail, no chips, `.movie-row--linked`) — it reuses the
  `.movie-card-link` class rather than declaring its own so it inherits that class's specificity guard.
  `ui/agenda.py`'s `_agenda_row_html` does the same, and is the *simple* case: the row carries no second
  anchor (time pills, the rating chip and the Paris lens badge are `<span>`s, and it deliberately has no
  trailer chip), so unlike
  the card nothing needs lifting back above the `::after` overlay with `z-index` — don't add a second link
  to an agenda row.
  **A renderer only links a row that still carries a slug**, which is why anything persisting a row
  snapshot must re-resolve it before rendering (see the pinned-recs note below).
- **Every anchor rule in `assets/styles.css` needs a specificity guard.** Streamlit styles links inside
  `st.markdown` (theme colour + underline) through a selector that outranks a bare class, so a rule like
  `.movie-card-link { color: inherit; text-decoration: none }` silently loses and the anchor renders as a
  default blue underlined URL — which is exactly what shipped before it was caught by eye. Every anchor
  rule is therefore written three ways: the bare class, `.stMarkdown a.<class>`, and
  `[data-testid="stMarkdownContainer"] a.<class>`. This applies to `.movie-card-link`, `.detail-back` and
  `.chip--trailer` (which also carries the detail page's Letterboxd/IMDB/TMDB out-links); **add the guard
  to any new anchor class or it will look unstyled.** Hover affordances deliberately avoid
  `text-decoration: underline` — the card's lift/shadow and the back pill's fill carry the affordance
  instead.
- **Shared UI vocabulary lives in the `ui/` package**, split by responsibility along the same boundary the
  old 641-line `utils/ui.py` grew: `ui/theme.py` (CSS injection, `format_runtime`/`rating_to_hsl`,
  `movie_href`/`row_slug`), `ui/cards.py` (`render_movie_card`, `render_poster_rail`, `render_hero_card` —
  imports the primitives it needs from `ui.theme` and `render_empty_state` from `ui.chips`), `ui/chips.py`
  (`match_chips_html`, `render_chip_filter`, `render_kpi_strip`, `render_empty_state`,
  `render_freshness_banner`), `ui/availability.py` (`render_free_time_filter` / `FreeTimeSelection`),
  `ui/agenda.py` (`render_agenda`, `render_day_strip`, the calendar agenda's row/day HTML), and
  `ui/ics.py` (`screening_end`, `to_ics`, both export builders, the ad-block sizing). `ui/__init__.py`
  re-exports the full public surface with an explicit `__all__`, so existing `from utils.ui import (...)`
  call sites became `from ui import (...)` — a one-token change; the handful of call sites that need a
  private helper (e.g. `pages/movie.py`'s `_streaming_badges_html`, `ui/agenda.py`'s `_title_of` /
  `_directors_of` / `_rating_chip_html`) import it from the owning submodule directly (`ui.cards`), not the
  package. New movie displays should reuse
  these renderers, not hand-roll `st.image`/HTML.
- **The detail page reads the cache, not the watchlist.** `core/movie.py` (Streamlit-free, pure pandas)
  holds `load_movie` / `movie_screenings` / `similar_films`; `load_movie` keys `data_letterboxd.parquet` by
  `slug` — unique and non-null there, whereas `tmdb_id` has nulls and duplicates and would collide as a
  route key — and left-joins `user_rating` from the ratings parquet (the cache has no such column; the
  all-zero `liked` column stays unused). Because the cache is a clean superset of ratings+watchlist, every
  film the app can render a card for has a page, and the screenings section re-uses
  `build_watchlist_showtimes` keyed on the *cache* so rated/cache-only films list screenings too. Sections
  are omitted, never rendered empty — cache coverage is uneven (measured Aug 2026, after the TMDB credits
  backfill: `trailer_url` ~46% null, `themes` ~31%, `mini_themes` ~31%, `tagline` ~30%, `composers` ~21%,
  `cast` ~1%). `composers` is the one that will *stay* null at that rate — it is TMDB's
  "Original Music Composer" only, so a film with no original score legitimately has none; it is data, not
  an unfinished backfill, and the Credits block drops the row rather than showing it empty.
  **The one section that does *not* read the whole cache is "More like this":** the page passes
  `watchlist_slugs` into `similar_films`, because that same superset property means an unfiltered rail is
  78% films already rated (already seen) against 20% watchlisted — the inverse of a what-to-watch-next
  rail. The watchlist alone leaves a median ~99 candidates per film (limit is 12), and the ~19% of films it
  empties fall through to the omit-the-section rule. `watchlist_slugs=None` (no watchlist parquet) means
  "don't filter"; an empty set is a real empty watchlist and correctly yields no rail.
- **The Gemini chat assistant has two surfaces, one state.** `chat/ui.py` owns the LLM transport and
  UI (`render_chat()`); context assembly (`ChatContext`, `build_chat_context()`, the system prompt) lives
  in `chat/prompt.py`, and conversation state + disk persistence (`ChatState`, `save_chat_state()` /
  `load_chat_state()` / `delete_chat_state()`) lives in `chat/state.py`. **`chat/__init__.py` re-exports
  nothing** — every name is imported from its owning submodule (`from chat.prompt import
  build_chat_context`), because importing *any* `chat.*` submodule executes the package `__init__` first:
  a convenience re-export there would make the deliberately-leaf `chat.tools` pull in `chat.prompt` and,
  through it, `config`/`core.taste`/`integrations.allocine`/`sources.loader` (measured: 2171 → 2202
  modules), and would put an import cycle one edit away the moment `chat/prompt.py` wanted a helper from
  `chat/tools.py`. `chat/ui.py` likewise imports only the names its own code calls, so callers needing
  both (e.g. `pages/recommendations.py`, `ui/cmdk.py`) take `render_chat` from `chat.ui` and
  `build_chat_context` from `chat.prompt`. It is mounted
  full-page by `pages/recommendations.py` (prompt chips, pinned-recs column, export) and compact by
  `ui/cmdk.py` (the `Cmd+K` `st.dialog`, no pinned column). Both share `st.session_state["chat"]` (a
  `ChatState` dataclass) so the conversation persists
  across surfaces; the transcript + pinned recs are also persisted to `data/chat_state.json`
  (`CHAT_STATE_PATH`, patchable in tests) and reloaded on launch — corrupt/absent file falls back to a
  fresh state, and "Clear conversation" deletes the file. The model gets taste profile + showtimes +
  streaming availability as markdown context, plus four tools: `search_theater` (live Allocine lookup,
  declared in `chat/ui.py`) and `top_matches` / `showtimes_query` / `streaming_query` (declared with their
  pure handlers in `chat/tools.py`). `_ask_gemini` dispatches them through a bounded loop (`MAX_TOOL_ROUNDS = 2`,
  plus one final pass to stream the answer); only `search_theater` sets `pending_ref`. **A round is a
  round-trip, not a single call — one model turn can carry several parallel `function_call` parts** (asking
  about two theaters at once produces one `search_theater` call each) and `_ask_gemini` must run *all* of
  them and return a function response for *each*: Gemini rejects a turn whose responses don't cover its
  calls one-for-one, which is how a multi-theater question used to end up with the tool apparently never
  firing. `search_theater` results accumulate across those calls via `_merge_theaters`, deduped on Allocine
  id because `_render_pending_theaters` keys its Add buttons on it. The system prompt is
  strictly **closed-set** — the model may only name films/providers present in the injected context or
  returned by a tool — and any new tool must preserve that by construction (return rows drawn from the same
  context, never from outside it). The `- {title} — flatrate=…` streaming-context line format is pinned by
  the eval goldens: append new segments (e.g. `; free=…`), never reword the existing prefix. The system
  prompt's existing rules are likewise pinned: **insert** new paragraphs, never reword or reflow old ones.
- **The pin picker's candidate set must be the model's whole closed set, or a recommendation can't be
  kept.** `chat.ui._pin_candidates` returns `(wl_shows, _streamable(streaming_df))` — the frame behind the
  showtimes block/`top_matches`/`showtimes_query` *and* the provider-carrying rows behind the streaming
  block/`streaming_query`, filtered by the same "non-empty `flatrate`/`free` list" rule those two apply.
  Scoping it to `wl_shows` alone was issue #53: ask "what can I watch on Netflix?" and every answer is
  unpinnable, since a streaming film screens nowhere. `_find_pinnable_titles` likewise matches **both**
  title spellings (`letterboxd_title` *and* `french_title`, returning the former as the stable pin key) —
  the prompt feeds the model both, so it answers with either — and matches on **whole words**: padding both
  sides of the normalized text is what stops a short title (*Up*, *M*, *RRR*, *Ran* inside "Le Grand Rex")
  from firing on every reply once the candidate set grew from ~12 screening films to hundreds of streaming
  ones. It reads the **whole transcript** (`_assistant_text`), not the latest reply: `state.pinnable` is
  derived per render, not accumulated, so a follow-up question can't un-offer the previous answer's films
  and a transcript reloaded from `data/chat_state.json` comes back pinnable. Add a new source of films the
  model may name (a new tool, a new context block) and it must be added to `_pin_candidates` too.
- **A persisted pin is a frozen row snapshot, so `chat.ui.resolve_pin` re-resolves it at render time.**
  `pinned_recs` stores a whole `wl_shows` row, which freezes that row's *shape*: pins taken before
  `letterboxd_slug` was carried through the showtimes join carry no slug, so `row_slug` finds nothing and
  the card renders as unclickable plain text. Re-resolving beats migrating the file because it also
  immunises every future column addition. Two levels, fixing two different failures: a live `wl_shows` row
  (matched on slug, else on title) supplies fresh columns *and* the **next upcoming** screening rather than
  whichever was scraped on pin day; and when a film's screenings have all passed it leaves `wl_shows`
  entirely, so the stored snapshot is returned with a slug recovered from `ChatContext.slug_by_title` — a
  whole-*watchlist* map (both title spellings), deliberately not derived from `wl_shows`, since the detail
  page reads the cache and the film still has a page. **Both levels fall back to matching on title, and a
  title does not identify a film** — 22 titles in the real watchlist name two different films (*King Lear*
  is Brook's *and* Godard's, *Mandy* is Mackendrick's *and* Cosmatos'). `slug_by_title` therefore maps to a
  **list** of `(slug, directors)`, not one slug: a plain `dict[str, str]` is last-write-wins and silently
  opens the wrong film. Every title match is confirmed by director through the same
  `sources.loader._directors_overlap` token containment the showtimes join uses, and anything still
  ambiguous resolves to *no* slug — an unlinked pin beats a wrong one. Pins render through
  `render_compact_movie_card`, not `render_movie_card`: in a 1/3-width column a 2:3 poster ran several
  hundred pixels tall. Anything else that persists a row snapshot must re-resolve it the same way.
- **The injected context blocks and the chat tools are deliberately redundant — don't "optimize" the
  blocks away.** The blocks define the closed set *at rest*: tools are opt-in, so any turn where the model
  doesn't call one would otherwise be ungrounded. `top_matches` is the tool that adds what the prompt
  genuinely lacks — `_showtimes_context` is built from the *unscored* `wl_shows`, so the 0–100 `match`
  value appears nowhere in the prompt; the tool reads `ctx.wl_scored` (scored once in `build_chat_context`,
  degrading to `wl_shows` on failure) so chat cites the same number as the Home rail badges instead of
  guessing an order. `ctx.taste` is a ~200-token profile distilled from a 4k-row ratings history that is
  never in the prompt; it drives style-matching and carries the rating-ladder legend, so no tool replaces
  it. `showtimes_query` only adds precision (exact day filtering) over data already in context.
  `streaming_query` is different from the other two: it isn't adding data the block lacks, it's recovering
  data the block deliberately *drops*. `_streaming_context` was a one-line-per-streaming-film block that
  alone cost ~74% of the ~9,500-token system prompt (measured against the real cache: 458 lines, ~8,460
  tokens of a ~11,150-token prompt via `tiktoken`'s `cl100k_base`). It's now capped to the top
  `STREAMING_CONTEXT_TOP_N` (50) films by taste `match` — landing around 50 lines / ~900 tokens, prompt
  total ~3,580 tokens — ranked against `streaming_scored` (`watchlist_df` × `attach_streaming` ×
  `attach_match`, computed in `build_chat_context` beside `wl_scored` and falling back to the unranked,
  uncapped full list the same way `wl_scored` falls back to `wl_shows` when there's no usable rating
  history). `ChatContext.streaming_df` carries that same pre-truncation frame so `streaming_query` can
  still answer "is X streaming?" / "what's on Mubi?" for the films the cap left out of the block; when the
  cap actually truncates, `_streaming_context` appends a trailing marker line naming the tool — never in
  the pinned `- {title} — flatrate=…` shape, so it can't be read as a film entry.
- **`chat/tools.py` is Streamlit-free and imports only pandas, `google.genai.types`, and
  `_normalize_title`** — that purity is what keeps the closed set true by construction. Its handlers take
  a DataFrame, *not* `ChatContext` (which would also cycle the import back into `chat.ui`): `top_matches`
  and `showtimes_query` take `ctx.wl_scored`, `streaming_query` takes `ctx.streaming_df`. New tools belong
  here and must follow both rules; they must also be total (missing columns / NaN / junk args return `[]`,
  never raise) since a raised exception would kill the streaming generator mid-reply.
- **Data flow:** `sources/loader.py` loads the parquets, validates `showtimes.parquet` against
  `contracts.SHOWTIMES`, and `build_watchlist_showtimes` produces `wl_shows` — the watchlist↔showtimes join
  every page consumes (one row per movie×showtime, carrying titles, directors, runtime, rating, genres,
  poster, theater, `letterboxd_slug` — the movie-detail route key — and the streaming list-columns —
  `sources.streaming.STREAMING_COLUMNS`, i.e. `flatrate` plus `free`). `sources/` is the layer's name
  instead of the more obvious `data/` because `cinema_dashboard/data/` is a *runtime* directory
  (`data/chat_state.json`, `data/streaming_providers.parquet`) listed in both this project's and the
  workspace root's `.gitignore` — a Python package placed there would be silently untracked and never
  committed.
- **Unmatched Allocine films are surfaced, not just counted.** `movies_management`'s Allocine cache
  enrichment (`allocine_enrichment.enrich_cache_from_showtimes`) writes films it couldn't resolve to a
  Letterboxd slug to `{OUTPUT_PATH}/unresolved_allocine.parquet` — previously read by exactly one thing,
  `pipeline/assets.py`'s Dagster metadata count. `sources.loader.load_unresolved_allocine` now reads it for
  the dashboard too (a missing file is the normal "nothing unresolved" case, not an error — it returns an
  empty frame, same convention as every other loader here); `build_unresolved_showtimes` joins it back onto
  the raw `showtimes.parquet` on the exact `(movie, original_title, director, release_year)` tuple the
  enrichment step read it *from* in the first place (pandas matches `NaN` keys to each other, so a blank
  `original_title`/`director` still joins), dropping screenings already in the past — same
  `future_showtimes`/`Europe/Paris` rule as everywhere else, not the title-normalisation/director-overlap
  fuzziness `build_watchlist_showtimes` needs (these keys come from one source, not two). The Movies
  Database page's "Unmatched" tab (`pages/database.py`) groups the result to one row per film
  (`_unresolved_summary`: next upcoming showtime, theater(s), screening count) and renders a designed
  "✅ every screening matched" empty state — not a broken panel — when there's nothing to review.
- **`sources/discover.py` joins the full showtimes against the metadata cache, not just the watchlist —
  the "Screening in Paris" page (`pages/paris.py`).** Every other showtimes surface is built on
  `build_watchlist_showtimes`'s inner join, so a film screening this week that was never watchlisted never
  appears anywhere; measured against the real parquets, 250 films screen across 13 tracked theaters in a
  week and that join surfaces 14 of them. `build_screenings` reuses `sources.loader._normalize_title` and
  `sources.loader._directors_overlap` — the identical title-matched, director-confirmed contract — against
  `data_letterboxd.parquet` instead of the watchlist, and like it this is an **inner** join — a showtime
  that never confirms a cache match is dropped, because a film with no cache row has no metadata to rank,
  no poster and no detail page, and the enrichment step already reports that set in
  `unresolved_allocine.parquet`. Every row is labelled `"seen"` (its slug is in the ratings parquet),
  `"watchlist"`, or `"untracked"` — the discovery case, taste-scored via `core.taste.attach_match` exactly
  like every other rail so its match badge and "because" chips mean the same thing here as on Home.
  `build_screenings` also maps `user_rating` on from the ratings parquet by slug (the cache has no such
  column — same join `core.movie.load_movie` does for the detail page), because the page's rewatch /
  second-chance sections are cuts on it; the column is always present, all-`NA` when there is no ratings
  parquet, since the page indexes it unconditionally. `french_title` is dropped from the cache-metadata
  side before the `movie`→`french_title` rename (mirroring `build_watchlist_showtimes`'s own `drop_cols`) —
  skipping that step produces two identically-named columns and pandas raises `InvalidIndexError`, a bug
  only the real parquets caught (most cache rows carry both `title` and `french_title`; the unit tests'
  synthetic cache rows mostly didn't).
- **The Paris page is one programme with three lenses — no rails.** It runs the *same* filter machinery as
  the calendar page — `core.agenda.AgendaFilters` + `apply_filters` + `apply_day`, one chain and one frame —
  behind a `_render_toolbar` modelled on the calendar's (search, a badged "Filters" popover with
  theaters/runtime buckets/min Letterboxd rating, a Time/Match sort shown only with a taste profile,
  time-of-day chips, and the shared `ui.render_free_time_filter` whose selection is *folded into*
  `AgendaFilters` rather than applied on the spot — don't also call `FreeTimeSelection.apply`). What it
  deliberately does **not** carry is the calendar's ICS/CSV export, its pydeck theater map and its
  Agenda/Map view switcher: this is a discovery surface, not a planning one. **Every widget key is
  namespaced `paris_*`** (`paris_search`, `paris_theaters`, …, `paris_lens`, `paris_day`,
  `key_prefix="paris"`) — both
  pages can live in one Streamlit session, so a shared `cal_*` key would make one page's filters follow the
  user onto the other; `_filters_badge()` reads those same `paris_*` session keys. `narrowed =
  apply_filters(screenings, filters)` is what everything below reads, and `pages.paris.categorize` (pure,
  total — a missing column just means that category never fires) then assigns each row at most one lens
  category as `_category`: `"new"` (`watch_status == "untracked"`), `"second_chance"` (`user_rating <
  RETRY_MAX_RATING` 2.5 **and** `match >= RETRY_MIN_MATCH` 70 — the disagreement lens), `"rewatch"`
  (`user_rating >= REWATCH_MIN_RATING` 4.0), else `None` — mutually exclusive by construction (untracked
  rows carry no rating; the two rating cuts are disjoint), coerced through `pd.to_numeric(...)` +
  `.fillna(False)` because `series >= x` on the nullable `user_rating` yields `NA`, which pandas rejects as
  a boolean mask. The KPI strip stays the page's own
  watch-status counts (Films screening / New to you / On your watchlist / Already seen, computed on
  `narrowed`), **not** `agenda_kpis` — the question here is "how much of the week is new to me?" — and stays
  on the whole post-`apply_filters` frame, every lens included. The lens control is a single-select chip row
  directly above the day strip (`st.segmented_control`, key `paris_lens`), built like `render_day_strip`:
  stable option values with the label + distinct-film count (`_film_key` nunique — the same slug-first
  identity the agenda groups on) riding in `format_func`, so the stored selection survives count changes;
  zero-count lenses are omitted rather than disabled (the old omit-empty-rail rule — "second chance" needs
  `match`, so it vanishes by itself without a taste profile), and when no lens applies at all the strip
  renders no control. **The lens is a scoping step like the day strip, deliberately not an `AgendaFilters`
  field**: the categories are a Paris-only concept (`watch_status`/`user_rating` don't exist on the
  calendar's `wl_shows` frame), so it follows `apply_day`'s precedent — `lensed =
  narrowed[narrowed["_category"] == lens]`, then `day_chips(lensed)` → `render_day_strip(key="paris_day")` →
  `apply_day` → `build_agenda`. Lens and day strip therefore scope only the agenda; the KPIs stay week-wide.
  Every categorised row also carries its lens into the agenda itself: `ui.agenda._agenda_row_html` renders
  an `.agenda-cat` badge (glyph + text, never color alone) plus an `agenda-row--cat-*` left-accent modifier
  **only when the row carries a non-null string `_category`** — the calendar page's frame has no such
  column, so its rows render exactly as before. The three score/rating thresholds are page constants
  calibrated on the
  real parquets (Aug 2026): 61 films clear the rewatch bar in a week, and of the 24 rated-below-2.5 films
  screening, match≥60 keeps 9, ≥65 keeps 6, ≥70 keeps 3 — 70 is chosen so the lens shows only strong
  disagreement. **A seen film that clears neither "worth" lens is dropped from `narrowed` outright**, right
  after `categorize` runs and before the KPI strip — `pages.paris.drop_uninteresting_seen` (pure, total,
  same missing-column convention as `categorize`). This page's whole premise is "what in this week's
  programme is worth your time", and an already-seen film the ranker didn't flag for a second chance and
  that didn't clear the rewatch bar answers "no" regardless of which lens is selected — so it is cut from
  the frame every lens and the KPI strip read, not just hidden behind one. "Already seen" on the KPI strip
  therefore only ever counts seen films that survived the drop (i.e. still worth a second chance or a
  rewatch), not every seen film screening this week.
- **`dict(some_groupby_object)` breaks on this project's pandas (3.x).** `DataFrameGroupBy` now exposes a
  public `.keys` attribute — the grouping column name(s), a plain string here — which shadows the mapping
  protocol `dict()` checks for (`hasattr(obj, "keys")` then calls it), raising `TypeError: 'str' object is
  not callable`. Caught against the real data (not by the unit tests — synthetic fixtures never exercised
  the actual `groupby` call) in an earlier `pages/paris.py` revision's per-film showtime lookup, since
  removed with the rails. Use a dict/generator
  comprehension over the groupby's `(key, frame)` iteration instead — `{k: g for k, g in df.groupby(col)}`
  — which sidesteps the mapping-protocol check entirely.
- **Taste ranker lives in `core/taste.py`** (all formulas + constants in one place). `build_affinity`
  derives signed, shrunk affinities per director/genre/theme/cast/country/language/decade from the
  ratings history (`_DIM_COLUMNS` + `WEIGHTS` are the single place new dimensions plug in; `_CARRY_COLUMNS`
  must mirror any dimension column the showtimes join strips, or "because" chips silently vanish on joined
  rows);
  `score_films` blends them into a stable 0–100 match value (fixed logistic `match_from_raw`, so a film's
  badge means the same thing every week); `explain` yields the liked contributors (per `SENTIMENT_PIVOT`,
  not affinity sign) for the "✓ because" chips, and is a ranked, liked-only view of `contributions` — the
  unfiltered per-value terms the movie detail page shows in full (disliked ones flagged, not hidden), which
  with `quality_prior` sum back to `_raw_score` exactly, so the breakdown always reconciles with the badge;
  `attach_match` joins scores onto candidate rows. Home's "Top matches this week" rail, home's streaming
  rail (ordering), the streaming page's per-provider rails (ordering, plus the match badge/"because"
  chips on each card), and the Screening in Paris page (the "second chance" lens cut, the agenda's Match
  sort and its per-row match chips) consume it.
  `sources.loader.build_taste_profile` (the chat-prompt string) is a thin formatter
  over the same profile — its line prefixes ("Average rating given:", "Favourite genres:", …) are a
  contract pinned by `tests/sources/test_loader.py` and the eval goldens: extend with new lines, don't reword.
- **Those taste constants are re-derivable, not folklore — `backtest.py` is how.** `core/backtest.py` is the
  harness (`random_holdout_splits` / `raw_scores` / `evaluate`) and `backtest.py` the CLI over it: no flag
  reports held-out Spearman + quartile lift for the constants as they stand, `--sweep` grid-searches
  `SHRINKAGE_K` / cast weight / `QUALITY_WEIGHT`. Run it before *and* after touching anything in
  `core/taste.py` — that is what "calibrated against the real parquets" above means. Three deliberate
  methodology choices, each easy to "fix" into a wrong answer: **repeated random holdout, not a temporal
  split**, because the ratings history has no watch-date column to split on (there is no last-N-months to
  hold out); **raw pre-logistic scores**, because the display logistic is strictly monotone and both metrics
  are rank-based, so it cannot change the answer and only costs float ops and a `LOGISTIC_TAU` dependency;
  and **quantile-based quartile masks, not `nlargest`/`nsmallest`**, because half-star ratings tie heavily
  and rank-order tie-breaking would let DataFrame row order bias which tied rows land in the top quartile.
  A single seeded generator drives every split, so a sweep compares candidates on identical partitions
  instead of confounding the weight change with a different random split. Current numbers on the real
  ratings (Aug 2026, ~3.3k rated films, μ=2.48), as a regression reference: **spearman 0.677 /
  quartile lift 2.03**, against a quality-prior-only baseline of 0.603 / 1.81. Beating that baseline is the
  bar — a constants change that drops toward it has removed the personalisation, not tuned it.
- **`core/agenda.py` is the calendar page's Streamlit-free half** — day grouping, friendly day labels
  ("Tonight"/"Tomorrow"/`%A %d %B`), time-of-day and runtime bucketing, and the one filter chain
  (`AgendaFilters` / `apply_filters` / `apply_day`, see "export mirrors its on-screen filters" below).
  `build_agenda` returns `AgendaDay` → `AgendaEntry` (one film × one day, carrying every showtime it has
  that day), which `ui/agenda.py` renders. Three things not to undo: it **groups on `letterboxd_slug`, not
  the title** — 22 real watchlist titles name two different films (*King Lear* is Brook's *and* Godard's),
  and a title-keyed group merges them into one row carrying both films' showtimes (the same hazard
  `chat.ui.resolve_pin` maps around); `_film_key` is always a string with an `""` fallback because
  `groupby` defaults to `dropna=True` and would make a NaN-keyed row vanish silently; and `today` is an
  injected parameter, never `date.today()` inline, so the day labels are testable rather than going red at
  midnight. `sort="match"` reorders entries *within* a day and never across days — the day strip is itself
  the day picker — and puts unscored entries last. `_film_key` resolves *identity*; `ui.cards._title_of`
  resolves *display*; they look similar and are not interchangeable.
- **`ui/agenda.py` emits one `st.markdown` blob per day, and that is what makes the sticky day header
  work.** Streamlit wraps every `st.markdown` in its own content-sized element container, so a header
  emitted separately from its rows would have a containing block exactly its own height and
  `position: sticky` on `.agenda-day-head` would be a silent no-op. Header + rows must share one blob.
  (If a future Streamlit adds `overflow` to those wrappers it degrades to a plain non-sticky header —
  nothing breaks. Don't reach for `!important` or target `stVerticalBlock` to force it back.)
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
  release actually runs the dashboard on macOS. Streamlit hit the identical crash in its own E2E suite and
  ships the same `pyarrow<25` cap from 1.60.0 onward, so the ceiling is now enforced upstream too — track
  [apache/arrow#50471](https://github.com/apache/arrow/issues/50471) and lift ours only once Streamlit
  relaxes its cap.
- **`common.__init__` is deliberately pandas-free.** It re-exports only settings + logging (cheap), because
  each member's `Settings` subclass (`movies_management/modules/config.py`, `cinema_dashboard/config.py`) is
  on a very-hot import path. The parquet helpers (which import pandas) are imported from
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
  `_update_display_names_catalog` follows its `json.dump` with an explicit `f.write("\n")` because `json.dump`
  emits no trailing newline — that one line is what keeps each rewrite clean under `end-of-file-fixer`, so
  dropping it as redundant would put a spurious ± newline on every refresh commit.
- **Free streaming providers are never gated by `STREAMING_SERVICES`.**
  `sources.streaming.STREAMING_COLUMNS = ("flatrate", "free")` is the single source of truth for the
  provider list-columns consumers join on. Free platforms (Arte.tv, France.tv, …) are watchable by
  everyone regardless of subscriptions; TMDB's `rent`/`buy`/`ads` blocks are deliberately not tracked.
  Cache schema changes to `data/streaming_providers.parquet` ship **without** migration guards — force a
  refresh (`refresh_streaming_providers(..., force=True)`) or delete the file instead of adding fallbacks.
- **`refresh_letterboxd_data` merges via `DataFrame.update`, which silently ignores columns absent from
  the target frame.** Any new `data_letterboxd.parquet` column must be pre-seeded on the target
  (`data_df[col] = None`) before `update()`, or refreshed rows never gain it — no error, just missing
  data. Add a regression test when introducing cache columns.
- **`cast`, the four crew columns, and `trailer_url` are TMDB-sourced cache columns** in
  `data_letterboxd.parquet` (not from letterboxdpy): `_fetch_credits` fills `cast` (top-8 billed, `", "`-joined)
  *and* `directors`/`producers`/`writers`/`composers` from **one** `/credits` round-trip, alongside `trailer_url`
  (a YouTube link preferring FR over EN) — all fetched beside `_fetch_french_title` on the same client, `None`
  without a `tmdb_id`. Each crew column is a job filter
  (`_DIRECTOR_JOBS`/`_PRODUCER_JOBS`/`_WRITER_JOBS`/`_COMPOSER_JOBS`), deduped because
  TMDB lists a person once *per job*. `_COMPOSER_JOBS` is `{"Original Music Composer"}` only: TMDB's looser
  `Music` job would take coverage from 74% to 86% (pre-1950: 67% → 84%) but also credits *source* music on
  films with no original score (*Ariel* returns six names ending in Tchaikovsky), so precision won — the same
  call as `_PRODUCER_JOBS`. `Composer` is not a job string TMDB uses. **`composers` is legitimately null ~26% of
  the time, so it is NOT a valid backfill signal** the way `cast` is — don't add it to `find_missing_cast_slugs`
  or a quarter of the cache would re-refresh every run forever, burning the 1000-slug budget. Two calibration notes, measured on a 250-film sample of the real cache:
  writers use `job in {Writer, Screenplay}` because the wider `department == "Writing"` sweeps in Novel/Story
  credits Letterboxd keeps separate (46% vs 80% agreement with the cached strings); `producers` is deliberately
  just `Producer`, so it is *narrower* than the old Letterboxd list (50.8% exact agreement — it drops
  line/associate/executive producers) and that column visibly changed content at the swap. `directors` matched
  98.4% exactly and **100% under the token-containment rule** `_directors_overlap` applies, which is what made
  the swap safe for the watchlist↔showtimes join.
  **Corollary: without `TMDB_API_KEY`, `directors` is now null**, which silently guts the taste ranker's
  highest-weighted dimension *and* the join's director confirmation — `main.py` warns about this at startup.
  Backfill is incremental: `main.py` adds missing-`cast` slugs to the refresh queue bounded by
  `letterboxd_refresh_limit` (1000/run), so a large cache converges over 2–3 runs; `--reset_database` is
  the escape hatch. Until it converges the cache holds a *mix* of Letterboxd- and TMDB-spelled crew, so a
  director carrying a Letterboxd disambiguation suffix (`Kirk Jones (II)`) briefly scores as two affinity keys.
- **Showtimes datetimes are naive Paris wall-clock.** The Allocine scraper emits no timezone;
  `data_loader.future_showtimes` anchors "now" to `Europe/Paris` accordingly. Other contract quirks:
  `runtime` is a raw string (`"1h 52min"`), `director` may be `" | "`-joined, `release_year` is nullable
  — see `packages/contracts/src/contracts/showtimes.py`.
- **`OUTPUT_PATH` is shared by both members; there is no `MOVIES_OUTPUT_PATH`.** `movies_management` writes
  its parquets to `OUTPUT_PATH` (required field); `cinema_dashboard`'s `movies_output_path` reads the *same*
  key via `Field(validation_alias="OUTPUT_PATH")`. The dashboard's scraper-dir defaults are still computed
  from `_ROOT = Path(__file__).resolve().parents[0]` in `cinema_dashboard/config.py` (the file's own
  directory, i.e. `cinema_dashboard/` — note the index: `config.py` lives at the package root now, not one
  level down in a `modules/` subpackage, so this is `parents[0]`, not the `parents[1]` a nested location
  would need): `allocine_dir` points *outside* the monorepo
  (`_ROOT.parent.parent / "Allocine-Showtimes-Scraping"`, override with `ALLOCINE_DIR`), `movies_dir` is the
  in-repo sibling. `movies_management/config.py` no longer uses `_ROOT` at all.
- **`ALLOCINE_DIR` is the scraper's *checkout*; the two `ALLOCINE_*_PATH` keys are its data — don't conflate
  them.** All three are separate `.env` keys and all three are load-bearing: `ALLOCINE_OUTPUT_PATH` is the
  `showtimes.parquet` the dashboard reads (`build_chat_context` hard-fails with "**ALLOCINE_OUTPUT_PATH** is
  not set" and every showtimes surface degrades to an empty state without it), while `ALLOCINE_INPUT_PATH`
  is the `theaters.csv` the scraper *reads* — see the theaters bullet below. `TMDB_API_KEY` is the fourth
  commonly-missed key: it drives `french_title`/`cast`/`trailer_url` enrichment and the streaming-provider
  cache, all of which simply stay null without it. Every data path is `Path | None` on purpose, so a missing
  key degrades one page instead of crashing the app — which also means a misspelled key fails *silently*
  (`extra="ignore"`), not loudly.
- **`theaters.csv` is a cross-repo *write* seam: the dashboard edits the scraper's input.** The file at
  `ALLOCINE_INPUT_PATH` (three headerless columns — `theater_id,theater_name,address`) is what
  `Allocine-Showtimes-Scraping` reads to decide which cinemas to scrape, and
  `integrations/theaters.py` writes to it: chat's `search_theater` tool surfaces Allocine matches, the
  "add this theater?" flow calls `append_theater` (deduped against `load_theater_ids`), and
  `backfill_addresses` fills blank addresses from the Allocine cache once per session. So **a chat turn can
  change what the sibling repo scrapes on its next run** — that write is the point, not a bug, but it means
  the dashboard is not a read-only consumer of the scraper and edits here outlive the session.
  `sources/geo.py` is the read side: it geocodes each address once via Nominatim
  (`NOMINATIM_USER_AGENT` identifies us — keep it set, it is their rate-limit contract) and persists lat/lon
  to `data/theaters_geo.parquet`, so later loads are free cache hits and the map falls back to central Paris
  for anything unresolved. Map rendering is `st.pydeck_chart`, deliberately Streamlit-native (no Folium dep).
- **`cinema_dashboard/integrations/scrapers.py` is unchanged.** Its subprocess argv (`uv run python main.py`) is
  run with `cwd` set to the target member/repo, so cwd-based resolution works for both the in-repo movies
  member and the external Allocine repo. No `--package` needed given the shared venv.
- **pip-audit scopes to runtime deps (`--no-dev`).** The dev-only eval tooling tree
  (`deepeval → llama-index → pypdf`, etc.) carries many CVEs that don't affect anything that ships; scanning
  the runtime export keeps the gate meaningful. The shipped runtime deps are currently clean.
- **Coverage gates:** movies 90 (97% actual), dashboard 75 (85% actual), common 90 (100%). The dashboard
  ran no gate before the merge; 75 gives buffer over its real number. The gates are deliberately slack —
  raise one only if you intend the headroom to disappear.
- **Git hooks (`.pre-commit-config.yaml`, run by [prek](https://prek.j178.dev)) mirror CI, split by measured
  cost:** pre-commit is ruff + `uv lock --check` + file hygiene + gitleaks (~0.5s), commit-msg is the
  Conventional Commits check, pre-push is mypy + bandit (12.8s cold / 0.27s warm on `cinema_dashboard`).
  Tests (~40s) and pip-audit (network) stay CI-only. Four things not to undo: **ruff/mypy/bandit are `local`
  hooks shelling out to `uv run --no-sync`, deliberately not `astral-sh/ruff-pre-commit`** — a `rev:` would
  be a second ruff pin beside the root pyproject's `ruff==0.16.1`, bumped by Dependabot in a *separate* PR
  from the `uv` group, so the hook could format differently from CI until both landed; **`make hooks` passes
  `--hook-type` explicitly** because prek does not read `default_install_hook_types`, and a bare
  `prek install` wires up pre-commit only, silently dropping commit-msg and pre-push; **the mypy hooks set
  `pass_filenames: false`** and re-check their whole CI area (mypy over a partial file list is unsound),
  scoped by `files:` so you only pay for areas you touched; and **`check-added-large-files` needs
  `--maxkb=1024`** because the default 500 rejects the 739 KB `uv.lock`. The `local` hooks need a synced venv,
  so a fresh worktree needs `make install` before it can commit — the same setup step as copying `.env`.
- **The calendar page's export mirrors its on-screen filters — now structurally, not by discipline.**
  `core.agenda.apply_filters(wl_shows, AgendaFilters(...))` applies every control *except* the day strip
  (search over both titles + directors, theater multiselect — empty selection = all theaters — runtime
  buckets, time-of-day chips, min rating, and the shared free-time selection), then
  `core.agenda.apply_day(narrowed, day)` folds in the day-strip choice; the single `filtered` frame that
  produces is what `build_agenda`, `ui.ics.build_ics_events`, `ui.ics.build_csv_rows` and the map's
  `groupby("theater_id")` all read. Only one function makes the frame, so there is only one frame. **Add a
  new filter by extending `AgendaFilters` + `apply_filters`, never by narrowing again downstream** — that
  is exactly how the download and the screen would diverge. Note the ordering is load-bearing in two
  places: the day chips are built from `narrowed` (so their counts describe the filtered frame, which is
  why the KPI/day-strip containers are *placed* above the toolbar but *filled* after it), and **the day
  strip therefore scopes the export by design** — picking "Wed 5" downloads a one-day `.ics`. Two controls
  the redesign deliberately dropped: the sidebar date range (the day strip covers a ~week horizon better)
  and the 15-minute time-range slider (96 stops for a decision with four real answers). `attach_match` runs
  *before* filtering so `match` survives every narrowing and the "because" chips get their carry columns;
  it resets the index, so nothing index-aligned may cross it. Both exports size their blocks with the
  shared `ui.ics.screening_end` (promoted out of the page module so the movie detail page's per-screening
  `.ics` uses the identical helper), which pads the film's runtime (120min when `runtime_minutes` is
  missing/junk) with the pre-feature ad block — `ADS_MINUTES_CHAIN` (20) when the theater name
  case-insensitively contains `mk2`/`ugc`, else `ADS_MINUTES_DEFAULT` (10). Keep all three downloads on that
  one helper so they can't drift.
- **The free-time control is one widget shared by two pages, rendered and applied at different moments.**
  `ui/availability.py` is the Streamlit half of `core/availability.py`: `render_free_time_filter(rows,
  key_prefix=...)` mounts the toggle plus its three pickers and returns a frozen `FreeTimeSelection`, whose
  `.apply(df)` does the masking. Render and apply are split because the selection is consumed late: both
  `pages/calendar.py` and `pages/paris.py` mount the control in their toolbar and then fold its four fields
  into `AgendaFilters`, so `core.agenda.apply_filters` — not `.apply` — does the masking as one link in the
  single filter chain. **Don't call both**; `.apply` exists for a caller that isn't on that chain. Both pass
  the *unfiltered* frame as the picker's date source, so another filter can't drop a date out from under it,
  and `key_prefix` namespaces the four widget keys since both pages can live in one session. A disabled
  selection is a passthrough that ignores its own other fields, and `.apply` short-circuits on an empty
  frame (which has no `showtimes` column to mask). Don't re-inline this into a page — it was duplicated
  once and drifted immediately.
- **The free-time filter distinguishes "day off" from "unavailable".** `core/availability.py` (Streamlit-
  free, unit-tested) computes `watchable = (weekend | FR holiday | day-off | weekday ≥ cutoff) & ~unavailable`.
  A *day off* is free all day (includes daytime screenings); an *unavailable* day (away/vacation) excludes the
  whole day and **overrides everything**, even weekends and holidays — don't merge the two pickers. Holidays
  come from the `holidays` PyPI package (a `cinema_dashboard` *runtime* dep, so pip-audit scans it); both date
  pickers are session-state multiselects over the current showtimes window, deliberately unpersisted (the data
  horizon is ~a week).
- **`build_watchlist_showtimes` strips taste metadata.** Its `_want_cols` whitelist drops `themes`/
  `mini_themes`, and `release_year` is lost to an `_x`/`_y` suffix collision (both sides carry it). That's
  why `taste.attach_match` scores the full-metadata *watchlist* and joins back onto `wl_shows` by `tmdb_id`
  (a solid key: ~0 dupes, ~2 nulls) — don't try to score `wl_shows` directly. It does **not** strip the
  slug: `letterboxd_slug` (the dedup key) is deliberately kept out of `drop_cols` because it is the
  `?movie=<slug>` route key for every card built off the frame — the home hero, all three home rails and the
  calendar agenda rows would silently stop linking without it.
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
  `letterboxd_avg_rating` (community weighted average, ~1.2–4.7 in practice) are 0–5. `ui.theme.rating_to_hsl`
  takes a `scale_max` (default 10 for the 0–100 match heatmap) — the rating chips pass `scale_max=5.0`, and
  the "Min Letterboxd rating" sliders (database + calendar) cap at 5. Treating either column as /10 mis-scales
  the amber heatmap and lets the sliders reach unreachable values. On cards the user's own rating shows as a
  green chip (`chip--user-rating`, `hue=145`) beside the amber community average — Letterboxd's convention.
- **Taste constants are calibrated, not arbitrary.** The user's ratings are a semantic tier ladder (2.5–3 =
  good, 3.5–4 = must watch, 4.5–5 = masterpiece); the low mean (≈2.5/5, ~43% of ratings ≤2) is the scale's
  design, not harshness. Affinity math still centers on the *user's own mean* — never recentre on 2.5 or
  3.0 (nor 2.25: μ shares the ladder pivot's half-star gap, and recentering only inflates the badge,
  verified July 2026). Sentiment labelling is the one non-μ surface: the "Least favourite" lines, the
  favourite-actors guard, and the "because" chips classify liked/disliked by whether a value's mean rating
  crosses `SENTIMENT_PIVOT = 2.25` (the ladder's watchable/good boundary — semantic, not tuned), so a
  [2.25, μ) "watchable-to-good" value is never branded disliked despite its negative affinity. The
  shrinkage k, dimension weights, and logistic τ in `core/taste.py` were tuned
  against the real parquets; changing them shifts every badge. The `liked` column in
  `ratings_with_letterboxd.parquet` is all-zero (pulled from letterboxdpy but never populated) — don't
  build features on it. The LLM taste-profile string (`format_taste_profile`) carries a pinned "Rating
  scale:" legend line so the chat model doesn't misread the average as dissatisfaction.

## Conventions

- Python 3.13+, `uv` for everything. Line length 130, ruff rules `E/W/F/I/UP`, mypy `ignore_missing_imports`.
- **Two type checkers, one gate.** mypy is authoritative; `ty` runs beside it in CI with
  `continue-on-error` because it is pre-1.0 (pinned exactly, `ty==0.0.70`) and a diagnostic change on a
  version bump must not redden the build. It needs no config — the Python version comes from
  `requires-python` and it resolves `cinema_dashboard`'s bare-name layer imports (`core.taste`,
  `sources.loader`) from the file's own directory, so no `extra-paths`. Two differences to know before
  promoting it: it has **no `ignore_missing_imports` equivalent** (an unresolvable import is an error —
  stricter than mypy here, which silently swallows a misspelled third-party import), and it infers pandas
  types far more precisely, which is what `core.agenda._as_date` and `common.parquet_io.StrPath` exist to
  satisfy. It is deliberately **not** a pre-push hook: a hook either blocks or does nothing, with no
  advisory mode. Keep new code passing both.
- ruff + mypy config live ONLY in the root `pyproject.toml` (ty needs none). Each member keeps its own
  `[tool.pytest.ini_options]` (pythonpath/markers/asyncio/filterwarnings differ) and `filterwarnings=["error"]`.
- The shared dev toolchain (ruff, mypy, ty, bandit, pytest*, pip-audit, ipykernel) is the root `dev` group; only
  `cinema_dashboard` carries member-specific dev deps (the `deepeval`/`langchain`/`llama-index` eval tooling).
- `uv.lock` and this `CLAUDE.md` are committed (single reproducible workspace lock, and shared guidance for
  every worktree/checkout); `.env` stays gitignored (secrets).

## Testing patterns

- Tests run **per member** from each member's directory (their pytest configs set `pythonpath=["."]`).
- `cinema_dashboard/tests/` mirrors the `core/`/`sources/`/`integrations/`/`chat/`/`ui/`/`pages/` layout
  (`tests/core/`, `tests/sources/`, `tests/integrations/`, `tests/chat/`, `tests/ui/`, `tests/pages/`), each
  with its own `__init__.py` — required both to keep same-basename test modules from colliding across
  directories, and because `tests/` itself needs an `__init__.py` too: without it, pytest's import-mode
  climbs the directory tree only until it finds a directory with no `__init__.py`, then inserts *that*
  directory onto `sys.path` and imports the test module under its bare subdirectory name — e.g.
  `tests/ui/test_cards.py` would import as top-level `ui.test_cards`, shadowing the real `ui` package for
  every subsequent import in the process. `tests/__init__.py` makes pytest climb one level further (to
  `cinema_dashboard/`, which has no `__init__.py`), so modules import as `tests.ui.test_cards` instead — no
  collision with `core`/`sources`/`integrations`/`chat`/`ui`. `tests/evals/`'s own `from evals.X import ...`
  imports were adjusted to `from tests.evals.X import ...` for the same reason (they relied on the pre-fix
  climbing behavior). `tests/conftest.py` patches `st.cache_data` to a no-op before imports so coverage can
  see inside decorated functions; `deepeval` is imported by `tests/evals/` (incl. the default-suite
  `test_metrics.py`), which is why it stays in the workspace lock.
- **The eval suite has two paths, and the tool one is load-bearing.** `test_chat_stays_in_bounds` calls
  Gemini *without* tools (prompt-only closed-set check); `test_chat_tool_layer` passes the
  `top_matches`/`showtimes_query`/`streaming_query` declarations and runs `_ask_gemini`'s bounded
  round-trip loop, recording `tools_called` for `ToolCorrectnessMetric`. `search_theater` is deliberately
  *not* dispatched there — it hits live Allocine and writes `theaters.csv`, so an eval run would mutate the
  sibling repo's scraper input. `OVERCAP_STREAMING_GOLDEN` renders 60 films through the real
  `_streaming_context` and asks about the 55th, which only the tool can answer: that is the regression test
  for the `STREAMING_CONTEXT_TOP_N` cap, so keep the golden's film count above the cap if the cap changes.
  Judge metrics (`Faithfulness`/`AnswerRelevancy`) sit behind `--judge` and use an explicit `GeminiModel` —
  DeepEval's default judge wants an `OPENAI_API_KEY` this workspace never provisions. All DeepEval metrics
  taking a model set `async_mode=False`: the async path raises a deprecated-event-loop warning that
  `filterwarnings=["error"]` turns into a hard failure.
- `movies_management` and `packages/*` use plain pytest; `asyncio_mode="auto"` where async tests exist.
- **`pages/*.py` call `main()` unconditionally at import time** (the Streamlit multipage convention —
  `st.Page` executes each file's source). To import a page module in a test, patch
  `config.settings.movies_output_path` to `None` *before the first import* so `main()` hits its
  early return instead of running against the real on-disk parquets (see `tests/pages/test_database.py`).
- **Coverage counts only imported modules** (`pytest --cov` has no source argument), which is why the
  import-time `pages/*.py` don't drag the gate down and why thin CLI entry points beside `orchestrate.py`
  stay outside the report — put testable logic in `core/`/`sources/`/`integrations/`/`chat/`/`ui/` and keep
  entry points thin.
