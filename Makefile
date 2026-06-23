# vgi-news — test + lint targets.
#
# Usage:
#   make test        # unit/integration pytest suite + SQL end-to-end
#   make test-unit   # pytest (parsers, mock-server E2E, worker integration)
#   make test-sql    # haybarn sqllogictest E2E, driven against a local mock
#   make test-live   # OPTIONAL live smoke against the real GDELT API (free)
#   make lint        # ruff + mypy
#
# The SQL E2E suite drives the *real* worker as a DuckDB subprocess through the
# haybarn-unittest runner, pointed at a local mock news API (no network, no
# keys) via scripts/run_sql_e2e.py.

# The command DuckDB runs for the `vgi` extension's ATTACH.
VGI_NEWS_WORKER ?= uv run --python 3.13 news_worker.py

HAYBARN ?= haybarn-unittest
LOCAL_BIN := $(HOME)/.local/bin

.PHONY: test test-unit test-sql test-live lint ensure-haybarn

test: test-unit test-sql

# Full pytest suite (live tests excluded by default via pyproject addopts).
test-unit:
	uv run pytest -q

# Install the haybarn-unittest sqllogictest runner if it isn't already present.
ensure-haybarn:
	@if ! PATH="$(LOCAL_BIN):$$PATH" command -v $(HAYBARN) >/dev/null 2>&1; then \
		echo "Installing haybarn-unittest..."; \
		uv tool install haybarn-unittest; \
	fi

# End-to-end SQL tests: start a mock news API, LOAD vgi, ATTACH the worker, run
# the .test glob. CRITICAL: under haybarn-unittest `require vgi` SKIPS — the
# .test files use an explicit `LOAD vgi;`.
test-sql: ensure-haybarn
	PATH="$(LOCAL_BIN):$$PATH" VGI_NEWS_WORKER="$(VGI_NEWS_WORKER)" \
		uv run python scripts/run_sql_e2e.py

# Optional: hits the real GDELT API (free, no key). Not in the CI gate.
test-live:
	uv run pytest -m live -q

lint:
	uv run ruff check .
	uv run mypy vgi_news/ news_worker.py
