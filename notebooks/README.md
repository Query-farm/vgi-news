# Global News Search examples

[Compare capped source mix](01_compare_capped_source_mix.ipynb) asks how a result cap changes the exact URLs and provider-reported domain mix in one recorded GDELT response. It demonstrates both public SQL surfaces: provider discovery and a bounded search. The experiment expands the preserved response prefix from 20 to 50 rows.

[Open in Colab](https://colab.research.google.com/github/Query-farm/vgi-news/blob/main/notebooks/01_compare_capped_source_mix.ipynb). The notebook installs only the shared base package and retrieves checksum-verified companions automatically. It preserves compatible kernel libraries; the local publication runner is a separate extra. GitHub displays short setup cells expanded; editor folding metadata is only an editor aid.

## Run locally

Requires uv, Git and Python 3.13 or 3.14. First execution needs internet access for released packages, worker and VGI extension. Replay needs no credentials and makes no NewsAPI request.

```sh
cd notebooks
uv sync --locked
uv run --locked vgi-notebooks --timeout 300
uv run --locked vgi-notebooks --parameters '{"ROW_LIMIT":50}' --output-dir artifacts/experiment
uv run --locked vgi-notebooks --parameters '{"DATA_MODE":"live"}' --output-dir artifacts/live --timeout 300
```

The visible ATTACH uses the short `uvx --python 3.13 --from git+https://github.com/Query-farm/vgi-news@v0.4.0 vgi-news-worker` release command. Shared tooling is pinned to `v0.3.48`; Haybarn is 2.0.0rc11. The worker environment owns its dependencies. Only domain-specific replay transport and original recordings remain here; the [standard](https://github.com/Query-farm/vgi-example-notebooks/blob/main/docs/standard.md), execution and plotting utilities live centrally.

The shared connection isolates saved SQL secrets and establishes UTC before SQL cursors are created. Overrides require a separate output directory; unknown parameters are rejected. Publication adds content-addressed SVG image links for GitHub while preserving native SVG for Jupyter/Colab. Review PNGs and validation artifacts are ignored.

## Release and current source

The actual runtime is released v0.4.0 (`24fd6584ee75ea9d4c40e978ed35d6a3a25185ad`). Complete catalogs contain two logical names: `news_search` and `news_providers`; the latter also has a same-named table access form. Current source `d90f8a7` keeps those signatures and dependencies but fixes GDELT older-window pagination and corrects tone descriptions. Current compatibility is reviewed only for this single first-page search and offline provider registry; no exact current-source runtime execution or equivalent paging behavior is claimed. The notebook uses count≤100 and page_size100, which avoids that changed pagination path.

[Coverage evidence](coverage.json) records 2/2 released surfaces executed and 2/2 reviewed current-compatible examples, with exact-current execution separately zero. NewsAPI is an optional authenticated branch of `news_search`, not an omitted catalog object; this example does not execute it.

## Public recording

`recordings/gdelt-climate.json` is the unchanged HTTP 200 body received at **2026-11-14T09:04:19.644329+00:00** from [GDELT's public DOC API](https://api.gdeltproject.org/api/v2/doc/doc?query=climate&mode=ArtList&format=json&maxrecords=100&sort=DateDesc&timespan=1d). Request parameters: climate, ArtList, JSON, maxrecords 100, DateDesc, timespan1d. The actual published Haybarn/remote worker obtained 84 rows through a recording proxy; its normal retry recovered from HTTP 429 to 200. The request URL, capture time, row count and SHA-256 are retained in `recordings/provenance.json`. Body SHA-256: `b1c13ec0d9b406a8c8af75112b9481ebaf6798636b5ff53ae77fbcd889dfc14f`.

Replay verifies that hash, serves only the exact recorded query contract and preserves article-array order, returning the first `maxrecords` rows. It does not fabricate extra results or refetch the web. A cap above 84 naturally returns the shorter 84-row recording. All returned index timestamps in that snapshot are 2026-11-13 18:00 UTC, a tied GDELT update bucket, not article publication times. A short prefix is sensitive to that arbitrary tie order. GDELT's search may match different senses of climate and different languages; no relevance classification is claimed.

Live mode clears both base-URL overrides, starts no replay server and uses the normal GDELT endpoint. The worker's timeout is 45 seconds per request with bounded retries/backoff. Failed/429 requests remain visible; no silent fallback exists. Caller environment values are restored at cleanup. Use replay for the controlled experiment and avoid rapid repeated live runs. The requested 1d is relative to the upstream request, not a guarantee that results span 24 hours. Both count and page size are at most 100, so the notebook does not invoke approximate older-window paging.

## Interpretation and validation

Exact URLs are deduplicated without URL normalization or syndicated-story clustering. Domain strings can include subdomains and shared owners. Missing URLs are excluded with diagnostics, unknown domains retained, and missing tone stays unavailable. Counts and concentration describe only returned index results; they do not establish world news coverage, sentiment, independence or trends.

The saved default and detached 20→50 experiment pass ten SQL invariants, chart-total reconciliation and one-request replay audits. Unique URLs increase20→50, domains16→42, and top-five share changes45%→26%. All three original displayed analytical tables and both original recording files are preserved. Three runner guards and six invalid-parameter checks pass. Default28.5s, experiment4.0s; eight figures reviewed across default, experiment, live and preloaded validation.

A fresh notebook-only Python3.13 kernel with pandas2.2.3, Matplotlib3.10.0, IPython8.30.0, ipykernel6.17.1 and jupyter-client7.4.9 already imported completes the full notebook, retains all five versions and passes pip check; no local nbclient runner is installed. This tests preloaded-kernel compatibility, not a manually operated hosted Colab session.

A separate actual public live notebook run on 2026-11-15 UTC succeeded in35.1s:20 unique URLs across19 domains, top-five share30%, no available tone values. The previous rollout encountered HTTP429; current success does not guarantee endpoint availability. Live uses the moving upstream index and is separate from the fixed-sample experiment. Errors remain visible without fallback.

Notebook Ruff, presentation and coverage checks pass. Existing successful source CI43579329889 at unchanged source revision is reused; this revision does not rebuild the worker or repeat its full suite. Saved output contains no runtime warnings or local paths. Kernel transport/debugger diagnostics were confined to external validation logs.
