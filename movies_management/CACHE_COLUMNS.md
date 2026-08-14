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
| 1 | **Letterboxd** — HTML scrape via `letterboxdpy`'s `Movie(slug)` | 17 columns: identifiers, core info, media, genres/themes — **plus `studio`/`country`/`language` while `USE_TMDB_TERRITORIES` is off (the default)** | The whole row (`_fetch_movie` returns `None`, the film is skipped) |
| 2 | **TMDB** — 2 REST calls on one shared `httpx.AsyncClient` | 7 columns: `french_title`, `cast`, the 4 crew columns, `trailer_url` — **and the 5 territory columns once `USE_TMDB_TERRITORIES` is on** | `None` per column — never raises into the batch |
| 3 | **This pipeline** — computed locally, no network | 3 columns: `slug`, `integration_date`, `source` | n/a |

A TMDB failure degrades columns; a Letterboxd failure drops the film. That asymmetry is deliberate: without
Letterboxd there is no slug, no title and no cache row to attach anything to. The flip side is that TMDB now
owns 7 of the 33 columns today, and 12 once the territory flag is flipped — at which point three of the
taste ranker's dimensions depend on it rather than one. `main.py` warns at startup, and says which.

## Column map

33 columns, all required by the contract. Nothing is dynamic any more: `studio`/`country`/`language` are
seeded on every row rather than expanded from whatever detail types a page happens to carry, and
`origin_country`/`original_language` are seeded beside them, so the contract enforces all 33 regardless of
which producer is filling the territory five. Coverage is the share of non-null rows measured against the
real cache.

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
| `french_title` | **TMDB** | `GET /movie/{tmdb_id}?language=fr-FR` → `title` (the *only* locale-bearing call — see [Quirks](#quirks-that-bite)) | 100.0% |
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
| `trailer_url` | **TMDB** | the bundle's `videos` block (`include_video_language=fr,en,null`) → best official YouTube trailer | 54.5% |

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

`GET /movie/{tmdb_id}?append_to_response=credits,videos` fills these five columns *and* `trailer_url` in a
single round-trip (`_fetch_bundle` → `TmdbColumns`, carrying the `Credits` NamedTuple whose field names are
the cache column names). Letterboxd's own cast/crew are deliberately not read — see
[`README.md`](README.md#1-data_letterboxdparquet) for the agreement measurements behind each job filter.

| Column | From the `credits` block | Filter | Coverage |
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
> it null and degrades both. Turning on `USE_TMDB_TERRITORIES` adds `country` and `language` to that
> exposure, taking it to three ranker dimensions. `main.py` warns at startup and says which case applies.

### Territories & provenance — two producers behind one flag

**These five columns are mid-migration, and `USE_TMDB_TERRITORIES` (default `false`) picks the producer.**
Both paths stay wired up and both write all five columns, so **the parquet schema does not depend on the
flag** — only the values do. That is the whole point: the schema can land, and the contract can require it,
before a single value moves.

| Column | `USE_TMDB_TERRITORIES=false` (default) | `USE_TMDB_TERRITORIES=true` |
|---|---|---|
| `studio` | Letterboxd, `type == "studio"` (98.9%) | `production_companies[].name` (~99%) |
| `country` | Letterboxd, `type == "country"` (99.7%) | `production_countries[].name`, ~1.47/film (~100%) |
| `origin_country` | **null placeholder** | `origin_country` — bare ISO 3166-1 codes, ~1.14/film (100%) |
| `language` | Letterboxd, `type == "language"` (100%) | `spoken_languages[].english_name` (~100%) |
| `original_language` | **null placeholder** | `original_language` — one ISO 639-1 code (100%) |

Under the default, `_fetch_movie` seeds all five as `None` and Letterboxd's `**details_by_type` expansion
then overwrites the first three; `origin_country`/`original_language` have no Letterboxd equivalent and stay
null. **A null in those two is "not migrated yet", not "TMDB had nothing"** — they are the one place in this
file where a null carries no information about the film.

With the flag on, the three Letterboxd detail types are filtered out (`_TMDB_OWNED_DETAIL_TYPES`) so the
seeds survive, and `_parse_territories` fills all five from the base movie payload
`GET /movie/{tmdb_id}?append_to_response=credits,videos` already returns — **zero extra cost**, no second
call, no extra append.

> ⚠️ **`country` and `origin_country` are different fields, not two spellings of one.**
> `production_countries` is the full co-production territory list; `origin_country` is the nationality of the
> production. Measured on 400 films they are identical 73.7% of the time and **never disjoint**; where they
> differ, `origin ⊂ production` in 96 of 105 cases. `country` keeps the production list for two independent
> reasons: in all 9 reverse cases the historical Letterboxd value tracked *production*
> (`tmdb_id=763` → `origin=[NZ,US]`, `production=[NZ]`, cached `New Zealand`), and the taste backtest ranked
> `origin_country` as the only losing variant (spearman 0.6668 vs 0.6682). `origin_country` is carried but is
> **not yet a taste dimension** — adding it to `_DIM_COLUMNS` is a separate, backtest-gated change.

Two vocabulary traps. TMDB ships **no display names for `origin_country`** anywhere in the payload, so it is
stored as codes — don't "fix" that with a lookup table without first deciding what the ranker keys on. And
`spoken_languages` is read via `english_name`, not `name`: `name` is the endonym (`Français`, `日本語`), while
the cache has always carried the English form and the affinity keys depend on it.

#### What flipping the flag changes, measured live on 120 real films

- **Names change, and that is what makes it one-way in practice.** Raw exact agreement between TMDB's values
  and the cached Letterboxd ones is only 46.7% for `country` and 91.7% for `language` — almost entirely
  `USA` → `United States of America`, `UK` → `United Kingdom`, `Chinese` → `Mandarin` (semantic: Letterboxd
  collapses Mandarin into "Chinese" while keeping Cantonese separate). Normalised for those aliases the two
  sources agree 99.5%/99.7%. TMDB names are used verbatim, with no alias table, so **flip the flag and then
  backfill every row in one pass** — a cache half-written under each setting splits `USA` and
  `United States of America` into two taste-affinity buckets. The flag is cheap to flip *back* (nothing
  gains or loses a column); it is the *values* that don't want to be mixed.
- **The language-duplication quirk goes away.** Letterboxd lists Primary Language *and* Spoken Languages
  under the same URL path, so both collapse into one un-deduped column: 40 of 120 sampled rows repeat a value
  (*Frankenstein* → `"English, Danish, English, French"`). TMDB's `spoken_languages` is a proper list —
  0 of 120 duplicated.
- **`origin_country` and `original_language` stop being placeholders.** `original_language` was previously
  only recoverable as "first entry of `language`", 98.5% reliably; `origin_country` had no equivalent at all.
- **`studio` barely moves** (95.0% exact, 0.979 jaccard); the residual is TMDB's catalogue being fresher
  (`One Cool Pictures` → `One Cool Films`, legal-suffix stripping).

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
  │     └─ 17 columns (+ studio/country/language unless the territory flag is on)
  │        + the 12 TMDB-fillable columns seeded as None
  │        (studio/country/language detail types dropped — see _TMDB_OWNED_DETAIL_TYPES)
  │
  └─ nested TaskGroup on one shared httpx.AsyncClient  [TMDB, 2 concurrent calls]
        ├─ _fetch_french_title  → french_title            [?language=fr-FR]
        └─ _fetch_bundle        → cast, directors, producers, writers, composers,
                                  trailer_url,            [?append_to_response=credits,videos]
                                  studio, country, origin_country,
                                  language, original_language
  │
  └─ integration_date stamped → concat into cache → source assigned → single validated write
```

`_fetch_movie` seeds all twelve TMDB-fillable columns as `None` in its return dict before `_fetch_all`
overwrites them (the territory five only when `USE_TMDB_TERRITORIES` is on; otherwise Letterboxd's expansion
supplies three of them and the other two stay null). That is what guarantees **column presence** even with no TMDB key at all — the columns exist and are
null, rather than vanishing from the frame and failing contract validation. It is also what let the five
territory columns join `required_columns`: presence is unconditional, values are not.

> ⚠️ **The seeding only holds because the three TMDB-owned detail types are filtered out of
> `**details_by_type`.** That expansion is the *last* entry in `_fetch_movie`'s dict literal, so a surviving
> Letterboxd `country` would overwrite the TMDB value seeded above it — right value fetched, wrong value
> written, nothing raised anywhere. `_TMDB_OWNED_DETAIL_TYPES` exists solely to prevent that, and
> `test_tmdb_owned_detail_types_are_dropped_not_expanded` pins it.

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

- **`language=fr-FR` localises TMDB *person names*, which is why the credits are a separate call.** It is
  the reason the two TMDB requests cannot be folded into one `append_to_response`. Measured on 120 films:
  the parameter changes 5 director sets and 15 top-8 cast lists — `Ho Meng-Hua` → `何夢華`,
  `Baku Kinoshita` → `木下麦`, `Marcell Jankovics` → `Jankovics Marcell` (locale name order). A control run
  rules out response variance (two identical calls agree 120/120; the appended block matches the standalone
  endpoint 120/120). Those names feed the taste ranker's highest-weighted dimension *and*
  `cinema_dashboard`'s token-containment director confirmation against Allocine, which is Latin-script — so
  hanging the credits off the French call would silently drop films from the watchlist↔showtimes join.
  Everything else is locale-invariant: crew `job` strings, video results, `production_countries`,
  `spoken_languages` and `production_companies` were all identical across 60 films, and only `title`
  differed (44/60). **Never add `language` to `_get_tmdb_bundle`.**
- **`?language=fr-FR` is not "the French translation" — it is the French *release* title.** Reconstructing
  it from an `append_to_response=translations` block reproduces it on only 93.25% of 400 films, and the
  misses are semantic rather than cosmetic: TMDB's fr-FR resolution returns `The Nice Guys`, `A Serious Man`
  and `Room` (how those films were released in France) where the translations block gives the literal
  `Les bons gars`, `Un homme sérieux`, `Room: le monde de Jack`. Since `french_title` exists to match
  Allocine's display titles, the release title is the correct semantics — don't "save a request" by
  switching to `translations`.
- **Flipping `USE_TMDB_TERRITORIES` mixes two spellings in one cache, and age will not fix it.** Rows
  rewritten after the flip read `United States of America` while everything else still reads `USA`, and the
  taste ranker treats those as two unrelated affinity keys. `find_stale_slugs` would take a full
  `LETTERBOXD_DAYS_TO_UPDATE` cycle to converge, so this is the one migration here that is not self-healing:
  flip the flag and run the one-pass backfill in the same sitting. Flipping *back* is safe at any time — no
  column appears or disappears — but leaving the cache half-written under each setting is not.
- **`origin_country` and `original_language` exist on no row cached before the move, and it is `main.py`'s
  own *write* that this breaks — not any read.** Nothing reads this parquet against the contract: the
  dashboard's `cinema_dashboard/sources/loader.py` uses a plain `pd.read_parquet`, so a cache missing the two
  columns degrades it silently rather than failing it. The enforcement point is `write_parquet_validated` at
  the end of a `main.py` run, which raises `SchemaValidationError: missing required columns
  ['origin_country', 'original_language']` against a pre-move cache.
  Two things add the columns, and **neither is guaranteed to run**: `get_letterboxd_data` introduces them by
  concat when there is at least one *new* slug to fetch, and `refresh_letterboxd_data`'s pre-seed loop
  (guarded by `test_refresh_adds_columns_missing_from_target_cache`) when there is at least one *stale* one.
  A run with no new films and nothing aged past `LETTERBOXD_DAYS_TO_UPDATE` reaches the write with the
  frame untouched and hard-fails. Recover with `--reset_database`, a lower staleness threshold, or the
  backfill — deliberately no defensive seeding in the pipeline, since all three exist.
  Note this is **independent of `USE_TMDB_TERRITORIES`**: `_fetch_movie` seeds the two columns in both flag
  positions, so any run that touches a row converges the schema without migrating a single value. The flag
  decides what the columns *contain*, never whether they exist.
- **`slug` is the requested slug, not the canonical one.** `_fetch_movie` stores its own argument, while
  `letterboxd_url` is the page's post-redirect URL. So an alias slug produces a row whose `slug` and
  `letterboxd_url` disagree.
- **`slug` is the only unique key.** `movie_id` has 14 duplicate rows and `tmdb_id` 16, because Letterboxd
  carries several film entries over one underlying record: spelling aliases
  (`favorites-of-the-moon` / `favourites-of-the-moon`, both `movie_id` 17951) and multi-part releases
  (`war-and-peace-1965` / `-1967` / `-1968`, all `movie_id` 33006 / `tmdb_id` 29266, three distinct pages).
  This is why `cinema_dashboard` routes on `slug`.
- **A new Letterboxd detail type would silently overwrite a fixed column.** `**details_by_type` is expanded
  *last* in `_fetch_movie`'s dict literal, so a detail type named after an existing key wins. This is no
  longer hypothetical — it is precisely why `studio`/`country`/`language` had to be *dropped* from the
  expansion (`_TMDB_OWNED_DETAIL_TYPES`) rather than merely overwritten by the TMDB values. Letterboxd still
  serves all three types; anything that puts them back reverts the column move without failing a test that
  isn't looking for it.
- **Physical column order is not declaration order.** `cast`, `trailer_url` and `composers` sit at the end
  of the parquet because they were appended to pre-existing rows by `refresh_letterboxd_data`'s pre-seed
  loop, not written in the dict-literal position they occupy in `_fetch_movie`.

## Adding a new cache column

Five places, and skipping any one of them fails quietly rather than loudly:

1. **`modules/get_letterboxd_data.py`** — add the key to `_fetch_movie`'s return dict (seeded `None` if a
   TMDB column), and assign it in `_fetch_all` if it comes from TMDB. Read it off `_fetch_bundle`'s
   existing round-trip rather than adding a third TMDB call: the bundle already fetches the whole movie
   detail payload, and `append_to_response` takes up to 20 blocks, so `keywords`, `release_dates`,
   `external_ids` and friends cost nothing extra. Only a field that needs `language=fr-FR` belongs on the
   other call.
2. **`packages/contracts/src/contracts/data_letterboxd.py`** — add it to `required_columns` if it is
   guaranteed on every row (anything seeded `None` in `_fetch_movie` is; a column left to
   `**details_by_type` would not be). The two cache writes validate against this; a missing required column
   raises `SchemaValidationError`. Note the ordering that implies: promoting a column to required makes the
   *next write* fail against a cache that predates it, and a run with no new and no stale slugs never adds
   the column — so land the backfill with the promotion, not after it.
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

*Letterboxd-sourced coverage figures measured 2026-08-11 against the real cache (6,759 rows). The territory
columns' figures were measured 2026-08-14 on a 120-film live TMDB sample, since no cached row carried them
yet. They will drift; the sources above will not.*
