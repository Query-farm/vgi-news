"""Optional LIVE smoke test against the real GDELT DOC API (free, no key).

NOT part of the CI gate. GDELT rate-limits to ~1 request / 5s and the live index
is non-deterministic, so this is a loose sanity check, run on demand:

    uv run pytest -m live -q

It is deselected by default (``-m "not live"`` is implied by most runs / CI).
"""

from __future__ import annotations

import pytest

from vgi_news.providers import get_provider


@pytest.mark.live
def test_gdelt_live_returns_rows():
    provider = get_provider("gdelt", timeout=30.0)
    page = provider.search(
        "climate",
        count=5,
        timespan="1d",
        window_end=None,
        page=None,
        country=None,
        language=None,
        api_key=None,
    )
    # Loose assertions: GDELT should return *some* rows for a common term, each
    # mapped to the unified shape with a parsed timestamp and the right source.
    assert page.rows, "expected at least one live GDELT article"
    row = page.rows[0]
    assert row.source == "gdelt"
    assert row.url
    assert row.seendate is not None
