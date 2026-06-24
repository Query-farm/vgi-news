# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.4",
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

_CATALOG_DESCRIPTION_LLM = (
    "Search global news coverage from SQL. The news_search table function queries worldwide news "
    "articles by free-text query and returns a unified schema (title, url, domain, language, "
    "seendate, country, tone, source, extra), with a look-back window, optional country/language "
    "filters, and row-count paging. GDELT is the default provider (free, no API key, worldwide "
    "monitoring of online news with sentiment 'tone'); NewsAPI is available with a key supplied via "
    "a DuckDB secret. The news_providers table function lists the available providers and whether "
    "each needs a key. Use this worker to find, filter, and analyze recent news articles in SQL "
    "(who is covering a topic, from which domains/countries, how recently, and at what sentiment)."
)

_CATALOG_DESCRIPTION_MD = (
    "# news\n\n"
    "Global news search as a DuckDB/SQL VGI worker.\n\n"
    "Query worldwide news coverage with a single unified schema regardless of provider.\n\n"
    "**Table functions**\n\n"
    "- `news_search(query, provider := ..., count := ..., timespan := ..., country := ..., "
    "language := ..., page_size := ...)` — search news articles.\n"
    "- `news_providers()` — list available providers and their API-key requirements.\n\n"
    "**Providers**: `gdelt` (default, free, no key, worldwide with sentiment tone) and `newsapi` "
    "(requires a `TYPE newsapi` secret with an `api_key`)."
)

_SCHEMA_DESCRIPTION_LLM = (
    "News-search table functions: news_search (query worldwide news articles, returning title, url, "
    "domain, language, seendate, country, tone, and source) and news_providers (list the available "
    "news providers and whether each requires an API key)."
)

_SCHEMA_DESCRIPTION_MD = (
    "News-search table functions over a unified article schema: `news_search` (article search) and "
    "`news_providers` (provider discovery)."
)

_NEWS_CATALOG = Catalog(
    name="news",
    default_schema="main",
    comment="Global news search (GDELT by default; NewsAPI with a key) for SQL.",
    source_url="https://github.com/Query-farm/vgi-news",
    tags={
        "vgi.description_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.description_md": _CATALOG_DESCRIPTION_MD,
        "vgi.author": "Query.Farm",
        "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-news/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-news/blob/main/README.md",
    },
    schemas=[
        Schema(
            name="main",
            comment="Global news search (GDELT by default; NewsAPI with a key) for SQL",
            tags={
                "vgi.description_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.description_md": _SCHEMA_DESCRIPTION_MD,
            },
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
