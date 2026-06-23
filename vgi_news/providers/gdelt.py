"""GDELT 2.0 DOC API provider — the FREE, no-key default.

Endpoint: ``https://api.gdeltproject.org/api/v2/doc/doc`` with
``mode=ArtList&format=json``. GDELT indexes worldwide online news in ~100
languages and is free with no API key.

Paging limitation (documented honestly)
---------------------------------------
The DOC API returns at most ``maxrecords`` articles per request (the API caps
this at 250) and does **not** offer a deep cursor. To go further back you must
*page by time window*: fetch the newest window, then request an **older** window
ending just before the oldest article you already saw. We sort ``DateDesc`` and
carry the oldest ``seendate`` of each page as the next window's end
(``&enddatetime=...``). This is approximate at window edges (articles sharing a
second can straddle the boundary) and cannot exceed GDELT's own retention/rate
limits, but it is the only honest way to fetch beyond one ``maxrecords`` page.

Tone
----
``mode=ArtList`` rows do not carry a per-article tone, so ``tone`` is ``NULL``
here. (GDELT exposes aggregate tone via ``mode=ToneChart`` / the GKG, not the
article list.) If a future response includes a numeric ``tone`` field we parse
it; otherwise it stays ``NULL`` — exactly the "missing field -> NULL" contract.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from .base import NewsRow, PageResult, Provider, ProviderError, http_get_json

_DEFAULT_BASE_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_MAX_RECORDS = 250  # GDELT DOC API hard cap.

# GDELT seendate format, e.g. "20240615T123000Z".
_SEENDATE_FMT = "%Y%m%dT%H%M%SZ"
# enddatetime / startdatetime format, e.g. "20240615123000".
_GDELT_DT_FMT = "%Y%m%d%H%M%S"


def parse_seendate(value: str | None) -> datetime | None:
    """Parse a GDELT ``seendate`` ('YYYYMMDDTHHMMSSZ') to an aware UTC datetime.

    Returns ``None`` for missing or unparseable values (never raises) so a
    single odd row can't fail the whole scan.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value, _SEENDATE_FMT).replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _coerce_tone(article: dict[str, Any]) -> float | None:
    """Return a float tone if the row happens to carry one, else None."""
    raw = article.get("tone")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def map_article(article: dict[str, Any]) -> NewsRow:
    """Map one GDELT ArtList article dict onto a unified ``NewsRow``."""
    known = {"title", "url", "domain", "language", "seendate", "sourcecountry", "tone"}
    extra = {k: v for k, v in article.items() if k not in known}
    return NewsRow(
        title=article.get("title"),
        url=article.get("url"),
        domain=article.get("domain"),
        language=article.get("language"),
        seendate=parse_seendate(article.get("seendate")),
        country=article.get("sourcecountry"),
        tone=_coerce_tone(article),
        source=GDELTProvider.name,
        extra=json.dumps(extra, ensure_ascii=False) if extra else None,
    )


def map_response(payload: dict[str, Any]) -> list[NewsRow]:
    """Map a full GDELT DOC API JSON payload to a list of ``NewsRow``."""
    articles = payload.get("articles") or []
    if not isinstance(articles, list):
        raise ProviderError("GDELT response 'articles' was not a list")
    return [map_article(a) for a in articles if isinstance(a, dict)]


class GDELTProvider(Provider):
    """GDELT DOC API provider (free, no key, window-paged)."""

    name: ClassVar[str] = "gdelt"
    requires_key: ClassVar[bool] = False

    def __init__(self, *, base_url: str | None = None, timeout: float = 20.0) -> None:
        """Construct the GDELT provider with an optional ``base_url`` override and timeout."""
        self.base_url = base_url or _DEFAULT_BASE_URL
        self.timeout = timeout

    def search(
        self,
        query: str,
        *,
        count: int,
        timespan: str,
        window_end: datetime | None,
        page: int | None,  # unused: GDELT pages by window, not page number
        country: str | None,
        language: str | None,
        api_key: str | None,  # unused: GDELT needs no key
    ) -> PageResult:
        """Fetch one window-paged page of GDELT results for ``query``."""
        if not query or not query.strip():
            raise ProviderError("gdelt: query must be a non-empty string")

        full_query = query.strip()
        if country:
            full_query += f" sourcecountry:{country}"
        if language:
            full_query += f" sourcelang:{language}"

        params: dict[str, Any] = {
            "query": full_query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": max(1, min(count, _MAX_RECORDS)),
            "sort": "DateDesc",
        }
        # Window paging: an explicit older window-end takes precedence over the
        # relative timespan once we are past the first page.
        if window_end is not None:
            params["enddatetime"] = window_end.astimezone(UTC).strftime(_GDELT_DT_FMT)
        else:
            params["timespan"] = timespan

        payload = http_get_json(self.base_url, params=params, timeout=self.timeout)
        if not isinstance(payload, dict):
            raise ProviderError("GDELT response was not a JSON object")
        rows = map_response(payload)

        # Next window ends at the oldest seendate on this page. If a page is
        # short (fewer than maxrecords) there is nothing older worth fetching.
        next_window_end: datetime | None = None
        if len(rows) >= params["maxrecords"]:
            seendates = [r.seendate for r in rows if r.seendate is not None]
            if seendates:
                next_window_end = min(seendates)

        return PageResult(rows=rows, next_window_end=next_window_end, next_page=None)
