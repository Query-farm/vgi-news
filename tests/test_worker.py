"""Integration tests driving the real worker subprocess via ``vgi.client.Client``.

Spawns ``news_worker.py`` and calls ``news_search`` / ``news_providers`` over
the VGI protocol, exactly as DuckDB would. The providers are pointed at a local
mock HTTP server via the ``VGI_NEWS_<PROVIDER>_BASE_URL`` env vars the worker
honours, so these are deterministic and need no network/keys.

This exercises the *real* pagination loop: ``process()`` is invoked repeatedly,
and the ``NewsScanState`` cursor is serialized and restored across each tick —
the scan-state round-trip — until ``out.finish()``.
"""

from __future__ import annotations

import pathlib
import sys

import pyarrow as pa
import pytest
from vgi import Arguments
from vgi.client import Client

from .conftest import make_server

WORKER = str(pathlib.Path(__file__).resolve().parent.parent / "news_worker.py")


@pytest.fixture
def worker_env(monkeypatch):
    """Set worker env vars (e.g. mock base URLs) on the parent process; the
    spawned worker subprocess inherits them. monkeypatch restores after."""

    def _set(env: dict[str, str]) -> None:
        for k, v in env.items():
            monkeypatch.setenv(k, v)

    return _set


def _client() -> Client:
    # Run the worker in the current interpreter (deps already installed), with
    # worker_limit=1 so the single-worker generator's output order is stable.
    # pool=None forces a fresh subprocess per Client so each picks up the
    # current (mock base-URL) environment rather than reusing a pooled worker
    # spawned with a different env.
    return Client(f"{sys.executable} {WORKER}", worker_limit=1, pool=None)


def _collect(gen) -> pa.Table:
    batches = list(gen)
    return pa.Table.from_batches(batches)


@pytest.fixture
def gdelt_paging_server():
    """Mock GDELT that pages by window: page 1 (full) then an older page, then empty.

    page_size in the test is set to the fixture row count so the FIRST page is
    "full" and the worker advances the window cursor — forcing a real
    cross-batch scan-state round-trip.
    """
    pages = [
        {
            "articles": [
                {"url": "https://a.test/1", "title": "Newest A", "seendate": "20240615T120000Z",
                 "domain": "a.test", "language": "English", "sourcecountry": "United States"},
                {"url": "https://a.test/2", "title": "Newest B", "seendate": "20240615T100000Z",
                 "domain": "a.test", "language": "English", "sourcecountry": "United States"},
            ]
        },
        {
            "articles": [
                {"url": "https://a.test/3", "title": "Older C", "seendate": "20240614T080000Z",
                 "domain": "a.test", "language": "English", "sourcecountry": "France"},
            ]
        },
    ]

    def route(query):
        # First page has no enddatetime; subsequent (window-paged) pages do.
        return 200, (pages[1] if query.get("enddatetime") else pages[0])

    server, base = make_server({"/doc": route})
    try:
        yield f"{base}/doc"
    finally:
        server.shutdown()


def test_news_providers_lists_providers():
    with _client() as client:
        table = _collect(client.table_function(function_name="news_providers"))
    assert table.column_names == ["provider", "requires_key"]
    providers = dict(
        zip(table.column("provider").to_pylist(), table.column("requires_key").to_pylist(), strict=True)
    )
    assert providers == {"gdelt": False, "newsapi": True}


def test_news_search_unified_schema_and_types(mock_gdelt, worker_env):
    worker_env({"VGI_NEWS_GDELT_BASE_URL": mock_gdelt})
    with _client() as client:
        table = _collect(
            client.table_function(
                function_name="news_search",
                arguments=Arguments(positional=[pa.scalar("climate summit")]),
            )
        )
    assert table.column_names == [
        "title", "url", "domain", "language", "seendate", "country", "tone", "source", "extra",
    ]
    # seendate is a real TIMESTAMPTZ.
    seendate_type = table.schema.field("seendate").type
    assert pa.types.is_timestamp(seendate_type)
    assert seendate_type.tz == "UTC"
    # tone column exists and is NULL for GDELT ArtList.
    assert table.column("tone").to_pylist() == [None, None, None]
    assert set(table.column("source").to_pylist()) == {"gdelt"}
    # The no-seendate fixture row maps to a NULL timestamp.
    assert None in table.column("seendate").to_pylist()


def test_scan_state_round_trips_across_batch_boundary(gdelt_paging_server, worker_env):
    """count spans two upstream windows: the worker must serialize the window
    cursor between process() ticks and resume from it. We get all 3 rows in
    newest-to-oldest order, proving the window cursor round-tripped."""
    worker_env({"VGI_NEWS_GDELT_BASE_URL": gdelt_paging_server})
    with _client() as client:
        table = _collect(
            client.table_function(
                function_name="news_search",
                arguments=Arguments(
                    positional=[pa.scalar("anything")],
                    named={
                        "count": pa.scalar(3),
                        "page_size": pa.scalar(2),  # forces a "full" first page -> window advance
                    },
                ),
            )
        )
    titles = table.column("title").to_pylist()
    # Page 1 (window 1): Newest A, Newest B. Page 2 (older window): Older C.
    assert titles == ["Newest A", "Newest B", "Older C"]
    # The oldest row came from the SECOND window, only reachable by resuming the
    # serialized window cursor across the batch boundary.
    assert table.column("country").to_pylist()[-1] == "France"


def test_newsapi_path_via_mock_requires_secret(mock_newsapi, worker_env):
    """Without a secret configured, the newsapi provider errors cleanly (no crash)."""
    worker_env({"VGI_NEWS_NEWSAPI_BASE_URL": mock_newsapi})
    with _client() as client:
        with pytest.raises(Exception) as excinfo:
            _collect(
                client.table_function(
                    function_name="news_search",
                    arguments=Arguments(
                        positional=[pa.scalar("elections")],
                        named={"provider": pa.scalar("newsapi")},
                    ),
                )
            )
    assert "API key" in str(excinfo.value) or "secret" in str(excinfo.value).lower()


def test_unknown_provider_errors_clean():
    with _client() as client:
        with pytest.raises(Exception) as excinfo:
            _collect(
                client.table_function(
                    function_name="news_search",
                    arguments=Arguments(
                        positional=[pa.scalar("x")],
                        named={"provider": pa.scalar("nope")},
                    ),
                )
            )
    assert "unknown provider" in str(excinfo.value).lower()
