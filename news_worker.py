# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.16.0",
#     "httpx>=0.27",
# ]
# ///
"""VGI worker exposing global news search to DuckDB/SQL (repo-root entry shim).

The worker itself — the ``news`` catalog, the :class:`NewsWorker` class, and
:func:`main` — lives in the wheel-importable :mod:`vgi_news.worker`. This file is
a thin PEP 723 shim that re-exports them so ``uv run news_worker.py`` keeps
working for local dev, the Makefile, ``ci/run-integration.sh``, and the tests,
while the packaged worker (Docker image, wheel, console script) imports the same
code from ``vgi_news.worker``.

Usage:
    uv run news_worker.py                # serve over stdio (DuckDB subprocess)

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'news' (TYPE vgi, LOCATION 'uv run news_worker.py');

    SELECT title, url, seendate, tone
    FROM news.news_search('climate summit', provider := 'gdelt', count := 25, timespan := '1d');

    SELECT * FROM news.news_providers();

Default provider: **GDELT** (free, no API key, worldwide news index). The
optional **newsapi** provider needs an API key supplied via a DuckDB secret
(``TYPE newsapi``) — never inline in SQL. See README.md.
"""

from __future__ import annotations

from vgi_news.worker import NewsWorker, main

__all__ = ["NewsWorker", "main"]


if __name__ == "__main__":
    main()
