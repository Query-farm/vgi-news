# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.16.0",
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
from vgi.catalog import Catalog, Schema, Table

from vgi_news.discovery import NewsProviders
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

_CATALOG_KEYWORDS = json.dumps(
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
)

_SCHEMA_KEYWORDS = json.dumps(
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
)

#: Ordered navigation registry (VGI413). Each object declares a ``vgi.category``
#: naming one of these entries.
_SCHEMA_CATEGORIES = json.dumps(
    [
        {
            "name": "search",
            "description": "Full-text search over worldwide news coverage, returning the unified article schema.",
        },
        {
            "name": "discovery",
            "description": "Introspect the worker: which providers exist and whether each needs an API key.",
        },
    ]
)

#: Analyst tasks the linter's agent-check (VGI152/VGI920) replays. The discovery
#: tasks reference the keyless, offline ``news_providers()`` surface, so their
#: graded results are deterministic. The ``search_returns_articles`` task exercises
#: ``news_search`` for coverage (VGI520); because it necessarily contacts the live
#: GDELT upstream (rate-limited, time-varying), it is phrased as a boolean
#: "did anything come back" predicate rather than an exact-value compare, and it is
#: only graded when the linter runs with a live upstream (``--execute``). In CI the
#: lint gate runs offline (``execute: "false"``) — see .github/workflows/ci.yml and
#: the live SQL E2E job, which executes ``news_search`` against a local mock.
_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "list_providers",
            "prompt": ("List every news provider this worker supports and whether each one requires an API key."),
            "reference_sql": "SELECT provider, requires_key FROM news.main.news_providers() ORDER BY provider",
            "ignore_column_names": True,
        },
        {
            "name": "count_providers",
            "prompt": "How many news providers can I search?",
            "reference_sql": "SELECT count(*) AS provider_count FROM news.main.news_providers()",
            "ignore_column_names": True,
        },
        {
            "name": "keyless_providers",
            "prompt": "Which news providers can I use for free, without supplying an API key?",
            "reference_sql": (
                "SELECT provider FROM news.main.news_providers() WHERE requires_key = false ORDER BY provider"
            ),
            "ignore_column_names": True,
            "unordered": True,
        },
        {
            "name": "search_returns_articles",
            "prompt": (
                "Search recent worldwide news coverage of 'climate' with the default provider and "
                "confirm at least one article comes back."
            ),
            # Boolean threshold predicate (not an exact-value compare): news_search
            # rows depend on the live GDELT feed, so the only stable assertion is
            # "the search returned something". Table-function args are inline
            # literals (a table function cannot bind a column reference).
            "reference_sql": "SELECT count(*) > 0 AS has_articles FROM news.main.news_search('climate', count := 5)",
            "ignore_column_names": True,
        },
    ]
)

