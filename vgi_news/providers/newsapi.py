"""NewsAPI.org provider — optional, API-key gated, page-paginated.

Endpoint: ``https://newsapi.org/v2/everything`` with an ``apiKey``. The key is
resolved via the SDK **secret provider** (never inline in SQL); see
``vgi_news.tables`` for the ``Secret("newsapi")`` lookup. The free NewsAPI tier
is limited (100 req/day, results delayed) — the user supplies and pays for their
own key.

Paging is the simple kind: ``page`` (1-based) + ``pageSize``. The externalized
scan-state is the integer next-page number.

Mapping notes
-------------
NewsAPI has no sentiment, so ``tone`` is always ``NULL``. It reports no source
country, so ``country`` is ``NULL``. ``publishedAt`` (ISO-8601) maps to
``seendate``; the source name and author go into ``extra``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, ClassVar

from .base import NewsRow, PageResult, Provider, ProviderError, http_get_json

_DEFAULT_BASE_URL = "https://newsapi.org/v2/everything"
_MAX_PAGE_SIZE = 100  # NewsAPI cap.


def parse_published_at(value: str | None) -> datetime | None:
    """Parse a NewsAPI ISO-8601 ``publishedAt`` to an aware UTC datetime.

    Returns ``None`` for missing/unparseable values (never raises).
    """
    if not value:
        return None
    try:
        # NewsAPI uses e.g. "2024-06-15T12:30:00Z"; fromisoformat handles the
        # offset form, so normalise a trailing 'Z' to '+00:00'.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _domain_from_url(url: str | None) -> str | None:
    """Best-effort publisher domain from an article URL (host, sans 'www.')."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        host = urlparse(url).netloc.lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def map_article(article: dict[str, Any]) -> NewsRow:
    """Map one NewsAPI article onto a unified ``NewsRow``."""
    src = article.get("source") or {}
    src_name = src.get("name") if isinstance(src, dict) else None
    url = article.get("url")
    extra = {
        "source_name": src_name,
        "author": article.get("author"),
        "description": article.get("description"),
    }
    extra = {k: v for k, v in extra.items() if v is not None}
    return NewsRow(
        title=article.get("title"),
        url=url,
        domain=_domain_from_url(url),
        language=None,  # NewsAPI /everything does not report per-article language
        seendate=parse_published_at(article.get("publishedAt")),
        country=None,  # not reported by /everything
        tone=None,  # NewsAPI has no sentiment
        source=NewsAPIProvider.name,
        extra=json.dumps(extra, ensure_ascii=False) if extra else None,
    )


def map_response(payload: dict[str, Any]) -> tuple[list[NewsRow], int]:
    """Map a NewsAPI JSON payload to ``(rows, total_results)``.

    Raises ``ProviderError`` on an ``status: "error"`` payload.
    """
    if payload.get("status") == "error":
        msg = payload.get("message") or "unknown error"
        raise ProviderError(f"newsapi error: {msg}")
    articles = payload.get("articles") or []
    if not isinstance(articles, list):
        raise ProviderError("NewsAPI response 'articles' was not a list")
    rows = [map_article(a) for a in articles if isinstance(a, dict)]
    total = int(payload.get("totalResults") or 0)
    return rows, total


# NewsAPI's relative-timespan support is limited; map a few common spans to the
# `from` parameter. Anything else is omitted (provider default window).
def _timespan_to_from(timespan: str) -> str | None:
    from datetime import timedelta

    units = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    ts = (timespan or "").strip().lower()
    if len(ts) < 2 or ts[-1] not in units or not ts[:-1].isdigit():
        return None
    delta = timedelta(**{units[ts[-1]]: int(ts[:-1])})
    return (datetime.now(UTC) - delta).strftime("%Y-%m-%dT%H:%M:%S")


class NewsAPIProvider(Provider):
    """NewsAPI.org provider (key required, page-paginated)."""

    name: ClassVar[str] = "newsapi"
    requires_key: ClassVar[bool] = True

    def __init__(self, *, base_url: str | None = None, timeout: float = 20.0) -> None:
        """Construct the NewsAPI provider with an optional ``base_url`` override and timeout."""
        self.base_url = base_url or _DEFAULT_BASE_URL
        self.timeout = timeout

    def search(
        self,
        query: str,
        *,
        count: int,
        timespan: str,
        window_end: datetime | None,  # unused: NewsAPI pages by page number
        page: int | None,
        country: str | None,  # unused by /everything
        language: str | None,
        api_key: str | None,
    ) -> PageResult:
        """Fetch one page-numbered page of NewsAPI results for ``query``."""
        if not query or not query.strip():
            raise ProviderError("newsapi: query must be a non-empty string")
        if not api_key:
            raise ProviderError(
                "newsapi requires an API key. Create a DuckDB secret of TYPE newsapi "
                "(e.g. CREATE SECRET (TYPE newsapi, api_key '...')); never inline the key in SQL."
            )

        page_num = page or 1
        page_size = max(1, min(count, _MAX_PAGE_SIZE))
        params: dict[str, Any] = {
            "q": query.strip(),
            "pageSize": page_size,
            "page": page_num,
            "sortBy": "publishedAt",
            "apiKey": api_key,
        }
        if language:
            params["language"] = language
        from_param = _timespan_to_from(timespan)
        if from_param:
            params["from"] = from_param

        payload = http_get_json(self.base_url, params=params, timeout=self.timeout)
        if not isinstance(payload, dict):
            raise ProviderError("NewsAPI response was not a JSON object")
        rows, total = map_response(payload)

        # Next page exists if we've not yet covered `total` results and this
        # page was full.
        next_page: int | None = None
        if len(rows) >= page_size and page_num * page_size < total:
            next_page = page_num + 1

        return PageResult(rows=rows, next_window_end=None, next_page=next_page)
