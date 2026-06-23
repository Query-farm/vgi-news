"""Shared Arrow-schema helpers and the unified news-result schema.

Every provider maps its native JSON onto the one schema defined here, so a
``news_search`` query returns the same columns regardless of which provider
served it. Missing provider fields map to NULL.

The TIMESTAMPTZ column (``seendate``) needs an *explicit* Arrow type
(``pa.timestamp("us", tz="UTC")``); DuckDB renders that as ``TIMESTAMP WITH
TIME ZONE``. ``extra`` is JSON carried as VARCHAR.
"""

from __future__ import annotations

import pyarrow as pa

#: Arrow type for the ``seendate`` column. Microsecond precision, UTC tz, so
#: DuckDB sees a ``TIMESTAMP WITH TIME ZONE`` (TIMESTAMPTZ).
TIMESTAMPTZ = pa.timestamp("us", tz="UTC")


def field(
    name: str,
    type: pa.DataType,
    comment: str,
    *,
    nullable: bool = True,
) -> pa.Field:
    """Build a ``pa.Field`` carrying a column comment in its metadata.

    The ``comment`` metadata key is the framework's transport for column
    comments -- DuckDB surfaces it via ``duckdb_columns()`` and ``DESCRIBE``.
    """
    return pa.field(
        name,
        type,
        nullable=nullable,
        metadata={b"comment": comment.encode("utf-8")},
    )


#: The unified result schema returned by ``news_search`` for every provider.
NEWS_SCHEMA: pa.Schema = pa.schema(
    [
        field("title", pa.string(), "Article headline.", nullable=True),
        field("url", pa.string(), "Canonical article URL.", nullable=True),
        field("domain", pa.string(), "Publisher domain (e.g. 'bbc.co.uk').", nullable=True),
        field("language", pa.string(), "Article language (provider-reported; codes vary).", nullable=True),
        field(
            "seendate",
            TIMESTAMPTZ,
            "When the article was first seen/published (UTC). NULL if unknown.",
            nullable=True,
        ),
        field("country", pa.string(), "Source country (FIPS/ISO as the provider reports it).", nullable=True),
        field(
            "tone",
            pa.float64(),
            "Sentiment tone (GDELT only; NULL for providers without sentiment).",
            nullable=True,
        ),
        field("source", pa.string(), "Provider that produced this row (e.g. 'gdelt', 'newsapi').", nullable=False),
        field("extra", pa.string(), "Provider-specific fields, JSON-encoded.", nullable=True),
    ]
)
