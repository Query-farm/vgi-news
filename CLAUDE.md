# CLAUDE.md — vgi-news

Guidance for working in this repo. `vgi-news` is a Python VGI worker that exposes
global news search to DuckDB/SQL behind a pluggable provider surface.

## What it is

- One table function, `news_search(query, provider := ..., count := ..., timespan := ..., ...)`,
  returning a **unified schema** (`title, url, domain, language, seendate,
  country, tone, source, extra`) for every provider.
- A discovery table function `news_providers()`.
- Providers (`vgi_news/providers/`): **gdelt** (free, no key, default) and
  **newsapi** (key via the secret provider). Each maps its JSON → the unified
  schema; missing fields → `NULL`.

## VGI conventions that bite

- **Scalars are positional-only; only TABLE functions take `name := value`.**
  `news_search` is a table function, so its named args (`provider`, `count`,
  `timespan`, `country`, `language`, `page_size`) work. Don't add named args to
  a scalar.
- **TIMESTAMPTZ / LIST / STRUCT / JSON returns need an explicit Arrow type.**
  `seendate` is `pa.timestamp("us", tz="UTC")` (DuckDB: `TIMESTAMP WITH TIME
  ZONE`); `tone` is `pa.float64()` (DOUBLE); `extra` is JSON carried as VARCHAR.
  See `vgi_news/schema_utils.py:NEWS_SCHEMA`.
- **Generator scan-state must extend `ArrowSerializableDataclass`** with
  `ArrowType(...)`-annotated fields. `NewsScanState` (in `tables.py`) is the
  pagination cursor; it round-trips between `process()` ticks. Keep it to plain
  serializable scalars.
- **Secrets only via the secret provider.** The NewsAPI key is requested in
  `on_bind` via `params.secrets.get("newsapi")` (two-phase bind) and read in
  `process` from `params.secrets`. Never inline a key in SQL or code.

## Paging (the important design point)

`process()` is called repeatedly until `out.finish()`. `news_search` fetches one
upstream page per tick and advances the cursor:

- **GDELT pages by time window.** The DOC API caps a response at `maxrecords`
  (≤250) and has **no deep cursor**. We sort `DateDesc` and carry the oldest
  `seendate` as the next window's `enddatetime`. Honest caveats (window-edge
  approximation, ~1 req/5s rate limit) are documented in `gdelt.py` and the
  README — keep them honest.
- **NewsAPI pages by page number** (`page`/`pageSize`).

`count` caps total rows across pages; `page_size` is per-request.

## Testing — never hit a live API in the gate

- **Parser units** (`tests/test_parsers.py`) run against captured JSON
  **fixtures** in `tests/fixtures/`: seendate/tone parsing, missing→NULL, `extra`.
- **Mock-server E2E** (`tests/test_mock_server.py`) points each provider's
  `base_url` at a local HTTP server (`tests/conftest.py`).
- **Worker integration** (`tests/test_worker.py`) spawns the real worker via
  `vgi.client.Client` and points it at the mock through the
  `VGI_NEWS_<PROVIDER>_BASE_URL` env vars the worker honours
  (`NewsSearch._base_url_override`). Uses `pool=None` so each Client spawns a
  fresh subprocess that picks up the current env. **Exercises the real
  scan-state round-trip.**
- **haybarn SQL E2E** (`test/sql/*.test`, driven by `scripts/run_sql_e2e.py`):
  explicit `LOAD vgi;` (NEVER `require vgi` — it SKIPs under haybarn),
  `require-env VGI_NEWS_WORKER`, ATTACH via `${VGI_NEWS_WORKER}`. The script
  starts a mock news API and exports the base-URL env vars so the DuckDB-spawned
  worker hits the mock. Asserts the unified columns, that `seendate` is a real
  TIMESTAMPTZ, the window scan-state ROUND-TRIPS across a batch boundary
  (`news_scan_state.test`), and clean error paths.
- **Optional live smoke** (`tests/test_live_gdelt.py`, `-m live`): hits the real
  (free) GDELT API. Excluded by default (`addopts = -m 'not live'`); NOT in CI.

## Commands

```bash
make test        # pytest + SQL E2E
make test-unit   # pytest only (live excluded by default)
make test-sql    # haybarn SQL E2E vs the local mock
make test-live   # optional live GDELT smoke
make lint        # ruff + mypy (both must be clean)
```

## Gotchas

- `requires-python >= 3.13`. `news_worker.py` has a PEP-723 header pinning
  `../vgi-python` for local `uv run`; CI installs `vgi-python` from PyPI instead.
- Provider errors must surface as a clean DuckDB error (`ValueError` from
  `process`), never an uncaught crash. `http_get_json` does the bounded retry on
  429/5xx and raises `ProviderError` on failure.
- Adding a provider: implement the `Provider` protocol in `providers/`, register
  it in `providers/__init__._PROVIDERS`, add a fixture + parser test, and a mock
  route. The unified schema does not change.
