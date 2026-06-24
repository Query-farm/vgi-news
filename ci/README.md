# CI: the vgi-news worker integration suite

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the unit tests
and this repo's sqllogictest suite (`test/sql/*.test`) against the vgi-news VGI
worker through the **real DuckDB `vgi` extension** on every push / PR.

## How it works (no C++ build)

Rather than building the vgi DuckDB extension from source, CI drives a
**prebuilt** standalone `haybarn-unittest` (the DuckDB/Haybarn sqllogictest
runner, published in Haybarn's releases) and installs the **signed** `vgi`
extension from the Haybarn community channel:

1. **Install the worker** — `uv sync --frozen --extra http` into a venv.
   `news_worker.py` is a self-contained PEP 723 worker the extension can spawn
   via `uv run news_worker.py` (stdio) or boot over `--http` / `--unix`.
2. **Download the runner** — the matching `haybarn_unittest-*` asset per platform.
3. **Preprocess** — [`preprocess-require.awk`](preprocess-require.awk) rewrites
   each `require <ext>` into an explicit signed `INSTALL <ext> FROM
   {community,core}; LOAD <ext>;`, and injects `INSTALL vgi FROM community;`
   before each bare `LOAD vgi;` (these tests skip `require vgi`, which haybarn
   silently SKIPs, and `LOAD vgi;` directly). `require-env` and everything else
   pass through.
4. **Run** — [`run-integration.sh`](run-integration.sh) stages the preprocessed
   tree, starts the mock news server, resolves `VGI_NEWS_WORKER` (the ATTACH
   `LOCATION`) per `$TRANSPORT`, warms the extension cache once, then runs the
   suite in a single `haybarn-unittest` invocation, guarded against silent skips.

## The mock-driven worker (all transports)

`run-integration.sh` starts an out-of-band **canned-response mock news server**
(the `_Handler` from [`scripts/run_sql_e2e.py`](../scripts/run_sql_e2e.py),
loaded via importlib and bound to a random port) and **`export`s** the providers'
base-URL env vars at it:

```
VGI_NEWS_GDELT_BASE_URL=<base>/doc
VGI_NEWS_NEWSAPI_BASE_URL=<base>/everything
VGI_NEWS_TIMEOUT=10
```

The worker reads those env vars (`NewsSearch._base_url_override`), so every
provider hits the deterministic mock — no keys, no cost, no live egress.
Crucially the vars are **exported**, so a worker booted out-of-band by the script
(http/unix) inherits them just like a DuckDB-spawned subprocess does. **The mock
server stays up for the life of the script and serves all three transports** — no
per-transport mock plumbing.

## Transport matrix (subprocess | http | unix)

The same `test/sql/*.test` suite is run over all three VGI transports — the
extension picks the transport from the `LOCATION` string the `.test` files
`ATTACH`, and `run-integration.sh` builds that string from `$TRANSPORT`:

| `TRANSPORT`  | `VGI_NEWS_WORKER` (LOCATION)         | How the worker is reached |
|--------------|--------------------------------------|---------------------------|
| `subprocess` | `.venv/bin/python news_worker.py`    | extension spawns the worker per query; Arrow IPC over stdin/stdout (default) |
| `http`       | `http://127.0.0.1:<port>`            | harness boots `news_worker.py --http --port 0 --port-file <f>`, waits for the port-file, then ATTACHes that URL |
| `unix`       | `unix:///tmp/news-<pid>.sock`        | harness boots `news_worker.py --unix <sock>`, waits for the socket, then ATTACHes it |

The CI `integration` job is a `transport: [subprocess, http, unix]` × `os`
matrix; each leg runs `ci/run-integration.sh` with `TRANSPORT=<t>`. Run a single
transport locally with e.g. `TRANSPORT=http ci/run-integration.sh`.

### Port / readiness discovery

- **http**: the worker writes its auto-selected port to `--port-file`
  atomically, so the harness watches for that file (not stdout). Boot line:
  `news_worker.py --http --port 0 --port-file <f>`.
- **unix**: the worker binds the socket and prints `UNIX:<abs-path>`; the harness
  polls for the socket file (`test -S`). Boot line: `news_worker.py --unix <sock>`.

Both out-of-band server processes run with cwd = the repo root (so the worker
resolves the `vgi_news` package and inherits the exported mock base-URLs) and are
trap-killed on exit alongside the mock server.

### HTTP transport needs the `httpfs` extension (resolved, not gated)

The vgi extension implements HTTP transport on top of DuckDB's **httpfs**
extension, so an `http://` ATTACH binds with `VGI HTTP transport requires the
httpfs extension` unless httpfs is loaded first. This is a **dependency**, not a
protocol limitation, so we resolve it: the http leg injects a signed `INSTALL
httpfs FROM core; LOAD httpfs;` into each staged `.test` (after the awk-injected
`LOAD vgi;`). The leg also needs the worker's `http` extra (waitress) —
`pyproject.toml` ships an `http` extra (`vgi-python[http]`), the PEP 723 header
in `news_worker.py` lists it, and CI runs `uv sync --frozen --extra http`.

> **Sharp edge — the runner silently SKIPs HTTP errors.** The haybarn/DuckDB
> sqllogictest runner's default skip list skips any statement whose error
> contains `"HTTP"` or `"Unable to connect"`, so a broken http setup reports
> "All tests were skipped" — a green-looking **fake pass**. `run-integration.sh`
> fails the leg unless the runner reports `All tests passed (N assertions …)`
> with N > 0 and zero skips.

### `news_search` pagination over HTTP (externalized cursor — no gate)

`news_search` is a streaming/paging table function: it fetches one upstream page
per `process()` tick and advances a cursor until `count` is met or the provider
is exhausted. Streaming table functions run fine over the **stateless** HTTP
transport **because the cursor is externalized**: the per-scan position lives in
a plain-serializable `NewsScanState(ArrowSerializableDataclass)`
(`emitted` / `done` / `window_end_iso` / `next_page` / `started` — all plain
ints/bools/strings) that the framework round-trips through its continuation token
on every tick. The worker carries **no fetched rows in state**; each tick
re-fetches the page deterministically from the (mock) provider keyed by the
cursor, so the serialized state stays a few scalars and HTTP resumes correctly
across the batch boundary. So the http leg runs the **full** suite including
`news_scan_state.test` (the GDELT window cursor hops to the second window and
emits "Older C", reachable only via the resumed cursor) — nothing is gated. This
is the same "externalize the scan position into the serialized state" pattern as
the vgi-cve cursor fix; `news_search` already followed it, so no worker change
was needed.

### Per-transport status

- **subprocess**: GREEN — 39 assertions.
- **http**: GREEN — 45 assertions (39 + the injected httpfs INSTALL/LOAD across
  the three `.test` files). Full suite incl. `news_scan_state.test` paging.
- **unix**: GREEN — 39 assertions.

## Run it locally

```bash
uv sync --python 3.13 --extra http
HAYBARN_UNITTEST=/path/to/haybarn-unittest \
WORKER_CMD="uv run --python 3.13 news_worker.py" \
  TRANSPORT=subprocess ci/run-integration.sh    # or TRANSPORT=http / TRANSPORT=unix
```

`TRANSPORT` defaults to `subprocess`, and `WORKER_CMD` defaults to
`uv run --python 3.13 <repo>/news_worker.py`.
