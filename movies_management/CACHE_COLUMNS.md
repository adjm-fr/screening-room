# Cache column provenance — `data_letterboxd.parquet`

Every column in the movie metadata cache, and exactly where it comes from.

[`README.md`](README.md#1-data_letterboxdparquet) documents what each column *means*. This file documents
what *produces* it — which upstream system, which endpoint or DOM element, which function in this package —
so that "why is this column null / stale / suddenly different?" is a lookup, not an investigation.

The cache is written in exactly two places, both validated against `contracts.DATA_LETTERBOXD`:

- [`main.py`](main.py) — the user-data pipeline (`--username`)
- [`modules/allocine_enrichment.py`](modules/allocine_enrichment.py)'s `enrich_cache_from_showtimes` — the
  Allocine expansion (`--enrich-from-allocine`)

Both build their rows through the *same* fetch path (`modules/get_letterboxd_data.py`), so the provenance
below holds regardless of which pipeline ingested a row.

## The three producers

| # | Producer | What it fills | Fails to |
|---|----------|---------------|----------|
| 1 | **Letterboxd** — HTML scrape via `letterboxdpy`'s `Movie(slug)` | 20 columns: identifiers, core info, media, genres/themes, and the dynamic detail columns | The whole row (`_fetch_movie` returns `None`, the film is skipped) |
| 2 | **TMDB** — 3 REST calls on one shared `httpx.AsyncClient` | 7 columns: `french_title`, `cast`, the 4 crew columns, `trailer_url` | `None` per column — never raises into the batch |
| 3 | **This pipeline** — computed locally, no network | 3 columns: `slug`, `integration_date`, `source` | n/a |

A TMDB failure degrades columns; a Letterboxd failure drops the film. That asymmetry is deliberate: without
Letterboxd there is no slug, no title and no cache row to attach anything to.

## Column map

31 columns in the real cache (6,759 rows, measured 2026-08-11): 28 required by the contract, plus the 3
dynamic detail columns. Coverage is the share of non-null rows on that same snapshot.

### Identifiers

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `slug` | pipeline | The **requested** slug — a dict key from the user's films/watchlist, or the output of `allocine_enrichment.resolve_slug_from_allocine_tuple`. Not read back off the page (see [Quirks](#quirks-that-bite)) | 100% |
| `movie_id` | Letterboxd | `span.block-flag-wrapper a[data-report-url]`, digits joined | 100% |
| `letterboxd_url` | Letterboxd | The profile page's **post-redirect** canonical URL | 100% |
| `imdb_id` | Letterboxd | `a[data-track-action="IMDb"]` href, `tt\d+` | 99.8% |
| `imdb_url` | Letterboxd | `a[data-track-action="IMDb"]` href | 99.8% |
| `tmdb_id` | Letterboxd | `a[data-track-action="TMDB"]` href, `movie/(\d+)` | 100.0% (3 rows null) |
| `tmdb_url` | Letterboxd | `a[data-track-action="TMDB"]` href | 100% |

`tmdb_id` is scraped from **Letterboxd**, not fetched from TMDB — it is the key that unlocks producer 2, so
the 3 rows without one get all 7 TMDB columns as null regardless of `TMDB_API_KEY`.

### Core information

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `title` | Letterboxd | `h1.primaryname span.name`, falling back to `h1.filmtitle` | 100% |
| `french_title` | **TMDB** | `GET /movie/{tmdb_id}?language=fr-FR` → `title` | 100.0% |
| `original_title` | Letterboxd | `h2.originalname` — absent when it equals `title` | 33.0% |
| `release_year` | Letterboxd | `span.releasedate`, falling back to JSON-LD `releasedEvent[0].startDate` | 100% |
| `runtime` | Letterboxd | `p.text-footer`, digits joined (minutes; stored as `float64`) | 100.0% |
| `tagline` | Letterboxd | `.tagline` | 70.4% |
| `description` | Letterboxd | `<meta name="description">` | 99.8% |
| `letterboxd_avg_rating` | Letterboxd | `span.average-rating`, falling back to JSON-LD `aggregateRating.ratingValue` (0–5 scale) | 97.3% |

`original_title`'s 33% is **not** missing data — a null means "same as `title`".

### Media

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `poster_url` | Letterboxd | JSON-LD `image`, query string stripped | 100.0% |
| `banner_url` | Letterboxd | `div#backdrop[data-backdrop2x]`, falling back to `[data-backdrop]`, query string stripped | 86.8% |
| `trailer_url` | **TMDB** | `GET /movie/{tmdb_id}/videos?include_video_language=fr,en,null` → best official YouTube trailer | 54.5% |

Letterboxd carries its own trailer link (`p.trailer-link`); it is deliberately **not** used — TMDB's is
language-rankable (fr → en → any, `_TRAILER_LANGUAGE_PRIORITY`).

### Genres & themes

All three are the same Letterboxd element, split on the link's URL path segment (`type`):

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `genres` | Letterboxd | `div#tab-panel-genres` links where `type == "genre"` | 99.8% |
| `themes` | Letterboxd | same block, `type == "theme"` | 68.7% |
| `mini_themes` | Letterboxd | same block, `type == "mini-theme"` | 69.5% |

### Cast & crew — all TMDB, all one request

`GET /movie/{tmdb_id}/credits` fills five columns in a single round-trip (`_fetch_credits` → the `Credits`
NamedTuple, whose field names are the cache column names). Letterboxd's own cast/crew are deliberately not
read — see [`README.md`](README.md#1-data_letterboxdparquet) for the agreement measurements behind each
job filter.

| Column | From `/credits` | Filter | Coverage |
|---|---|---|---|
| `cast` | `cast[:8]` → `name` | Top 8 by TMDB billing order | 99.2% |
| `directors` | `crew` → `name` | `job == "Director"` | 100.0% |
| `producers` | `crew` → `name` | `job == "Producer"` (narrower than Letterboxd's list) | 92.4% |
| `writers` | `crew` → `name` | `job in {"Writer", "Screenplay"}` | 96.0% |
| `composers` | `crew` → `name` | `job == "Original Music Composer"` | 78.6% |

All five are comma-joined, deduped (TMDB lists a person once *per job*), and `None` when the film has no
`tmdb_id` or `TMDB_API_KEY` is unset.

> ⚠️ `directors` is the taste ranker's highest-weighted dimension **and** what confirms `cinema_dashboard`'s
> watchlist↔showtimes join. It moved off Letterboxd onto TMDB, so running without `TMDB_API_KEY` now leaves
> it null and degrades both. `main.py` warns about this at startup.

### Dynamic detail columns

Not fixed keys — `_fetch_movie` expands `**details_by_type` from whatever detail types the film's page
carries, grouped from `div#tab-panel-details` links by URL path segment. In practice Letterboxd emits
exactly three types, confirmed against all 6,759 cached rows and by live fetch:

| Column | Source | Coverage |
|---|---|---|
| `studio` | Letterboxd, `type == "studio"` | 98.9% |
| `country` | Letterboxd, `type == "country"` | 99.7% |
| `language` | Letterboxd, `type == "language"` | 100% |

Because they are dynamic, they are deliberately **excluded from `contracts.DATA_LETTERBOXD`** — present on
almost every row, guaranteed on none. Consumers must treat them as optional.

### Provenance / bookkeeping

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `integration_date` | pipeline | `datetime.now().date()` at fetch time, set by `get_letterboxd_data` (new rows) and `refresh_letterboxd_data` (refreshed rows). Drives the staleness check in `find_stale_slugs` | 100% |
| `source` | pipeline | `ratings` / `watchlist` reconciled on every `--username` run by `utils.assign_cache_source`; `allocine_showtimes` stamped only by `enrich_cache_from_showtimes` | 100% |

Current split: 4,123 `ratings` / 2,283 `watchlist` / 353 `allocine_showtimes`.

## How a row gets filled

```
slug (from user lists, or resolved from an Allocine tuple)
  │
  ├─ asyncio.to_thread → _fetch_movie(slug)          [Letterboxd, blocking scrape]
  │     └─ 20 columns + the 7 TMDB columns seeded as None
  │
  └─ nested TaskGroup on one shared httpx.AsyncClient  [TMDB, 3 concurrent calls]
        ├─ _fetch_french_title  → french_title
        ├─ _fetch_credits       → cast, directors, producers, writers, composers
        └─ _fetch_trailer       → trailer_url
  │
  └─ integration_date stamped → concat into cache → source assigned → single validated write
```

`_fetch_movie` seeds all seven TMDB columns as `None` in its return dict before `_fetch_all` overwrites
them. That is what guarantees **column presence** even with no TMDB key at all — the columns exist and are
null, rather than vanishing from the frame and failing contract validation.

## Refresh & backfill — which source re-runs

A refresh re-runs **both** producers for the slug: `refresh_letterboxd_data` calls the same `_fetch_all`, so
every column above is refetched, not just the stale one. **Age is the only trigger**, capped per run:

1. **Age** — `find_stale_slugs`: `integration_date` older than `LETTERBOXD_DAYS_TO_UPDATE` (default 365),
   bounded by `LETTERBOXD_REFRESH_LIMIT` (default 1000).

> ⚠️ **A null column is not a refresh trigger, by design.** Nulls here are ambiguous: `composers` is
> legitimately null on ~21% of films (no original score), `trailer_url` on 45%, `tagline` on 30%. Wiring any
> of them — or `cast` — into the run would re-queue a large slice of the cache every run, forever, burning
> the 1000-slug budget. Backfilling a new column onto old rows is an ad-hoc script's job (see below).

## Quirks that bite

- **`language` repeats the primary language.** Letterboxd's details tab lists Primary Language *and* Spoken
  Languages, both linking under `/films/language/`, so both collapse into one column and `_fetch_movie`'s
  `", ".join(names)` does not dedupe. 32.2% of non-null rows repeat a value — e.g. *Frankenstein* →
  `"English, Danish, English, French"`. The first entry is the primary language. `studio`, `country`,
  `genres` and `themes` do not have this problem.
- **`slug` is the requested slug, not the canonical one.** `_fetch_movie` stores its own argument, while
  `letterboxd_url` is the page's post-redirect URL. So an alias slug produces a row whose `slug` and
  `letterboxd_url` disagree.
- **`slug` is the only unique key.** `movie_id` has 14 duplicate rows and `tmdb_id` 16, because Letterboxd
  carries several film entries over one underlying record: spelling aliases
  (`favorites-of-the-moon` / `favourites-of-the-moon`, both `movie_id` 17951) and multi-part releases
  (`war-and-peace-1965` / `-1967` / `-1968`, all `movie_id` 33006 / `tmdb_id` 29266, three distinct pages).
  This is why `cinema_dashboard` routes on `slug`.
- **A new Letterboxd detail type would silently overwrite a fixed column.** `**details_by_type` is expanded
  *last* in `_fetch_movie`'s dict literal, so a detail type named after an existing key would win. Harmless
  today (the three types collide with nothing), but it is why a new detail type should be checked, not
  assumed.
- **Physical column order is not declaration order.** `cast`, `trailer_url` and `composers` sit at the end
  of the parquet because they were appended to pre-existing rows by `refresh_letterboxd_data`'s pre-seed
  loop, not written in the dict-literal position they occupy in `_fetch_movie`.

## Adding a new cache column

Five places, and skipping any one of them fails quietly rather than loudly:

1. **`modules/get_letterboxd_data.py`** — add the key to `_fetch_movie`'s return dict (seeded `None` if a
   TMDB column), and assign it in `_fetch_all` if it comes from TMDB. Reuse `_fetch_credits`' round-trip
   rather than adding a fourth TMDB call where possible.
2. **`packages/contracts/src/contracts/data_letterboxd.py`** — add it to `required_columns` if it is
   guaranteed on every row (dynamic detail columns are not; anything seeded `None` in `_fetch_movie` is).
   The two cache writes validate against this; a missing required column raises `SchemaValidationError`.
3. **`refresh_letterboxd_data`** — nothing to do *if* you leave the pre-seed loop alone.
   `DataFrame.update()` silently ignores columns absent from the target, so the
   `refresh_df.columns.difference(data_df.columns)` loop is what lets refreshed rows gain a column added
   after they were cached. Removing it means new columns stay null on old rows forever, with no error.
4. **This file and [`README.md`](README.md#1-data_letterboxdparquet)** — a column with no documented source
   is the problem this file exists to prevent.
5. **`movies_management/tests/`** — a regression test that the column survives a refresh onto rows cached
   before it existed. `test_get_letterboxd_data.py::test_refresh_adds_columns_missing_from_target_cache` is
   the pattern to copy.

Backfilling the column onto existing rows is then either a one-off ad-hoc script (feed the affected slugs
straight to `refresh_letterboxd_data` — do **not** add a null-column trigger to `main.py`, see the warning
above) or `--reset_database`. Rows also gain it on their own as they age past
`LETTERBOXD_DAYS_TO_UPDATE`.

---

*Coverage figures measured 2026-08-11 against the real cache (6,759 rows). They will drift; the sources
above will not.*
