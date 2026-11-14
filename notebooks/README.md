# Capped news search through VGI

[01 — Compare capped source mix](01_compare_capped_source_mix.ipynb) measures exact-URL and domain coverage in one public GDELT response, then changes the raw result cap from 20 to 50. It does not assess truth, sentiment or market share.

## Run from notebooks alone

Requires uv, Git and Python 3.13/3.14. Initial internet access installs the pinned public Git worker, packages and extension. Replay then uses the included response through the real worker's loopback HTTP hook. No credentials or paid NewsAPI calls are used.

```sh
cd notebooks
uv sync --frozen
uv run --frozen python execute.py
uv run --frozen python execute.py --parameters '{"ROW_LIMIT":50}' --output-dir artifacts/experiment
uv run --frozen python execute.py --parameters '{"DATA_MODE":"live"}' --output-dir artifacts/live --timeout 240
```

Select `.venv/bin/python` in Jupyter, or register it with:

```sh
uv run --frozen python -m ipykernel install --user --name vgi-news-notebooks --display-name "Python (vgi-news notebooks)"
```

The runner uses its own temporary kernel spec, notebook-directory cwd, and atomic successful saves. Overrides require a separate destination. Unknown parameters fail before execution; invalid values fail before starting a worker. Copies include recordings, helpers, branding and environment documentation. Logs, experiment copies and environments are ignored.

## Pins and public-response provenance

Visible SQL uses immutable public Git revision `9df0eb1c2480b0b791501f053c7e1be35d2a7e19`, the unchanged `vgi-news-worker` entry point, and published `vgi-python==0.51.0`, `vgi-rpc==0.53.1`, `pyarrow==26.0.1`, `httpx==0.28.1`. The independent notebook lock pins Haybarn **2.0.0rc11**. There is no checkout/root-environment discovery or engine build. Upgrading any pin requires rerunning both modes and the experiment.

`recordings/gdelt-climate.json` is the unchanged HTTP 200 body received at **2026-11-14T09:04:19.644329+00:00** from [GDELT's public DOC API](https://api.gdeltproject.org/api/v2/doc/doc?query=climate&mode=ArtList&format=json&maxrecords=100&sort=DateDesc&timespan=1d). Request parameters: climate, ArtList, JSON, maxrecords 100, DateDesc, timespan1d. The actual published Haybarn/remote worker obtained 84 rows through a recording proxy; its normal retry recovered from HTTP 429 to 200. The request URL, capture time, row count and SHA-256 are retained in `recordings/provenance.json`. Body SHA-256: `b1c13ec0d9b406a8c8af75112b9481ebaf6798636b5ff53ae77fbcd889dfc14f`.

Replay verifies that hash, serves only the exact recorded query contract and preserves article-array order, returning the first `maxrecords` rows. It does not fabricate extra results or refetch the web. A cap above 84 naturally returns the shorter 84-row recording. All returned index timestamps in that snapshot are 2026-11-13 18:00 UTC, a tied GDELT update bucket, not article publication times. A short prefix is sensitive to that arbitrary tie order. GDELT's search may match different senses of climate and different languages; no relevance classification is claimed.

Live mode clears both base-URL overrides, starts no replay server and uses the normal GDELT endpoint. The worker's timeout is 45 seconds per request with bounded retries/backoff. Failed/429 requests remain visible; no silent fallback exists. Caller environment values are restored at cleanup. Use replay for the controlled experiment and avoid rapid repeated live runs. The requested 1d is relative to the upstream request, not a guarantee that results span 24 hours. Both count and page size are at most 100, so the notebook does not invoke approximate older-window paging.

## Analytical limits

Exact URLs are deduplicated without URL normalization or syndicated-story clustering. Domain strings can include subdomains and shared owners; they are not independent confirmations. Missing URLs are excluded with diagnostics, unknown domains retained as their own group, and missing tone remains unavailable. Counts and concentration describe only returned index results. A changed cap cannot establish world news coverage or a trend.

Validation outcomes are recorded after default/standalone experiment/live-path checks, chart review and the repository's source gates.

Validated on macOS arm64: default 13.3 s and detached notebooks-only 50-row experiment 13.3 s each pass ten SQL checks, chart-total reconciliation and a single-request replay audit. Four charts were reviewed. Cap 20 → 50 yields 20 → 50 unique URLs, 16 → 42 known domains, and top-five share 45% → 26%. A one-row boundary run passes in 2.6 s; three runner guards preserve the successful default. Source `make test` and `make lint` pass: 41 pytest tests (one optional live test deselected), 42 SQL assertions in three cases, Ruff and mypy over ten source files. Formatting and docstring checks pass. Structural metadata is 100/100 L1 with zero findings; live metadata examples were deliberately not run. Root source and lock are unchanged.

**Live limitation observed during validation:** public capture through the actual worker succeeded after HTTP 429 → 200 and produced the saved 84-row recording. A later direct live notebook run at cap 20 exhausted normal retries with HTTP 429. That failed run saved no notebook and left the successful replay default unchanged. Live availability is therefore not guaranteed, and no successful live-notebook charts are claimed. Retry only after the upstream rate window permits; keep replay for the controlled experiment.
