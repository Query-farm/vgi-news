# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python>=0.8.3",
#     "httpx>=0.27",
# ]
# ///
"""VGI worker exposing global news search to DuckDB/SQL.

Assembles the table functions in ``vgi_news`` into a single ``news`` catalog and
runs the worker over stdio (a DuckDB subprocess).

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

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_news.discovery import DISCOVERY_FUNCTIONS
from vgi_news.tables import TABLE_FUNCTIONS

_NEWS_CATALOG = Catalog(
    name="news",
    default_schema="main",
    schemas=[
        Schema(
            name="main",
            comment="Global news search (GDELT by default; NewsAPI with a key) for SQL",
            functions=[*TABLE_FUNCTIONS, *DISCOVERY_FUNCTIONS],
        ),
    ],
)


class NewsWorker(Worker):
    """Worker process hosting the ``news`` catalog."""

    catalog = _NEWS_CATALOG


def main() -> None:
    """Run the news worker process over stdio."""
    NewsWorker.main()


if __name__ == "__main__":
    main()
