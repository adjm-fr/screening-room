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

The "top matches this week" rail ranks this week's watchlist screenings against a taste profile induced from your ratings history (`core/taste.py`): each rated director, genre, theme, actor, country, language, and decade gets a signed, shrunk affinity centered on *your* average rating; candidate films blend those affinities through fixed weights plus a small Letterboxd-rating prior, mapped to a stable 0–100 match value. Cards show a "◎ {n}% match" badge (amber heatmap) and up to two "✓ because" chips naming the strongest liked contributors (liked/disliked follows the rating ladder's 2.25 sentiment pivot, not the sign of the mean-relative affinity).

The "available on streaming platforms" rail is drawn from the full watchlist (not the cinema join), ranked by taste match (Letterboxd rating as tie-break, and as fallback before any films are rated). A film counts as "available" when it's on a subscribed provider in `STREAMING_SERVICES` (or on any provider when that's unset) **or** on a no-cost provider (Arte.tv, France.tv, …) — free platforms always count, regardless of `STREAMING_SERVICES`.

Every card shows a small badge row: subscribed services carrying the film (filled `chip--streaming`) when `STREAMING_SERVICES` is configured, plus a distinct dashed `chip--streaming-free` badge — labelled with the word "free" so the distinction isn't color-only — for any no-cost provider, unconditionally. Any card with a cached `trailer_url` also gets a `▶ Trailer` chip that opens the trailer on YouTube in a new tab.

**Requires**: `OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH`

### Movie detail (`?movie=<slug>`)

Every movie rendered anywhere in the app — the Home hero, all poster rails, the calendar's agenda rows, the streaming rails, the Discover rail and the chat's pinned recommendations — is a link to that film's detail page at `?movie=<letterboxd-slug>`. It's a real URL: shareable, bookmarkable, and the browser back button returns you to where you were. The page has no sidebar entry; it overlays whichever section you were on, and a "← Back to the dashboard" control clears the parameter. An unknown or truncated slug renders a designed empty state, never a traceback.

The page is backed by `data_letterboxd.parquet`, which is a superset of the ratings and watchlist parquets, so **any** cached film has a page — including the few hundred Allocine-enriched films on neither list. It shows, omitting any section the cache has no data for:

- **Hero** — banner (poster fallback), title, original/French titles, year · runtime · country, tagline
- **Your verdict** — your own star rating as a green chip, else "on your watchlist", else "not tracked yet"
- **Taste match** — the `◎ n% match` badge plus, behind an expander, the full per-dimension breakdown: every value that fed the score with its weight, its shrunk affinity, how many films you've rated it across, and its signed contribution, then the community-quality prior and the logistic that maps the raw total to the badge. Disliked contributors are shown and labelled rather than hidden (sentiment follows the rating ladder's 2.25 pivot, so a value can be *liked* and still contribute negatively). The arithmetic reconciles exactly with the badge on the film's card.
- **Synopsis**, **Credits** (director, writer, producer, studio, top-8 billed cast), **Themes** chips
- **Upcoming screenings** — grouped by theater, each with a one-click `.ics` sized by the same helper the calendar page's bulk ICS/CSV export uses (runtime + the pre-feature ad block)
- **Streaming**, **Trailer** (embedded when cached — null for ~2/3 of films), **More like this** (same-director and shared-theme films, drawn from your *watchlist* — the cache also holds every film you've already rated, which would otherwise fill four fifths of the rail with films you've seen), and out-links to Letterboxd / IMDB / TMDB

**Requires**: `OUTPUT_PATH` (+ `ALLOCINE_OUTPUT_PATH` for the screenings section)

### Movies Database (📊)

