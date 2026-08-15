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
| 1 | **Letterboxd** — HTML scrape via `letterboxdpy`'s `Movie(slug)` | 17 columns: identifiers, core info, media, themes | The whole row (`_fetch_movie` returns `None`, the film is skipped) |
| 2 | **TMDB** — 2 REST calls on one shared `httpx.AsyncClient` | 14 columns: `french_title`, `cast`, the 4 crew columns, `trailer_url`, the 5 territory columns, `genres`, `keywords` | `None` per column — never raises into the batch |
| 3 | **This pipeline** — computed locally, no network | 3 columns: `slug`, `integration_date`, `source` | n/a |

A TMDB failure degrades columns; a Letterboxd failure drops the film. That asymmetry is deliberate: without
Letterboxd there is no slug, no title and no cache row to attach anything to. **`TMDB_API_KEY` is required**
(`Settings` fails fast at startup without it) precisely because TMDB now owns 14 of the 34 columns, including
four of the taste ranker's dimensions (`directors`, `genres`, `country`, `language`) and the field that
confirms `cinema_dashboard`'s watchlist↔showtimes join (`directors`).

The territories (`studio`/`country`/`origin_country`/`language`/`original_language`) and taxonomy
(`genres`/`keywords`) columns were migrated from Letterboxd to TMDB behind a `TMDB_COLUMN_GROUPS` flag,
backfilled across the whole cache in one pass on 2026-08-15, and measured ranker-neutral (spearman +0.0001,
quartile lift +0.0008 against the pre-migration cache on identical seeded splits — see the taste-ranker
memory for the full before/after). The flag has since been removed: TMDB is now the columns' only producer,
unconditionally, with no Letterboxd fallback and no setting to revert to one.

## Column map

34 columns, all required by the contract. `studio`/`country`/`language` are filtered out of Letterboxd's
`**details_by_type` expansion (`_TMDB_OWNED_DETAIL_TYPES`) rather than read from it, and every TMDB-sourced
column is seeded `None` in `_fetch_movie`'s return dict so the key exists even before `_fetch_all` fills it —
that is what lets the contract require all 34 unconditionally. Coverage is the share of non-null rows
measured against the real cache (6,763 rows, post-backfill).

### Identifiers

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `slug` | pipeline | The **requested** slug — a dict key from the user's films/watchlist, or the output of `allocine_enrichment.resolve_slug_from_allocine_tuple`. Not read back off the page (see [Quirks](#quirks-that-bite)) | 100% |
| `movie_id` | Letterboxd | `span.block-flag-wrapper a[data-report-url]`, digits joined | 100% |
| `letterboxd_url` | Letterboxd | The profile page's **post-redirect** canonical URL | 100% |
| `imdb_id` | Letterboxd | `a[data-track-action="IMDb"]` href, `tt\d+` | 99.8% |
| `imdb_url` | Letterboxd | `a[data-track-action="IMDb"]` href | 99.8% |
| `tmdb_id` | Letterboxd | `a[data-track-action="TMDB"]` href, `movie/(\d+)` | 100.0% (2 rows null) |
| `tmdb_url` | Letterboxd | `a[data-track-action="TMDB"]` href | 100% |

