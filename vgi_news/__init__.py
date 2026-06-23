"""vgi-news — global news search as a DuckDB/SQL VGI worker.

Exposes ``news_search(query, provider := ..., count := ..., timespan := ...)``
returning a unified news schema, behind a pluggable provider surface (GDELT by
default, NewsAPI with a key). See :mod:`vgi_news.tables` and
:mod:`vgi_news.providers`.
"""

from __future__ import annotations

from .discovery import DISCOVERY_FUNCTIONS
from .schema_utils import NEWS_SCHEMA
from .tables import TABLE_FUNCTIONS

__all__ = ["DISCOVERY_FUNCTIONS", "NEWS_SCHEMA", "TABLE_FUNCTIONS"]
