# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.5",
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

import json

from vgi import Worker
from vgi.catalog import Catalog, Schema, View

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
    "# Global News Search in SQL\n\n"
    "**Search worldwide news coverage directly from DuckDB SQL** — query millions of online news "
    "articles by free text, filter by country, language, time window, and sentiment, and get back a "
    "single unified result schema no matter which provider you use.\n\n"
    "This VGI worker turns global news monitoring into ordinary SQL. It is built for journalists, "
    "analysts, researchers, and data engineers who want to find, filter, and quantify recent news "
    "coverage without writing API client code or stitching together pagination. Point DuckDB at the "
    "worker, run a `SELECT`, and you have headlines, source domains, publication timestamps, and "
    "sentiment scores as queryable, joinable, aggregatable columns — ready to combine with the rest "
    "of your data.\n\n"
    "Under the hood the worker speaks to two pluggable news providers and normalizes each into one "
    "consistent shape. The default provider is **GDELT** — the free, no-API-key "
    "[GDELT Project](https://www.gdeltproject.org) global news index that continuously monitors "
    "online news in over 100 languages and annotates it with sentiment 'tone'; it is queried through "
    "the [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) (see the "
    "[GDELT data documentation](https://www.gdeltproject.org/data.html)). The second provider is "
    "**NewsAPI**, the commercial [newsapi.org](https://newsapi.org) headline service "
    "([API documentation](https://newsapi.org/docs)), enabled by supplying a key through a DuckDB "
    "secret. Every provider's JSON is mapped onto the same columns, so you can switch sources "
    "without rewriting a single query.\n\n"
    "## Table functions\n\n"
    "- `news_search(query, provider := ..., count := ..., timespan := ..., country := ..., "
    "language := ..., page_size := ...)` — search worldwide news articles by free text and return a "
    "unified schema: `title, url, domain, language, seendate, country, tone, source, extra`. "
    "`seendate` is a real `TIMESTAMP WITH TIME ZONE` and `tone` is a sentiment `DOUBLE`, so results "
    "sort, filter, and aggregate natively.\n"
    "- `news_providers()` — list the available providers and whether each requires an API key "
    "(also exposed as the parenthesis-free view `news.main.news_providers`).\n\n"
    "## Example SQL\n\n"
    "```sql\n"
    "-- Recent coverage of a topic, newest first, with sentiment\n"
    "SELECT title, url, seendate, tone\n"
    "FROM news.news_search('climate summit', provider := 'gdelt', count := 25, timespan := '1d');\n\n"
    "-- Which domains are driving a story?\n"
    "SELECT domain, count(*) AS articles\n"
    "FROM news.news_search('elections', timespan := '2d', count := 50)\n"
    "GROUP BY domain ORDER BY articles DESC;\n\n"
    "-- Discover available providers and their key requirements\n"
    "SELECT * FROM news.news_providers();\n"
    "```\n\n"
    "**Providers**: `gdelt` (default, free, no key, worldwide with sentiment tone) and `newsapi` "
    "(requires a `TYPE newsapi` secret with an `api_key` — never inline a key in SQL)."
)

_SCHEMA_DESCRIPTION_LLM = (
    "# news.main\n\n"
    "The single schema of the news worker. It holds two table functions over a unified article "
    "schema:\n\n"
    "- `news_search(query, ...)` — search worldwide news articles, returning `title, url, domain, "
    "language, seendate, country, tone, source, extra`.\n"
    "- `news_providers()` — list the available providers and whether each requires an API key.\n\n"
    "Use `news_providers()` first to discover valid `provider :=` values, then `news_search()` to "
    "retrieve and filter coverage. GDELT is the default (free, no key, includes sentiment `tone`); "
    "NewsAPI requires a `TYPE newsapi` secret."
)

_SCHEMA_DESCRIPTION_MD = (
    "# news.main\n\n"
    "News-search table functions over a unified article schema.\n\n"
    "## Functions\n\n"
    "- `news_search` — query worldwide news articles by free text.\n"
    "- `news_providers` — discover available providers and key requirements.\n\n"
    "## Notes\n\n"
    "All functions return the same column set regardless of provider, so callers can switch "
    "providers without changing their SQL."
)