Four tabs in place of the old chart wall:
- **Overview** — Genre × avg rating chart (rated films only) + micro-card insights (runtime distribution sparkline, top directors chip cloud, top themes chip cloud). A caption below the title clarifies the stats are based on your rated films count.
- **Discover** — chip filters (genre, director multiselect with live search, min-rating slider) over a poster rail of matching films. Each card shows your own star rating as a green chip (Letterboxd convention) next to the amber Letterboxd community average; both ratings are on the same 0–5 scale.
- **Tables** — raw dataframes with poster, a "Details" link into the film's [movie detail page](#movie-detailmovieslug), IMDB, TMDB, and Letterboxd link columns. A "Streaming on" column lists, per film, the subscribed services currently carrying it (when `STREAMING_SERVICES` is set) plus every no-cost provider suffixed `(free)` (e.g. `netflix, arte-tv (free)`) — free platforms always show, subscription-gated ones don't.
- **Unmatched** — surfaces `unresolved_allocine.parquet`: Allocine screenings whose title/director combination `movies_management`'s Allocine cache enrichment couldn't resolve to a Letterboxd film, otherwise invisible everywhere else in the dashboard. `sources.loader.load_unresolved_allocine` reads the parquet (a missing file is the normal "nothing unresolved" case, not an error — it returns an empty frame) and `build_unresolved_showtimes` joins it back onto the raw `showtimes.parquet` on the exact `(movie, original_title, director, release_year)` tuple the enrichment step read it from, dropping screenings already in the past (`Europe/Paris` wall-clock, same rule as everywhere else). The page groups that to one row per film — title, director, year, theater(s), next upcoming showtime, and screening count — via `pages.database._unresolved_summary`, and renders a designed "✅ every screening matched" empty state when there's nothing to review.

**Requires**: `OUTPUT_PATH` (+ `ALLOCINE_OUTPUT_PATH` for theater/showtime context on the Unmatched tab — without it the tab still reports the raw unresolved count, just without screening details)

### Watchlist Showtimes (📅)

Inner-joins your watchlist with current showtimes. The join matches Allocine's display title against both normalized watchlist titles — the TMDB French retitle *and* the original title, since repertory screenings often run in VO (*Sudden Fear* screens as such even though TMDB calls it *Le Masque arraché*) — and then **confirms each match by director**, so a recurring or remade title (e.g. *Nosferatu*) can't attach the wrong film's screenings. Director confirmation uses token-subset containment — one director name's tokens being wholly contained in the other's — so name-form drift between Allocine and TMDB (`Kirk Jones (II)` vs `Kirk Jones`, `Akinola Davies` vs `Akinola Davies Jr.`, `Ringo Lam` vs `Ringo Lam Ling-Tung`) still matches while genuinely different directors are still rejected.

The result renders as a **compact vertical agenda**: one section per day with a sticky heading ("Tonight · Tuesday 04 August", then a screening count), and inside it one row per film — 56px thumbnail, title, director · runtime · Letterboxd rating, and that day's showtimes as **time pills** (`19:00 Le Champo`). It replaced a horizontal poster rail per day, which spent ~390px of height and a sideways drag per film on a page whose whole job is *what can I see, and when?*. Films are grouped by Letterboxd slug rather than by title, so two different films sharing a name (*King Lear* is Brook's *and* Godard's) stay two rows instead of merging into one with both films' showtimes.

Every control sits in a toolbar above the agenda — this page used to be the only one in the app with a sidebar, which is collapsed by default on the phone the page is most useful on:
- a **day strip** of chips with per-day screening counts (`Tue 4 · 3`), which replaced a date-range picker: the horizon is about a week, so a range control was duplicating a strip that fits on one line. Picking a day also scopes the export, so "download tonight only" is one click
- **search** across both title spellings *and* directors (searching only the Allocine display title used to make a film unreachable by its Letterboxd name)
- a **Filters** popover for the low-frequency controls — theater multi-select (empty selection means *all theaters*, so the long list stays inside the dropdown), runtime buckets, min Letterboxd rating — badged with the number of active filters
- **time-of-day chips** — Morning / Afternoon / Evening / Late — replacing a 15-minute range slider that offered 96 stops for a decision with four real answers
- the **"Only times I'm free"** toggle (shared with Screening in Paris): weekends, French public holidays (via the `holidays` library), days you mark off, or weekdays at/after an editable cutoff (default 19:00). Turning it on reveals the cutoff picker plus two date multi-selects over the upcoming showtime dates — **Days off (free all day)**, which includes that day's daytime screenings, and **Unavailable (away)**, which excludes the whole day and overrides everything else (even a weekend or holiday)
- a **Time / Match** sort. Match ranks by the same `core/taste.py` score Home's rails use, showing a `◎ N% match` badge and "✓ because" chips on each row, and it reorders films *within* each day rather than flattening the agenda — the day strip is already the day picker. The option only appears when there's a ratings history to build a profile from
- an **Export** popover — `.ics` (Google / Apple / Outlook) with CSV behind an expander — and an **Agenda / Map** switch, the map being pydeck markers sized ∝ screening count

