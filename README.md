<p align="center">
  <img src="https://raw.githubusercontent.com/Query-farm/vgi/main/docs/vgi-logo.png" alt="Vector Gateway Interface (VGI)" width="320">
</p>

<p align="center"><em>A <a href="https://query.farm">Query.Farm</a> VGI worker for DuckDB.</em></p>

# vgi-news

Search global news coverage from SQL. A [VGI](https://github.com/Query-farm/vgi-python)
worker that exposes a `news_search(...)` table function over a **pluggable
provider** surface — **GDELT** (free, no key) by default, **NewsAPI** behind a
key — normalising every provider to one unified schema.

```sql
INSTALL vgi FROM community; LOAD vgi;
ATTACH 'news' (TYPE vgi, LOCATION 'uv run news_worker.py');

-- Latest GDELT articles mentioning "climate summit" from the past day
SELECT title, url, domain, seendate, country
FROM news.news_search('climate summit', count := 25, timespan := '1d')
ORDER BY seendate DESC;
```

## Honest framing (read first)

This is an **egress connector**: queries leave the engine and hit a third-party
news index (note for data-residency-sensitive users). Its value is **breadth +
a clean, stable schema over a free global news index**, not a moat — GDELT does
the heavy lifting. The pluggable-provider design is the durable part: it
future-proofs against any single API changing or dying.

GDELT's paging is the **honest caveat** — see [Paging](#paging-and-scan-state).

## Providers

| Provider | Key? | Notes |
|---|---|---|
| **gdelt** (default) | none | [GDELT 2.0 DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/). Worldwide online news in ~100 languages. Free. |
| **newsapi** | yes | [NewsAPI.org](https://newsapi.org) `/v2/everything`. The user supplies and pays for their own key (resolved via the secret provider). |

```sql
SELECT * FROM news.news_providers();   -- list providers + whether a key is needed
```

## The `news_search` table function

```sql
news_search(
  query,                  -- positional: free-text search query (required)
  provider := 'gdelt',    -- 'gdelt' (default) | 'newsapi'
  count    := 25,         -- total max rows across all pages
  timespan := '1d',       -- look-back window: '6h', '1d', '2w', ... (provider-mapped)
  country  := '',         -- optional source-country filter (provider-mapped)
  language := '',         -- optional source-language filter (provider-mapped)
  page_size := 50         -- rows fetched per upstream request / per process() tick
)
```

It is a **table function**, so it takes DuckDB `name := value` arguments. (VGI
*scalar* functions are positional-only; only table functions take named args.)

### Unified result schema

Every provider maps onto exactly these columns (missing fields → `NULL`):

| column | type | notes |
|---|---|---|
| `title` | VARCHAR | headline |
| `url` | VARCHAR | canonical article URL |
| `domain` | VARCHAR | publisher domain |
| `language` | VARCHAR | provider-reported language (codes vary) |
| `seendate` | TIMESTAMPTZ | first-seen / published instant (UTC); `NULL` if unknown |
| `country` | VARCHAR | source country (provider's coding) |
| `tone` | DOUBLE | GDELT sentiment; **`NULL`** for providers without sentiment |
| `source` | VARCHAR | the provider that produced the row |
| `extra` | VARCHAR (JSON) | provider-specific fields, JSON-encoded |

`seendate` is a real `TIMESTAMP WITH TIME ZONE` (declared with an explicit Arrow
`timestamp[us, tz=UTC]`), so you can filter and order on it directly:

```sql
SELECT title, seendate
FROM news.news_search('elections', count := 50, timespan := '2d')
WHERE seendate > now() - INTERVAL 12 HOUR
ORDER BY seendate DESC;
```

### A note on `tone`

GDELT's `mode=ArtList` (the article list this worker queries) does **not** carry
a per-article tone, so `tone` is `NULL`. (GDELT exposes aggregate tone via
`mode=ToneChart` / the GKG, not the article list.) The column exists so a future
provider/mode with sentiment slots in without a schema change; if a response
ever includes a numeric `tone`, it is parsed. NewsAPI has no sentiment → `NULL`.

## Paging and scan state

`count` may exceed what one upstream request returns, so `news_search`
paginates across `process()` calls, carrying a small **serializable cursor**
(`NewsScanState`) between ticks. Two paging styles:

- **GDELT pages by TIME WINDOW.** The DOC API returns at most `maxrecords`
  articles per request (the API caps this at **250**) and offers **no deep
  cursor**. To go further back you must fetch the newest window, then request an
  **older** window ending just before the oldest article you already saw. We
  sort `DateDesc` and carry the **oldest `seendate`** of each page as the next
  window's `enddatetime`. This is approximate at window edges (articles sharing
  a second can straddle the boundary) and cannot exceed GDELT's own
  retention/rate limits — but it is the only honest way past one `maxrecords`
  page. GDELT also rate-limits to roughly **one request every 5 seconds**.
- **NewsAPI pages by PAGE NUMBER** (`page` / `pageSize`) — the simple kind.

The cursor is the externalized scan state: the GDELT window boundary (an ISO
timestamp) or the NewsAPI next-page integer. It extends
`ArrowSerializableDataclass`, so it survives the framework's state round-trip
between batches — see the round-trip assertion in `test/sql/news_scan_state.test`.

## Authentication (NewsAPI)

The NewsAPI key is resolved via the SDK **secret provider** — **never inline in
SQL**. Create a DuckDB secret of `TYPE newsapi`:

```sql
CREATE SECRET (TYPE newsapi, api_key 'YOUR_NEWSAPI_KEY');

SELECT title, source
FROM news.news_search('semiconductors', provider := 'newsapi', count := 20, timespan := '2d');
```

GDELT needs no key. Per-call timeouts and a small bounded retry (on 429/5xx)
apply to both providers; a provider error surfaces as a clean DuckDB error — the
worker never crashes.

## Local development

```bash
uv venv && uv pip install -e ../vgi-python "httpx>=0.27" "pyarrow>=16" pytest ruff mypy

make test        # pytest (parsers + mock server + worker integration) + SQL E2E
make test-unit   # pytest only
make test-sql    # haybarn sqllogictest E2E, driven against a local mock API
make test-live   # OPTIONAL: live smoke against the real (free) GDELT API
make lint        # ruff + mypy
```

Tests never hit a live API in the gate: provider parsers run against captured
JSON **fixtures**, and both the worker-integration and haybarn E2E suites point
the providers at a local **mock HTTP server** via `VGI_NEWS_<PROVIDER>_BASE_URL`.

## Layout

```
news_worker.py              # entrypoint: assembles the `news` catalog, serves over stdio
vgi_news/
  schema_utils.py           # the unified NEWS_SCHEMA + field() comment helper
  tables.py                 # news_search table function + NewsScanState cursor
  discovery.py              # news_providers() discovery table function
  providers/
    base.py                 # Provider protocol, NewsRow/PageResult, HTTP + retry
    gdelt.py                # GDELT DOC API (free, window-paged) — the default
    newsapi.py              # NewsAPI.org (key, page-paged)
tests/                      # fixtures, parser units, mock-server + worker E2E, live smoke
test/sql/                   # haybarn sqllogictest .test files
scripts/run_sql_e2e.py      # starts the mock API + runs the haybarn SQL suite
```

## Licensing

- **This worker: MIT** (see `LICENSE`).
- **GDELT:** the DOC API is free and public. Review and abide by the
  [GDELT terms / usage policy](https://www.gdeltproject.org/about.html#termsofuse)
  (including the ~1 request / 5s rate limit). GDELT data and your use of it are
  your responsibility.
- **NewsAPI:** governed by [NewsAPI's terms](https://newsapi.org/terms). The API
  key, subscription, and compliance are **the user's responsibility**; the key
  is never stored by this worker (it is supplied per-attach via the DuckDB
  secret manager).
- Python deps (`httpx`, `pyarrow`, `vgi-python`) are permissively licensed; no
  provider SDKs are bundled — providers are plain HTTP.

---

## Authorship & License

Written by [Query.Farm](https://query.farm) — every VGI worker is designed and built by Query.Farm.

Copyright 2026 Query Farm LLC - https://query.farm