# VGI311 — `news_providers()` is parameterless and always returns the same rows,
# so we also expose it as a regular VIEW of the same name. That lets consumers
# write `SELECT * FROM news.main.news_providers` (no parentheses); the view simply
# scans the backing table function.
_NEWS_PROVIDERS_VIEW = View(
    name="news_providers",
    definition="SELECT provider, requires_key FROM news.main.news_providers()",
    comment="Discovery table of every news provider and whether it needs an API key.",
    column_comments={
        "provider": "Provider name to pass as provider := '...' to news_search (e.g. 'gdelt', 'newsapi').",
        "requires_key": "Whether the provider needs an API key supplied via a DuckDB secret.",
    },
    tags={
        "vgi.title": "News Providers (table)",
        "vgi.doc_llm": (
            "A ready-to-scan **discovery table** of every news provider this worker can route "
            "`news_search` to, with `provider` (the name to pass as `provider :=`) and "
            "`requires_key` (whether an API key must be supplied via a DuckDB secret). This is the "
            "no-argument table form of the `news_providers()` table function -- query it directly "
            "with `SELECT * FROM news.main.news_providers` (no parentheses). `gdelt` needs no key "
            "(free); `newsapi` requires a `TYPE newsapi` secret. It makes no network calls."
        ),
        "vgi.doc_md": (
            "## news_providers (view)\n\n"
            "Every supported news **provider**, one per row, as a plain table.\n\n"
            "`provider` is the name to pass to `news_search` via `provider :=`; `requires_key` is "
            "whether it needs an API key (via a `TYPE newsapi` DuckDB secret). The no-argument "
            "table form of `news_providers()` -- scan it with `SELECT * FROM news.main.news_providers` "
            "(no parentheses). `gdelt` is free (no key); `newsapi` requires a key. No network calls."
        ),
        "vgi.keywords": json.dumps(
            [
                "news providers",
                "list providers",
                "discovery",
                "metadata",
                "gdelt",
                "newsapi",
                "api key",
                "requires key",
                "introspection",
                "capabilities",
                "available sources",
                "providers table",
            ]
        ),
        "domain": "media-and-news",
        "category": "search",
        "topic": "news-providers",
        "vgi.example_queries": (
            '[{"description": "List the available news providers and whether each requires a key.", '
            '"sql": "SELECT provider, requires_key FROM news.main.news_providers ORDER BY provider"}, '
            '{"description": "Count how many news providers are available.", '
            '"sql": "SELECT count(*) AS provider_count FROM news.main.news_providers"}]'
        ),
    },
)


_NEWS_CATALOG = Catalog(
    name="news",
    default_schema="main",
    comment="Global news search (GDELT by default; NewsAPI with a key) for SQL.",
    source_url="https://github.com/Query-farm/vgi-news",
    tags={
        "vgi.title": "Global News Search",
        "vgi.keywords": json.dumps(
            [
                "news",
                "news search",
                "articles",
                "headlines",
                "gdelt",
                "newsapi",
                "journalism",
                "media",
                "coverage",
                "press",
                "current events",
                "sentiment",
                "tone",
                "world news",
                "current affairs",
            ]
        ),
        "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
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
                "vgi.title": "News — main",
                "vgi.keywords": json.dumps(
                    [
                        "news",
                        "news search",
                        "news_search",
                        "news_providers",
                        "articles",
                        "headlines",
                        "gdelt",
                        "newsapi",
                        "media",
                        "coverage",
                        "sentiment",
                        "tone",
                        "providers",
                    ]
                ),
                # VGI123 classifying tags use BARE keys (not vgi.-namespaced).
                "domain": "media-and-news",
                "category": "search",
                "topic": "news-articles",
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                # VGI506 representative, catalog-qualified example queries.
                "vgi.example_queries": (
                    "SELECT provider, requires_key FROM news.main.news_providers() ORDER BY provider;\n"
                    "SELECT title, url, seendate, tone "
                    "FROM news.main.news_search('climate summit', count := 10);\n"
                    "SELECT domain, count(*) AS articles "
                    "FROM news.main.news_search('elections', timespan := '2d', count := 50) "
                    "GROUP BY domain ORDER BY articles DESC;"
                ),
            },
            functions=[*TABLE_FUNCTIONS, *DISCOVERY_FUNCTIONS],
            views=[_NEWS_PROVIDERS_VIEW],
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
