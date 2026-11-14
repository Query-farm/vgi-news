"""Guards for the published composed example (``COMPOSED_OVERLAP_EXAMPLE``).

The example SQL is defined once in ``vgi_news/tables.py``; these checks keep its
three publication points and the haybarn E2E that executes it from drifting.
"""

from __future__ import annotations

import json
import pathlib

from vgi_news.tables import COMPOSED_OVERLAP_EXAMPLE, NewsSearch
from vgi_news.worker import NewsWorker

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_composed_example_in_news_search_examples() -> None:
    """It is among news_search's first 5 examples (what the agent simulator shows)."""
    sqls = [ex.sql for ex in NewsSearch.Meta.examples[:5]]
    assert COMPOSED_OVERLAP_EXAMPLE["sql"] in sqls
    tagged = json.loads(NewsSearch.Meta.tags["vgi.example_queries"])
    assert COMPOSED_OVERLAP_EXAMPLE in tagged[:5]


def test_composed_example_in_schema_examples() -> None:
    """The schema-level example list carries it too."""
    (schema,) = NewsWorker.catalog.schemas
    assert COMPOSED_OVERLAP_EXAMPLE in json.loads(schema.tags["vgi.example_queries"])


def test_composed_example_executed_by_sql_e2e() -> None:
    """test/sql/news_search.test runs the exact published SQL against the mock."""
    text = (ROOT / "test" / "sql" / "news_search.test").read_text()
    assert COMPOSED_OVERLAP_EXAMPLE["sql"] in text
