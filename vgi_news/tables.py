"""The ``news_search`` table function — the worker's main surface.

    SELECT title, url, domain, seendate, tone, source
    FROM news.news_search('climate summit', provider := 'gdelt', count := 25, timespan := '1d');

It is a **table function** (so it accepts DuckDB ``name := value`` arguments)
that returns the unified ``NEWS_SCHEMA`` for every provider. It paginates across
``process()`` calls using a small serializable scan-state cursor:

* GDELT pages by **time window** (the cursor carries the oldest ``seendate``
  fetched; the next page asks for an older window). See ``providers/gdelt.py``
  for the honest write-up of GDELT's no-deep-cursor limitation.
* NewsAPI pages by **page number** (the cursor carries the next page).

The cursor extends ``ArrowSerializableDataclass`` so it survives the framework's
HTTP state round-trip between ``process()`` ticks.

Secrets: a NewsAPI key is resolved via the SDK secret provider (``TYPE newsapi``)
at bind time — never inline in SQL. GDELT needs no key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse
from vgi.metadata import FunctionExample
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableFunctionGenerator,
    init_single_worker,
)
from vgi_rpc import ArrowSerializableDataclass, ArrowType
from vgi_rpc.rpc import OutputCollector

from .providers import NewsRow, ProviderError, get_provider, provider_names
from .schema_utils import NEWS_SCHEMA

#: Secret type for the NewsAPI key (resolved via the secret provider).
NEWSAPI_SECRET_TYPE = "newsapi"


@dataclass(slots=True, frozen=True, kw_only=True)
class NewsSearchArgs:
    """Named arguments for ``news_search`` (table functions accept ``name :=``)."""

    query: Annotated[str, Arg(0, doc="Free-text news search query (required).")]
    provider: Annotated[
        str, Arg("provider", default="gdelt", doc="Provider: 'gdelt' (default, free) or 'newsapi' (needs a key).")
    ]
    count: Annotated[int, Arg("count", default=25, doc="Total max rows to return across all pages.", ge=1)]
    timespan: Annotated[
        str,
        Arg(
            "timespan",
            default="1d",
            doc=(
                "Look-back window as a duration string (a count followed by a unit such as h for "
                "hours, d for days, or w for weeks). Any duration the provider accepts works — this "
                "is an open range, not a fixed vocabulary — and it is mapped per provider."
            ),
        ),
    ]
    country: Annotated[str, Arg("country", default="", doc="Optional source-country filter (provider-mapped).")]
    language: Annotated[str, Arg("language", default="", doc="Optional source-language filter (provider-mapped).")]
    page_size: Annotated[
        int,
        Arg("page_size", default=50, doc="Rows fetched per upstream request / per process() tick.", ge=1),
    ]


@dataclass(kw_only=True)
class NewsScanState(ArrowSerializableDataclass):
    """Serializable pagination cursor carried between ``process()`` calls.

    Exactly one of ``window_end_iso`` / ``next_page`` is meaningful, depending
    on the provider's paging style; both are plain serializable scalars.

    Attributes:
        emitted: How many rows we've emitted so far (to honour ``count``).
        done: True once the provider reports no more pages (or ``count`` met).
        window_end_iso: GDELT window-paging cursor: ISO-8601 end of the next
            (older) window, or empty for the first page / exhausted.
        next_page: NewsAPI page-paging cursor: next 1-based page, or 0 when
            exhausted. Starts at 1 for the first page.
        started: False until the first page has been requested.
    """

    emitted: Annotated[int, ArrowType(pa.int64())] = 0
    done: Annotated[bool, ArrowType(pa.bool_())] = False
    window_end_iso: Annotated[str, ArrowType(pa.string())] = ""
    next_page: Annotated[int, ArrowType(pa.int64())] = 1
    started: Annotated[bool, ArrowType(pa.bool_())] = False


def _rows_to_batch(rows: list[NewsRow], schema: pa.Schema) -> pa.RecordBatch:
    """Build an output RecordBatch (unified schema) from ``NewsRow`` objects."""
    return pa.RecordBatch.from_pydict(
        {
            "title": [r.title for r in rows],
            "url": [r.url for r in rows],
            "domain": [r.domain for r in rows],
            "language": [r.language for r in rows],
            "seendate": [r.seendate for r in rows],
            "country": [r.country for r in rows],
            "tone": [r.tone for r in rows],
            "source": [r.source for r in rows],
            "extra": [r.extra for r in rows],
        },
        schema=schema,
    )


@init_single_worker
class NewsSearch(TableFunctionGenerator[NewsSearchArgs, NewsScanState]):
    """Search global news coverage, returning the unified news schema."""

    FunctionArguments: ClassVar[type] = NewsSearchArgs

    class Meta:
        """Function metadata."""

        name = "news_search"
        description = "Search global news coverage (GDELT by default; NewsAPI with a key)"
        categories = ["news", "search", "http"]
        tags = {
            "vgi.title": "Global News Article Search",
            "vgi.category": "search",
            "vgi.doc_llm": (
                "# news_search\n\n"
                "Search worldwide news coverage by free-text query and return a **unified article "
                "schema** regardless of which upstream provider served the rows. This is the worker's "
                "primary surface: use it whenever you need to find, filter, or analyze recent news "
                "articles directly from SQL.\n\n"
                "## When to use\n\n"
                "- Find which outlets/domains are covering a topic right now.\n"
                "- Pull recent headlines with their URLs and publish times for a feed or digest.\n"
                "- Measure coverage volume or sentiment (`tone`) over a look-back window.\n"
                "- Filter coverage by source country or language.\n\n"
                "## Inputs\n\n"
                "- `query` (positional, required): the free-text search string.\n"
                "- `provider :=` (default `'gdelt'`): `'gdelt'` (free, no key, worldwide, includes "
                "sentiment `tone`) or `'newsapi'` (requires a `TYPE newsapi` DuckDB secret).\n"
                "- `count :=` (default 25): total maximum rows across all internally-paged requests.\n"
                "- `timespan :=` (default `'1d'`): look-back window such as `'6h'`, `'1d'`, `'2w'`.\n"
                "- `country :=` / `language :=`: optional provider-mapped filters (empty = no filter).\n"
                "- `page_size :=` (default 50): rows fetched per upstream request / per scan tick.\n\n"
                "## Outputs\n\n"
                "One row per article over `title, url, domain, language, seendate, country, tone, "
                "source, extra`. `seendate` is a real `TIMESTAMP WITH TIME ZONE`; `tone` is a `DOUBLE` "
                "sentiment score (GDELT only, NULL otherwise); `extra` carries provider-specific "
                "fields as a JSON string.\n\n"
                "## Behavior & edge cases\n\n"
                "- Results are paginated transparently across `process()` ticks: GDELT pages by time "
                "window (carrying the oldest `seendate` as the next window edge — a window-edge "
                "approximation, since GDELT has no deep cursor), NewsAPI pages by page number.\n"
                "- An empty/blank `query` raises a clean error; an unknown `provider` lists the known "
                "ones. Upstream HTTP failures (rate limits, 5xx) surface as a clean DuckDB error.\n"
                "- Missing provider fields map to `NULL`. GDELT is rate-limited (~1 request / 5s)."
            ),
            "vgi.doc_md": (
                "# News Article Search\n\n"
                "`news_search(query, provider := ..., count := ..., timespan := ..., country := ..., "
                "language := ..., page_size := ...)` searches global news coverage and returns a "
                "single unified column set for every provider.\n\n"
                "## Overview\n\n"
                "Pass a free-text `query` and get back recent articles with their headline, URL, "
                "publisher domain, language, publish time, source country, sentiment tone, the "
                "originating provider, and a JSON `extra` blob of provider-specific fields. "
                "Runnable queries are attached as this function's example queries.\n\n"
                "## Notes\n\n"
                "- Default provider is **GDELT** (free, no API key, includes `tone`).\n"
                "- **NewsAPI** requires a `TYPE newsapi` DuckDB secret holding an `api_key`; never "
                "inline a key in SQL.\n"
                "- `count` caps total rows; `page_size` controls the per-request fetch size.\n"
                "- GDELT enforces a ~1 request / 5 seconds rate limit and has no deep cursor, so very "
                "large look-backs are approximated at window edges."
            ),
            "vgi.keywords": (
                "news, news search, articles, headlines, gdelt, newsapi, journalism, media, coverage, "
                "press, current events, sentiment, tone, search, http, current affairs"
            ),
            # Structured static result schema (VGI307/VGI321): the unified article
            # schema is identical for every provider. Replaces the retired free-form
            # vgi.result_columns_md (VGI414).
            "vgi.result_columns_schema": json.dumps(
                [
                    {"name": "title", "type": "VARCHAR", "description": "Article headline."},
                    {"name": "url", "type": "VARCHAR", "description": "Canonical article URL."},
                    {"name": "domain", "type": "VARCHAR", "description": "Publisher domain (e.g. bbc.co.uk)."},
                    {
                        "name": "language",
                        "type": "VARCHAR",
                        "description": "Article language as reported by the provider (code scheme varies).",
                    },
                    {
                        "name": "seendate",
                        "type": "TIMESTAMPTZ",
                        "description": "When the article was first seen/published (UTC); NULL if unknown.",
                    },
                    {
                        "name": "country",
                        "type": "VARCHAR",
                        "description": "Source country as the provider reports it (FIPS/ISO code scheme).",
                    },
                    {
                        "name": "tone",
                        "type": "DOUBLE",
                        "description": "Sentiment tone (GDELT only; NULL for providers without sentiment).",
                    },
                    {
                        "name": "source",
                        "type": "VARCHAR",
                        "description": "Provider that produced this row (e.g. gdelt, newsapi).",
                    },
                    {
                        "name": "extra",
                        "type": "VARCHAR",
                        "description": "Provider-specific fields, JSON-encoded.",
                    },
                ]
            ),
            # VGI515: the native duckdb_functions().examples carrier drops the
            # per-example descriptions, so mirror the FunctionExample list here as
            # a described {description, sql} JSON tag (byte-identical SQL).
            "vgi.example_queries": json.dumps(
                [
                    {
                        "description": "Latest 10 GDELT articles mentioning 'election' from the past week.",
                        "sql": "SELECT title, url, seendate "
                        "FROM news.main.news_search('election', count := 10, timespan := '1w')",
                    },
                    {
                        "description": "Count how many domains are covering 'elections' over the last two days.",
                        "sql": "SELECT domain, count(*) AS articles "
                        "FROM news.main.news_search('elections', timespan := '2d', count := 50) "
                        "GROUP BY domain ORDER BY articles DESC",
                    },
                    {
                        "description": "NewsAPI search (requires a TYPE newsapi secret with an api_key).",
                        "sql": "SELECT title, source "
                        "FROM news.main.news_search('elections', provider := 'newsapi', count := 20, timespan := '2d')",
                    },
                ]
            ),
        }
        examples = [
            FunctionExample(
                sql=(
                    "SELECT title, url, seendate FROM news.main.news_search('election', count := 10, timespan := '1w')"
                ),
                description="Latest 10 GDELT articles mentioning 'election' from the past week",
            ),
            FunctionExample(
                sql=(
                    "SELECT domain, count(*) AS articles "
                    "FROM news.main.news_search('elections', timespan := '2d', count := 50) "
                    "GROUP BY domain ORDER BY articles DESC"
                ),
                description="Count how many domains are covering 'elections' over the last two days",
            ),
            FunctionExample(
                sql=(
                    "SELECT title, source FROM news.main.news_search('elections', provider := 'newsapi', "
                    "count := 20, timespan := '2d')"
                ),
                description="NewsAPI search (requires a TYPE newsapi secret with api_key)",
            ),
        ]

    # -- bind ---------------------------------------------------------------

    @classmethod
    def on_bind(cls, params: BindParams[NewsSearchArgs]) -> BindResponse:
        """Validate the query/provider and resolve the NewsAPI secret when required."""
        a = params.args
        if not a.query or not a.query.strip():
            raise ValueError("news_search requires a non-empty query")
        if a.provider.strip().lower() not in provider_names():
            known = ", ".join(provider_names())
            raise ValueError(f"unknown provider {a.provider!r}; known: {known}")

        # Resolve the NewsAPI key via the secret provider (two-phase bind). GDELT
        # needs none, so only request the secret for providers that require a key.
        if a.provider.strip().lower() == NEWSAPI_SECRET_TYPE:
            params.secrets.get(NEWSAPI_SECRET_TYPE)
            if params.secrets.needs_resolution:
                return BindResponse.secret_scope_request(params.secrets.pending_lookups)

        return BindResponse(output_schema=NEWS_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[NewsSearchArgs]) -> NewsScanState:
        """Return the empty pagination cursor for a fresh scan."""
        return NewsScanState()

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _api_key(params: ProcessParams[NewsSearchArgs]) -> str | None:
        """Extract the NewsAPI key from resolved secrets, if present."""
        secret = params.secrets.get(NEWSAPI_SECRET_TYPE)
        if not secret:
            return None
        for key in ("api_key", "apiKey", "key", "token"):
            scalar = secret.get(key)
            if scalar is not None and scalar.is_valid:
                return str(scalar.as_py())
        return None

    # -- process ------------------------------------------------------------

    @classmethod
    def process(
        cls,
        params: ProcessParams[NewsSearchArgs],
        state: NewsScanState,
        out: OutputCollector,
    ) -> None:
        """Fetch the next provider page, emit its rows, and advance the cursor."""
        a = params.args
        if state.done or state.emitted >= a.count:
            out.finish()
            return

        provider = get_provider(a.provider, base_url=cls._base_url_override(a.provider), timeout=cls._timeout())
        remaining = a.count - state.emitted
        this_count = min(remaining, a.page_size)

        window_end: datetime | None = None
        if state.window_end_iso:
            window_end = datetime.fromisoformat(state.window_end_iso)

        try:
            page = provider.search(
                a.query,
                count=this_count,
                timespan=a.timespan,
                window_end=window_end,
                page=state.next_page if state.started else 1,
                country=a.country or None,
                language=a.language or None,
                api_key=cls._api_key(params),
            )
        except ProviderError as exc:
            # Surface a clean DuckDB error; never crash the worker.
            raise ValueError(str(exc)) from exc

        state.started = True
        rows = page.rows[:remaining]

        if rows:
            out.emit(_rows_to_batch(rows, params.output_schema))
            state.emitted += len(rows)

        # Advance the cursor for the next page, or finish.
        if page.next_window_end is not None:
            state.window_end_iso = page.next_window_end.isoformat()
        elif page.next_page is not None:
            state.next_page = page.next_page
        else:
            state.done = True

        if state.emitted >= a.count or not rows:
            state.done = True

        if state.done:
            out.finish()

    @staticmethod
    def _timeout() -> float:
        import os

        try:
            return float(os.environ.get("VGI_NEWS_TIMEOUT", "20"))
        except ValueError:
            return 20.0

    @staticmethod
    def _base_url_override(provider: str) -> str | None:
        """Per-provider base_url override from the environment.

        Lets tests / the haybarn mock E2E point a provider at a local mock
        server without touching SQL: e.g. ``VGI_NEWS_GDELT_BASE_URL=...``.
        Returns ``None`` (the provider default) when unset.
        """
        import os

        env_key = f"VGI_NEWS_{provider.strip().upper()}_BASE_URL"
        return os.environ.get(env_key) or None


TABLE_FUNCTIONS: list[type] = [NewsSearch]