`tmdb_id` is scraped from **Letterboxd**, not fetched from TMDB — it is the key that unlocks producer 2, so
the 2 rows without one get all 14 TMDB columns as null regardless of `TMDB_API_KEY`. Both are films TMDB
catalogues under `/tv/` rather than `/movie/` (`histoires-du-cinema-1989`, `the-sorrow-and-the-pity-1969`
— Letterboxd's `movie/(\d+)` regex never matches a `/tv/` link), so they keep their native Letterboxd
`studio`/`country`/`language`/`genres` values indefinitely: `DataFrame.update()` in `refresh_letterboxd_data`
only writes non-null values, and with no `tmdb_id` these two rows will never get one.

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

`themes`/`mini_themes` are Letterboxd's, one element split on the link's URL path segment (`type`).
`genres` and `keywords` are both TMDB's — `genres` a plain field of the base movie payload, `keywords` an
appended block — read off the same `GET /movie/{tmdb_id}?append_to_response=credits,videos,keywords`
round-trip `_fetch_bundle` already makes for the cast/crew columns, so neither costs an extra request:

| Column | Source | Upstream origin | Coverage |
|---|---|---|---|
| `genres` | **TMDB** | base payload `genres[].name` | 99.8% |
| `themes` | Letterboxd | `div#tab-panel-genres` links, `type == "theme"` | 68.7% |
| `mini_themes` | Letterboxd | same block, `type == "mini-theme"` | 69.5% |
| `keywords` | **TMDB** | appended `keywords.keywords[].name` | 90.9% |

> **Letterboxd's genre list *was* TMDB's genre list, which is why swapping the producer barely moved
> anything.** Measured across all 6,761 cached films with a `tmdb_id` before the swap (Aug 2026, 100% fetch
> success): the two vocabularies were **the same 19 terms**, with none unique to either side; per film the
> sets matched **exactly on 98.18%**, mean Jaccard **0.9934**, and **not one film was disjoint**. The
> one-pass backfill (2026-08-15) confirmed it live: of 6,761 films refetched, 126 (1.86%) had a real set
> change, 3,491 were pure reordering (the string differs, the term set doesn't — invisible to the taste
> ranker, which splits on commas and averages), and the rest were already identical. `genres` therefore has
> **no vocabulary-split hazard** the way `territories` did — there was never a `USA` → `United States of
> America`-shaped rename here.

`keywords` is TMDB's open, crowd-maintained tag space — **14,780 distinct terms** across the pre-migration
sample, **10.42 per film**, and it is emphatically **not** a replacement for `themes`/`mini_themes`:

| | `keywords` (TMDB) | `themes` + `mini_themes` (Letterboxd) |
|---|---|---|
| Coverage | 90.9% | 69.5% |
| Terms/film | 10.42 | 5.35 |
| Distinct terms | 14,780 | 140 |
| Terms on exactly 1 film | 7,087 (47.9%) | 0 |
| Shape | open tags (`dual identity`, `paris`) | curated sentences (`Moving relationship stories`) |

The two vocabularies intersect on **8 terms** — 5.7% of Letterboxd's theme vocabulary. They are adjacent
taxonomies describing the same films in different registers, so both are kept and `keywords` lands in a
column of its own. Folding them into one affinity dimension would double-count the overlap; whether
`keywords` earns a dimension *beside* `themes` is a taste-ranker question, measured separately.

> ⚠️ Keywords carry production metadata as well as content: `woman director` (312 films), `black and white`
> (382), `aftercreditsstinger` (152) and `duringcreditsstinger` (148) all rank in the top 30. Anything built
> on this column has to decide whether those are signal or noise — they are not themes in the Letterboxd
> sense, and `aftercreditsstinger` in particular is a property of the credits reel, not the film.

### Cast & crew — all TMDB, all one request

`GET /movie/{tmdb_id}?append_to_response=credits,videos,keywords` fills these five columns *and*
`trailer_url` in a single round-trip (`_fetch_bundle` → `TmdbColumns`, carrying the `Credits` NamedTuple whose field names are
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
> watchlist↔showtimes join. `TMDB_API_KEY` is required precisely because a missing key would leave it null
> and degrade both — along with `genres`, `country` and `language`, three more ranker dimensions that are
> also TMDB's now. `Settings` fails fast at startup rather than letting the pipeline run degraded.

### Territories & provenance — all TMDB

All five columns are TMDB's, read off the same base movie payload
`GET /movie/{tmdb_id}?append_to_response=credits,videos,keywords` already returns for the cast/crew
columns — **zero extra cost**, no second call, no extra append:

| Column | Upstream origin | Coverage |
|---|---|---|
| `studio` | `production_companies[].name` | 98.9% |
| `country` | `production_countries[].name`, ~1.47/film | 99.7% |
| `origin_country` | `origin_country` — bare ISO 3166-1 codes, ~1.14/film | 99.8% |
| `language` | `spoken_languages[].english_name` | 100% |
| `original_language` | `original_language` — one ISO 639-1 code | 100% |

`_fetch_movie` seeds all five `None` and the three Letterboxd detail types they used to come from
(`studio`/`country`/`language`) are filtered out of `**details_by_type` (`_TMDB_OWNED_DETAIL_TYPES`) so the
seeds always survive to be filled from TMDB — there is no Letterboxd fallback path left in the code.

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

#### What the migration changed, measured on the real 6,761-film backfill (2026-08-15)

Numbers below count films the backfill actually refetched; `new` means the cache had no value at all,
`set_diff` means the term set changed (a real content change), `reorder` means the string changed but the
term set didn't (invisible to the ranker), `kept_lb` means TMDB returned nothing and the old Letterboxd
value survived (the pre-seed loop's `update()` never blanks a value with a null).

| column | new | set_diff | reorder | kept_lb |
|---|---|---|---|---|
| `studio` | 1 | 372 | 2 | 0 |
| `country` | 0 | 4,109 | 3 | 1 |
| `origin_country` | 6,749 | 0 | 0 | 0 |
| `language` | 0 | 588 | 1,879 | 27 |
| `original_language` | 6,761 | 0 | 0 | 0 |

- **`country`'s 4,109 set_diff is almost entirely a rename, not new information.** Raw exact string
  agreement between TMDB's values and the pre-migration Letterboxd ones was measured at 46.7% before the
  backfill, almost entirely `USA` → `United States of America`, `UK` → `United Kingdom`, `Chinese` →
  `Mandarin` (semantic: Letterboxd collapsed Mandarin into "Chinese" while keeping Cantonese separate).
  Post-backfill the cache carries **zero** occurrences of `USA` and 3,544 of `United States of America` —
  the rename converged in the single pass, which is why it had to be one pass: a half-migrated cache would
  have split `USA` and `United States of America` into two taste-affinity buckets, and age alone would not
  have fixed it (`find_stale_slugs` takes a full `LETTERBOXD_DAYS_TO_UPDATE` cycle to touch every row).
- **`language`'s 1,879 reorder is the old duplication quirk being fixed, not new content.** Letterboxd
  listed Primary Language *and* Spoken Languages under the same URL path, so both collapsed into one
  un-deduped column (*Frankenstein* → `"English, Danish, English, French"`, ~32% of sampled rows repeated a
  value). TMDB's `spoken_languages` is a proper list — the string changed, the term set usually didn't.
- **`origin_country`/`original_language` went from placeholder to populated on effectively every row**
  (6,749 and 6,761 of 6,761 respectively) — neither had a Letterboxd equivalent before.
- **`studio` barely moved** (372 set_diff of 6,761, ~5.5%); the residual is TMDB's catalogue being fresher
  (`One Cool Pictures` → `One Cool Films`, legal-suffix stripping).
- **29 rows total kept a Letterboxd value** (`kept_lb` summed) where TMDB had nothing on record — these are
  the only rows still carrying pre-migration values for a populated field, and they will only move if TMDB
  later gains a record and the row is refetched.
- **Ranker-neutral, as designed**: spearman 0.6735 → 0.6736, quartile lift 2.0212 → 2.0220 on identical
  seeded splits, with an unchanged quality-only baseline (0.6012 / 1.8237) proving both runs saw the same
  ratings. The migration moved vocabulary, not signal.

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
  │     └─ 17 columns (identifiers, core info, media, themes)
  │        + the 14 TMDB-fillable columns seeded as None
  │        (studio/country/language detail types dropped — see _TMDB_OWNED_DETAIL_TYPES)
  │
  └─ nested TaskGroup on one shared httpx.AsyncClient  [TMDB, 2 concurrent calls]
        ├─ _fetch_french_title  → french_title            [?language=fr-FR]
        └─ _fetch_bundle        → cast, directors, producers, writers, composers,
                                  trailer_url, genres, keywords,
                                  studio, country, origin_country,     [?append_to_response=credits,videos,keywords]
                                  language, original_language
  │
  └─ integration_date stamped → concat into cache → source assigned → single validated write
```

`_fetch_movie` seeds all fourteen TMDB-fillable columns as `None` in its return dict; `_fetch_all`
unconditionally overwrites all fourteen from the bundle it fetches. That is what guarantees **column
presence** even with no TMDB key at all — the columns exist and are null, rather than vanishing from the
frame and failing contract validation.

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
- **The territories+genres migration was a one-pass backfill for exactly this reason.** A `TMDB_COLUMN_GROUPS`
  flag drove it while it was in flight: enabling a group without an immediate backfill would have meant rows
  rewritten after the flip reading `United States of America` while everything else still read `USA`, and
  the taste ranker treating those as two unrelated affinity keys — `find_stale_slugs` would have taken a
  full `LETTERBOXD_DAYS_TO_UPDATE` cycle to converge on its own. The backfill ran on 2026-08-15 in one pass
  across all 6,761 films with a `tmdb_id`; the flag has since been removed (TMDB is now the unconditional
  producer). This note is historical — there is no longer a setting that can half-migrate the cache.
- **`origin_country` and `original_language` exist on no row cached before the move, and it is `main.py`'s
  own *write* that this breaks — not any read.** Nothing reads this parquet against the contract: the
  dashboard's `cinema_dashboard/sources/loader.py` uses a plain `pd.read_parquet`, so a cache missing the two
  columns degrades it silently rather than failing it. The enforcement point is `write_parquet_validated` at
  the end of a `main.py` run, which raises `SchemaValidationError: missing required columns
  ['origin_country', 'original_language']` against a pre-move cache.
  Both routes that add a column require a row to actually be *fetched*: `get_letterboxd_data` introduces
  them by concat only when there is a **new** slug, and `refresh_letterboxd_data`'s pre-seed loop (guarded by
  `test_refresh_adds_columns_missing_from_target_cache`) only when there is a **stale** one. Age is the only
  refresh trigger, so a cache that a recent backfill rewrote in full has neither — every row is young and
  every slug is known — and the run would reach the write with a pre-migration frame and fail **every time**,
  not occasionally. `--reset_database` is not a real escape hatch here: it refetches ~6.7k films from
  Letterboxd and drops any whose page has since gone.
  `get_letterboxd_data` therefore seeds `_SCHEMA_MIGRATION_COLUMNS` (null) onto the loaded cache, which is
  what makes the schema converge on *any* run rather than only a busy one — a migration step with an
  expiry, not a permanent guard: drop an entry once no cache in use predates it (i.e. once every checkout's
  cache postdates 2026-08-15). Values are untouched, so a populated column is never clobbered. This applies
  to any cache built by an older version of this pipeline, independent of the now-removed flag.
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
   *next write* fail against a cache that predates it, and neither the concat nor the refresh pre-seed adds
   the column unless a row is actually fetched. Add it to `_SCHEMA_MIGRATION_COLUMNS` in the same change, so
   any run converges the schema; the backfill then fills the values.
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

*Letterboxd-sourced coverage figures measured 2026-08-11 against the real cache (6,759 rows). The
territories+genres migration ran as a one-pass backfill on 2026-08-15 across 6,761 films (6,763-row cache,
2 skipped for having no `tmdb_id`); their coverage figures above reflect that backfilled cache. They will
drift; the sources above will not.*