Every one of those filters narrows a single frame, and the agenda, both exports and the map all read that same frame, so a download can never disagree with what's on screen. Both exports size each block with one shared helper: the film's runtime (120min when missing or unparseable) plus the pre-feature ad block — 20min when the theater name contains `mk2`/`ugc`, 10min elsewhere — so the calendar entry ends when you actually walk out. Streaming availability isn't shown here; see the dedicated Streaming page.

**Requires**: `OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH` (+ `ALLOCINE_INPUT_PATH` for the map)

### Screening in Paris (🎭)

Every other showtimes-driven page is built on `build_watchlist_showtimes`, an inner join that only ever surfaces films already on the watchlist — measured against the real parquets, 250 films screen across 13 tracked theaters in a week and that join surfaces 14 of them. This page joins the **full** showtimes against the Letterboxd metadata cache instead (`sources/discover.py`'s `build_screenings`), reusing the exact title-matched, director-confirmed contract `build_watchlist_showtimes` uses (see Watchlist Showtimes above), and like it dropping any showtime that never confirms a cache match — those are diagnosed on the Movies Database page's Unmatched tab, not here. Every film is labelled with a watch status — **New to you** (in the cache but neither rated nor watchlisted), **Watchlist**, or **Seen** (present in the ratings parquet) — and taste-ranked with the same `core/taste.py` ranker every other rail uses, so the match badge and "✓ because" chips mean the same thing here as on Home.

The page is **curated sections, not a filter wall — there is no browse-everything rail**. The one control is the "Only times I'm free" toggle (`core.availability.free_time_mask`, identical semantics to the Watchlist Showtimes page below — weekends, French holidays, days off, or weekday evenings after a cutoff, minus days marked unavailable), which narrows every section to screenings the user can actually attend before any of them render. A KPI strip then counts unique films per status, followed by three rails, each answering one question and each omitted (not rendered empty) when nothing qualifies:

| Rail | What's in it |
| --- | --- |
| **Best matches — new to you** | Never rated, never watchlisted; highest taste match first. |
| **Worth a second chance?** | Films you rated **< 2.5** that the ranker nonetheless scores **≥ 70** — the disagreement rail. Deliberately short: of the 24 films rated under 2.5 screening in a sample week, 3 cleared 70. |
| **Worth a rewatch!** | Films you rated **≥ 4.0**, ordered by *your* rating — this rail is your verdict, not the ranker's. Last and biggest (up to 24 cards, vs. 12 for the other two): it draws on films the user has already vouched for, so there's less risk in showing more. |

The thresholds are constants at the top of `pages/paris.py` (`REWATCH_MIN_RATING`, `RETRY_MAX_RATING`, `RETRY_MIN_MATCH`, `REWATCH_RAIL_SIZE`). Every card also lists its upcoming showtimes (day, time, theater) beneath it — capped at `MAX_SHOWTIME_BADGES` (6) with a "+N more" suffix for a wide release, which can carry dozens of screenings in a week.

**Requires**: `OUTPUT_PATH` + `ALLOCINE_OUTPUT_PATH`

### Streaming (📺)

One horizontal poster rail per FR streaming provider, populated from the TMDB watch-providers cache. Films are taken from your full watchlist (not only those with upcoming showtimes). When a ratings history exists each rail is taste-ranked (`core/taste.py`) and every card carries the same "◎ {n}% match" badge and "✓ because" chips as the Home rails, with the Letterboxd average breaking ties; before any films are rated, rails fall back to Letterboxd average order. A multi-select chip filter at the top (with an inclusive *All* sentinel) lets you focus on one or more providers using human-readable provider names (e.g. *Canal+*, *MUBI*). The slug → pretty-name map is persisted at `assets/provider_display_names.json` and auto-grows every time `orchestrate.py` refreshes the cache and TMDB returns a new provider.

Rails cover two kinds of availability: subscription (`flatrate`) providers, limited to your `STREAMING_SERVICES` when set (every flatrate provider TMDB returns when it's unset), and no-cost `free` providers (e.g. Arte.tv, France.tv) — free platforms always get a rail, regardless of `STREAMING_SERVICES`, since they're watchable by everyone. The chip filter operates over the union of both. The page is explicitly FR-scoped — availability comes from TMDB's France region, and only `flatrate`/`free` are tracked (rent/buy/ads listings are intentionally not surfaced).

**Requires**: `OUTPUT_PATH` (+ `TMDB_API_KEY` set when running `orchestrate.py` so the cache is populated)

### Recommendations (🤖)

Chat interface powered by the [Gemini API](https://ai.google.dev/) via the native `google-genai` SDK (model configurable via `GEMINI_MODEL`, defaults to `gemini-3.1-flash-lite`). Ask questions like:

- "Which watchlist movies are showing this weekend?"
- "Based on my taste, what should I prioritise?"
- "What's showing at Cinéma X that I'd enjoy?"
- "What are my top matches this week?" *(taste-ranked, via tool use)*
- "What's on my streaming services tonight that fits my taste?" *(requires `STREAMING_SERVICES`)*

Power-user surface: prompt-suggestion chips, streaming spinner with transparent tool-call expanders, in-page pinned-recommendations column on the right (substring-match watchlist titles in each reply, then click to pin), Markdown conversation export. Pins render as compact rows — thumbnail, title, director, next screening — each linking to the film's detail page.

The same assistant is reachable from any page via the global **`Cmd+K`** command palette (or the "✦ Ask AI" sidebar button). Both surfaces share a single `st.session_state['chat']` (a `ChatState` dataclass) so the conversation persists across them. The transcript and pinned recommendations are also persisted to `data/chat_state.json` (gitignored, beside the streaming/geo caches) and reloaded on the next launch, so they survive app restarts — saved after each assistant reply and pin change; **🗑 Clear conversation** deletes the file. A corrupt or missing file falls back to a fresh conversation. A reloaded pin is a snapshot of the row as it was when pinned, so it is re-resolved against the current showtimes before rendering — that keeps its detail-page link and its "next screening" line correct as the data moves on, and falls back to the snapshot once the film stops screening. Because a title alone doesn't identify a film (22 watchlist titles are shared by two, e.g. Brook's and Godard's *King Lear*), that re-resolution is confirmed by director and gives up rather than guess — a pin never opens a different film of the same name.

The page derives a taste profile from your Letterboxd ratings (favourite *and least favourite* genres, themes, directors, plus favourite actors and eras — ranked by the signed affinities in `core/taste.py`, with liked/disliked classified against the ladder's 2.25 sentiment pivot) and sends only the matched watchlist-showtime rows to the model — no full parquets are transmitted. Because ratings follow a tier ladder rather than a conventional satisfaction scale (2.5–3/5 already means a good film, 3.5+/5 a must-watch), the profile carries a `Rating scale:` legend line and the system prompt is reminded not to read the ~2.5/5 average as dissatisfaction. When the FR streaming-providers cache is populated, per-film availability is injected into the system prompt as `flatrate={a, b}` (subscription providers) plus, when the film also has one, `; free={c}` (no-cost providers) — and the model is rule-bound to only reference providers from those lists (no hallucinated availability). That streaming block is capped to the user's top 50 taste-matched films (`chat.prompt.STREAMING_CONTEXT_TOP_N`) — an uncapped block cost ~74% of the whole system prompt's tokens — with the rest still reachable via the `streaming_query` tool below.

#### Tool use

The model can call four read-only tools, each surfaced in the chat as a collapsed "🛠" expander showing what was queried and what came back (up to two tool calls per question):

| Tool | What it does |
| --- | --- |
| `top_matches` | Ranks your **own** watchlist films that have upcoming screenings by taste match (`core/taste.py`), optionally narrowed to a genre — "what are my top matches tonight?" |
| `showtimes_query` | Targeted screening lookup filtered by title (original *or* French), theater, and/or day — "when is X playing?", "what's on at the Champo on Saturday?" |
| `streaming_query` | FR streaming lookup filtered by title and/or provider, reaching films the streaming block's top-50 cap left out — "what's on Mubi?", "is X streaming anywhere?" |
| `search_theater` | Searches Allocine for a Paris cinema you haven't tracked yet (see below) |

`top_matches`, `showtimes_query` and `streaming_query` live in `chat/tools.py` and are pure filters over the same watchlist×showtimes / watchlist×streaming frames already injected into the prompt (just not truncated to the prompt's top-N cap): they can only ever return rows that are in one of those frames, so tool use never widens the model's closed set.

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
   ┌──────┬──────────┬──────┴──────┬──────────────┬───────────┬─────────────────┐
  Home  Database  Watchlist    Screening in     Streaming  Recommendations
              Showtimes         Paris
   └──────┴──────────┴─────────┴──────────────┴───────────┘        │
     every movie card → ?movie=<slug>                        Gemini API
     (pages/movie.py — detail overlay)                  (google-genai SDK)
                                                                    │
                               sources/loader.py          ← cached parquet readers + watchlist↔showtimes join
                               sources/discover.py        ← full showtimes↔cache join + watch-status labels + user_rating
                               sources/streaming.py       ← TMDB FR providers cache
                               integrations/allocine.py   ← theater lookup
                               integrations/theaters.py   ← CSV append
```

## Project structure

```
cinema_dashboard/
├── app.py                        # Streamlit entry point — registers pages, injects CSS, mounts Cmd+K, routes ?movie=<slug>
├── config.py                     # Centralised settings via pydantic-settings (BaseSettings)
├── orchestrate.py                # Lightweight CLI to refresh all data (consumes integrations/scrapers.py)
├── backtest.py                   # CLI to evaluate/sweep the taste-ranker constants against held-out ratings
├── .streamlit/
│   └── config.toml               # Cinema theme: dark + light, system-driven
├── assets/
│   ├── styles.css                # Design tokens, movie cards, poster rails, agenda rows + sticky day headers + time pills, chips, KPI cards, detail page + contribution bars, anchor styling, motion, focus rings, mobile media queries
│   └── provider_display_names.json  # Slug → pretty-name catalogue (auto-grown by refresh_streaming_providers)
├── pipeline/                     # Dagster pipeline (alternative to orchestrate.py)
│   ├── assets.py                 # @asset definitions for showtimes + watchlist (consume integrations/scrapers.py)
│   ├── resources.py              # ScraperConfig resource (ScraperConfig.from_settings)
│   └── definitions.py            # Dagster Definitions entry point
├── pages/
│   ├── __init__.py               # Makes `pages` a real package (app.py imports pages.movie — see the module docstring)
│   ├── 0_home.py                 # Home — hero "tonight" card, poster rails, KPI strip
│   ├── database.py               # Movies Database page (Overview / Discover / Tables / Unmatched)
│   ├── calendar.py               # Watchlist Showtimes page (top toolbar, day strip, vertical agenda, Time/Match sort, export popover, map view)
│   ├── movie.py                  # Movie detail page — routed by ?movie=<slug>, not by st.navigation (no import-time main())
│   ├── paris.py                  # Screening in Paris page — full showtimes×cache discovery, curated rewatch/second-chance rails
│   ├── streaming.py              # Streaming page — one poster rail per FR provider
│   └── recommendations.py        # Recommendations chat page (calls chat.ui.render_chat)
├── core/                         # Streamlit-free domain logic
│   ├── taste.py                  # Taste ranker — affinity profile, 0–100 match scorer, "because" explanations + full contribution breakdown
│   ├── movie.py                  # Movie detail data assembly (load_movie, movie_screenings, similar_films)
│   ├── availability.py           # Free-time mask (weekend/holiday/day-off/after-cutoff, minus unavailable days)
│   ├── agenda.py                 # Calendar day grouping, friendly day labels, time/runtime buckets, the one filter chain
│   └── backtest.py               # Held-out evaluation of the taste-ranker constants (used by backtest.py)
├── sources/                      # Cached parquet readers + joins. Named `sources`, not `data` — the
│   │                              # runtime `data/` dir is gitignored, so a package there would never commit.
│   ├── loader.py                  # Cached parquet readers + watchlist↔showtimes join + attach_streaming
│   ├── discover.py                # Full showtimes↔Letterboxd-cache join + watch-status labels + user_rating
│   ├── streaming.py               # TMDB FR watch-providers cache + display-name catalogue loader/updater
│   └── geo.py                     # Theater geocoding (Nominatim + RateLimiter, cached parquet) + pydeck map renderer
├── integrations/                 # External-system integrations
│   ├── allocine.py                # Searches Paris theaters via the Allocine API
│   ├── theaters.py                # Reads/appends to the theaters CSV
│   └── scrapers.py                # Shared scraper command builders + staleness rules (single source of truth)
├── chat/                         # The Gemini recommendations assistant
│   ├── ui.py                      # Gemini transport + chat UI (render_chat)
│   ├── prompt.py                  # ChatContext assembly + the pinned system prompt (build_chat_context, build_system_message)
│   ├── state.py                   # ChatState dataclass + transcript/pins persistence to data/chat_state.json
│   └── tools.py                   # Pure handlers + declarations for the top_matches / showtimes_query / streaming_query tools
├── ui/                           # Streamlit rendering — split from a single 641-line utils/ui.py
│   ├── theme.py                   # CSS injection, format_runtime/rating_to_hsl, movie_href/row_slug
│   ├── cards.py                    # render_movie_card, render_compact_movie_card, render_poster_rail, render_hero_card
│   ├── agenda.py                   # render_agenda, render_day_strip, agenda row/day HTML, time pills
│   ├── chips.py                    # match_chips_html, render_chip_filter, render_kpi_strip, render_empty_state, render_freshness_banner
│   ├── availability.py             # "Only times I'm free" control (render_free_time_filter / FreeTimeSelection)
│   ├── ics.py                      # screening_end, to_ics, build_ics_events, build_csv_rows, ad-block sizing
│   └── cmdk.py                     # Global Cmd+K command palette (st.dialog + hand-rolled st.iframe shortcut)
├── tests/
│   ├── conftest.py                # Shared fixtures + @st.cache_data no-op patch
│   ├── test_config.py             # Covers the root config.py
│   ├── core/ sources/ integrations/ chat/ ui/ pages/   # mirror the packages above, one test_*.py per module
│   └── evals/                    # LLM hallucination evals (opt-in via `-m evals`)
│       ├── goldens.py            # Bait prompts + allowed film/provider sets
│       ├── metrics.py            # FilmSetMembership + StreamingClaim DeepEval metrics
│       ├── test_metrics.py       # Unit tests for the metric regex (no Gemini calls)
│       └── test_chat_evals.py    # Parameterized harness, no-tool + tool paths (hits live Gemini API)
```

`movies_management` deliberately keeps its own `modules/` package — there it *is* the implementation (one
CLI, one flat module tree). `cinema_dashboard`'s equivalent code used to be a flat `utils/` (13 files, 3.7k
lines) plus a two-file `modules/`; it is now split by responsibility into the layered packages above, so the
divergence between the two members is intentional, not an oversight.

> Environment variables live in a single shared `.env` at the **workspace root**, not in this folder. See [Configuration](#configuration).

All pages share `sources/loader.py` for parquet I/O and the watchlist↔showtimes join. Centralising the loaders means Streamlit's `@st.cache_data` keys on a single qualified function name, so each parquet is read once across all pages within the cache TTL — navigating between pages is a cache hit.

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

After the Allocine scrape succeeds, the orchestrator automatically runs a third step that expands `data_letterboxd.parquet` with Letterboxd metadata for every film found in the fresh `showtimes.parquet` — not only the user's watchlist and ratings. Films that cannot be resolved to a Letterboxd slug are written to `{OUTPUT_PATH}/unresolved_allocine.parquet` — surfaced in the dashboard's Movies Database page, on the "Unmatched" tab (see [Pages](#pages) above), rather than only in Dagster metadata.

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

Streamlit cache TTL is **5 minutes**, shared across all pages (`DATA_TTL_SECONDS` in [`sources/loader.py`](sources/loader.py)). Conversation history on the Recommendations page is session-scoped and not affected by the cache.

### Backtesting the taste ranker

`backtest.py` measures how well the taste ranker (`core/taste.py`) actually predicts held-out ratings,
instead of trusting the shrinkage/weight/quality constants on eyeball alone. It repeatedly holds out a
random slice of your rated films, retrains an affinity profile on the rest, and reports the held-out
Spearman rank correlation and top-vs-bottom-quartile rating lift — both compared against a quality-prior-only
baseline (i.e. "just trust Letterboxd's community rating"), so it's clear whether the ranker earns its keep.
See [`core/backtest.py`](core/backtest.py) for the full methodology. Like other data-dependent commands,
it requires `OUTPUT_PATH` (real ratings data) to be set.

```bash
uv run --no-sync --directory cinema_dashboard python backtest.py            # metrics for the current constants
uv run --no-sync --directory cinema_dashboard python backtest.py --sweep    # grid-search SHRINKAGE_K / cast-weight / QUALITY_WEIGHT
```

## LLM evals

The Recommendations chat is rule-bound to only reference watchlist titles and FR streaming providers from the lists injected into its system prompt. To verify that the live model actually respects those rules, `tests/evals/` ships a small DeepEval-based regression suite of bait prompts (e.g. *"Recommend me Oppenheimer for tonight."*, *"Is Parasite on Disney+?"*, *"Surprise me with a Bong Joon-ho-style movie"*). Two deterministic metrics flag violations:

- **`FilmSetMembershipMetric`** — fails if the output names a film outside the allowed set.
- **`StreamingClaimMetric`** — fails if the output ties a film to a provider not in the allowed `(film, provider)` set. The post-mention scan window is truncated at the next allowed-film mention so providers attributed to a later film in the same sentence don't falsely pin onto the current one.

Both metrics ignore mentions inside a **refusal context** (*"I can't recommend Oppenheimer"*, *"Past Lives isn't on Netflix"*) so a principled denial doesn't count as a hallucination. The refusal logic is regex-based and unit-tested separately in `tests/evals/test_metrics.py`, which runs in the default `pytest` suite and does **not** hit the Gemini API.

The system prompt also enforces a **refusal flow**: when the user asks about a film, director, or provider not in the provided lists, the model must respond in 1–2 sentences, acknowledge it isn't in the watchlist/streaming list, and **ask** whether the user wants a recommendation from what is available — without auto-dumping the watchlist.

### Two eval paths: prompt-only and tool-enabled

`test_chat_stays_in_bounds` runs the goldens **without** tool declarations, so it verifies the injected prompt alone keeps the model in its closed set. `test_chat_tool_layer` runs `TOOL_GOLDENS` **with** the `top_matches` / `showtimes_query` / `streaming_query` declarations and the same bounded round-trip dispatch loop `chat.ui._ask_gemini` uses, recording every call so tool assertions are possible. `search_theater` is deliberately excluded from the eval dispatch — it hits the live Allocine site and writes to `theaters.csv`, which a read-only eval must not do.

That second path exists for one reason: `_streaming_context` caps the streaming block at `STREAMING_CONTEXT_TOP_N` films, so anything below the cap is answerable *only* if the tool fires. `OVERCAP_STREAMING_GOLDEN` renders 60 films through the real `_streaming_context` and asks about the one ranked 55th — the regression test for that whole narrow-the-block/recover-with-a-tool design. It adds a third deterministic metric, **`ToolCorrectnessMetric`** (compares the recorded calls against the golden's `expected_tools`, no judge tokens), alongside the reused `StreamingClaimMetric`.

Two LLM-as-judge metrics — **`FaithfulnessMetric`** and **`AnswerRelevancyMetric`** — are opt-in behind `--judge` so judge tokens aren't burned on every run. Both are pointed at a `GeminiModel` judge rather than DeepEval's default, which would want an `OPENAI_API_KEY` this project never asks for.

The suite is deselected from the default `pytest` run (every file is tagged `pytest.mark.evals` and `pyproject.toml` uses `addopts = "-m 'not evals'"`) because each case hits the live Gemini API.

```bash
uv run --no-sync --directory cinema_dashboard pytest tests/evals/ -m evals                       # full suite
uv run --no-sync --directory cinema_dashboard pytest tests/evals/ -m evals -k outside_film_bait  # one golden
uv run --no-sync --directory cinema_dashboard pytest tests/evals/ -m evals --judge               # + the judge metrics
```

Requires `GEMINI_API_KEY`; the suite skips itself when unset. Running the full suite twice in quick succession can trip the Gemini free tier's 15 req/min quota (`429 RESOURCE_EXHAUSTED`) — that's rate limiting, not a failure. To add a new failure mode, append a `Golden(...)` entry to `tests/evals/goldens.py` — keep the dataset tight and curated rather than sprawling.

## Troubleshooting

**"OUTPUT_PATH is not set"** — add it to the workspace-root `.env`.

**"Watchlist data not found"** — run `uv run --no-sync --directory movies_management python main.py --username <user>` from the workspace root.

**"Showtimes data not found"** — run the Allocine scraper: `uv run --directory ../Allocine-Showtimes-Scraping python main.py`.

**"No upcoming showtimes for your watchlist"** — either your watchlist is empty, no watchlist movies are currently showing, or the showtimes data is stale (re-run the scraper).

**Map shows no theaters** — addresses are geocoded once via Nominatim (rate-limited, free) and cached to `data/theaters_geo.parquet`. To force re-geocoding, delete the parquet. Theaters whose addresses Nominatim can't resolve are kept in tables but skipped on the map.

**`Cmd+K` doesn't open the assistant** — the keyboard shortcut is a small JS snippet injected via `st.iframe`; if the binding fails on your browser, the "✦ Ask AI" button in the sidebar opens the same dialog.

**Theme looks broken / fonts not loading** — `assets/styles.css` imports Inter and Playfair Display from Google Fonts. Browsers without internet access render the dashboard with system fallbacks; the layout still works.

**"GEMINI_API_KEY is not set"** — add your Gemini API key to the workspace-root `.env`. Create one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

**"No upcoming showtimes for your watchlist"** (Recommendations page) — either no watchlist movies are currently showing, or the showtimes data is stale. Re-run both scrapers to refresh.

## Known limitations

- Only covers Allocine (French cinemas). Other regions require a different showtimes source.
- Watchlist-to-showtimes matching joins Allocine's display title against the normalised TMDB French title (`french_title`) *and* the original title, **confirmed by director overlap**. A title match is kept only when both sources agree on at least one director, so films whose Allocine display title matches neither watchlist title — or whose director metadata is missing on either side — may not be matched. This is a deliberate precision-first trade-off: showing a wrong film's screening (e.g. the wrong "Nosferatu") is worse than missing one.
- Data is only as fresh as the last scraper run.