_NEWS_CATALOG = Catalog(
    name="news",
    default_schema="main",
    comment="Global news search (GDELT by default; NewsAPI with a key) for SQL.",
    source_url="https://github.com/Query-farm/vgi-news",
    tags={
        "vgi.title": "Global News Search",
        "vgi.keywords": _CATALOG_KEYWORDS,
        "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
        "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
        "vgi.author": "Query.Farm",
        "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
        "vgi.license": "MIT",
        "vgi.support_contact": "https://github.com/Query-farm/vgi-news/issues",
        "vgi.support_policy_url": "https://github.com/Query-farm/vgi-news/blob/main/README.md",
        "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
    },
    schemas=[
        Schema(
            name="main",
            comment="Global news search (GDELT by default; NewsAPI with a key) for SQL",
            tags={
                "vgi.title": "News — main",
                "vgi.keywords": _SCHEMA_KEYWORDS,
                # VGI413 navigation registry; each object names one of these via vgi.category.
                "vgi.categories": _SCHEMA_CATEGORIES,
                # VGI123 classifying tags use BARE keys (not vgi.-namespaced).
                "domain": "media-and-news",
                "category": "search",
                "topic": "news-articles",
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                # VGI506/VGI515 representative, catalog-qualified example queries as a
                # described {description, sql} JSON list.
                "vgi.example_queries": json.dumps(
                    [
                        {
                            "description": "List the available providers and their key requirements.",
                            "sql": "SELECT provider, requires_key FROM news.main.news_providers ORDER BY provider",
                        },
                        {
                            "description": "Fetch the 10 most recent headlines mentioning 'climate summit'.",
                            "sql": "SELECT title, url, seendate, tone "
                            "FROM news.main.news_search('climate summit', count := 10)",
                        },
                        {
                            "description": "Rank domains by how many 'elections' articles they ran in the "
                            "last two days.",
                            "sql": "SELECT domain, count(*) AS articles "
                            "FROM news.main.news_search('elections', timespan := '2d', count := 50) "
                            "GROUP BY domain ORDER BY articles DESC",
                        },
                    ]
                ),
            },
            # VGI311: expose the parameterless, deterministic discovery function
            # as a regular table so consumers can `SELECT * FROM news.news_providers`
            # (no parens). The function stays registered too, so the paren call
            # form keeps working for existing SQL and the client API.
            tables=[
                Table(
                    name="news_providers",
                    function=NewsProviders,
                    inline_bind=True,
                    comment="Available news providers and whether each requires an API key.",
                    # 'provider' is unique, so it's the natural primary key.
                    primary_key=(("provider",),),
                    not_null=("provider", "requires_key"),
                    tags={
                        "vgi.title": "Available News Providers & Key Requirements",
                        "vgi.category": "discovery",
                        # VGI123 classifying tags use BARE keys (not vgi.-namespaced).
                        "domain": "media-and-news",
                        "topic": "news-providers",
                        "vgi.keywords": json.dumps(
                            ["news providers", "providers", "discovery", "gdelt", "newsapi", "api key"]
                        ),
                        "vgi.doc_llm": (
                            "# news_providers (table)\n\n"
                            "One row per news provider this worker can route `news_search` to, with "
                            "`provider` (the name to pass as `provider :=`) and `requires_key` "
                            "(whether an API key must be supplied via a DuckDB secret). It contacts "
                            "no external service, so it always runs offline. Query it before choosing "
                            "a provider or to check whether you must create a secret first. `gdelt` is "
                            "free (`requires_key = false`, the default); `newsapi` needs a key "
                            "(`requires_key = true`)."
                        ),
                        "vgi.doc_md": (
                            "# News Providers\n\n"
                            "Lists the providers this worker supports and whether each requires an API "
                            "key. It is backend-free, so it is a safe way to verify the worker is "
                            "attached. Scan it with no parentheses (it is a plain table). Runnable "
                            "queries are attached as this table's example queries."
                        ),
                        # One runnable, deterministic, offline example (VGI509/VGI906): the
                        # paren-less table scan always returns the fixed provider list. Its
                        # measured time is dominated by `uv run` cold-start, which vgi-lint.toml
                        # accommodates via a raised slow_seconds (VGI908).
                        "vgi.executable_examples": json.dumps(
                            [
                                {
                                    "description": "List the available news providers and whether each "
                                    "requires an API key.",
                                    "sql": "SELECT provider, requires_key FROM news.main.news_providers "
                                    "ORDER BY provider",
                                }
                            ]
                        ),
                        # Illustrative (not spawn-timed) examples: the paren-less table scan
                        # is deterministic and offline.
                        "vgi.example_queries": json.dumps(
                            [
                                {
                                    "description": "List the available providers and their key requirements.",
                                    "sql": "SELECT provider, requires_key FROM news.main.news_providers "
                                    "ORDER BY provider",
                                },
                                {
                                    "description": "Count how many providers are available.",
                                    "sql": "SELECT count(*) AS provider_count FROM news.main.news_providers",
                                },
                                {
                                    "description": "List only the free providers (no API key required).",
                                    "sql": "SELECT provider FROM news.main.news_providers WHERE requires_key = false",
                                },
                            ]
                        ),
                    },
                ),
            ],
            functions=[*TABLE_FUNCTIONS, NewsProviders],
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
