"""Mock-HTTP-server E2E for the providers (no network, no keys).

Points each provider's ``base_url`` at a local mock server and asserts the full
fetch -> map -> PageResult path, including the scan-state cursor each provider
reports for the next page.
"""

from __future__ import annotations

from datetime import UTC, datetime

from vgi_news.providers import get_provider
from vgi_news.providers.base import ProviderError, http_get_json

from .conftest import make_server


def test_gdelt_search_against_mock(mock_gdelt):
    provider = get_provider("gdelt", base_url=mock_gdelt)
    page = provider.search(
        "climate summit",
        count=250,
        timespan="1d",
        window_end=None,
        page=None,
        country=None,
        language=None,
        api_key=None,
    )
    assert [r.title for r in page.rows][0] == "World leaders gather for climate summit"
    assert page.rows[0].source == "gdelt"
    assert page.next_page is None  # GDELT does not page by number
    # 3 rows < maxrecords(250) => short page => nothing older to fetch.
    assert page.next_window_end is None


def test_gdelt_window_paging_cursor(mock_gdelt):
    """A full page (count==len(rows)) reports the oldest seendate as next window."""
    provider = get_provider("gdelt", base_url=mock_gdelt)
    page = provider.search(
        "climate summit",
        count=3,  # equals the fixture's row count => "full" page
        timespan="1d",
        window_end=None,
        page=None,
        country=None,
        language=None,
        api_key=None,
    )
    # oldest dated row is 2024-06-15T09:00:00Z (the no-date row is ignored).
    assert page.next_window_end == datetime(2024, 6, 15, 9, 0, 0, tzinfo=UTC)

    # Fetching the next window returns the older article and then terminates.
    page2 = provider.search(
        "climate summit",
        count=3,
        timespan="1d",
        window_end=page.next_window_end,
        page=None,
        country=None,
        language=None,
        api_key=None,
    )
    assert page2.rows[0].title == "An older article from the next window"
    assert page2.next_window_end is None  # short page => done


def test_newsapi_search_against_mock(mock_newsapi):
    provider = get_provider("newsapi", base_url=mock_newsapi)
    page = provider.search(
        "elections",
        count=2,
        timespan="1d",
        window_end=None,
        page=1,
        country=None,
        language=None,
        api_key="test-key",
    )
    assert page.rows[0].title == "Markets rally after election results"
    # totalResults=42, page_size=2 => more pages remain.
    assert page.next_page == 2
    assert page.next_window_end is None


def test_newsapi_requires_key(mock_newsapi):
    provider = get_provider("newsapi", base_url=mock_newsapi)
    try:
        provider.search(
            "x", count=2, timespan="1d", window_end=None, page=1,
            country=None, language=None, api_key=None,
        )
        raise AssertionError("expected ProviderError for missing key")
    except ProviderError as exc:
        assert "API key" in str(exc)


def test_http_get_json_retries_then_succeeds():
    """A 503 then 200 should be retried transparently (bounded retry)."""
    state = {"calls": 0}

    def route(query):
        state["calls"] += 1
        if state["calls"] == 1:
            return 503, {"error": "temporarily unavailable"}
        return 200, {"ok": True}

    server, base = make_server({"/r": route})
    try:
        out = http_get_json(f"{base}/r", params={}, timeout=5, backoff=0.0, _sleep=lambda _: None)
        assert out == {"ok": True}
        assert state["calls"] == 2
    finally:
        server.shutdown()


def test_http_get_json_4xx_raises_clean():
    def route(query):
        return 400, {"error": "bad query"}

    server, base = make_server({"/r": route})
    try:
        try:
            http_get_json(f"{base}/r", params={}, timeout=5, _sleep=lambda _: None)
            raise AssertionError("expected ProviderError")
        except ProviderError as exc:
            assert "HTTP 400" in str(exc)
    finally:
        server.shutdown()
