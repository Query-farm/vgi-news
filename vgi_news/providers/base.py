"""Provider protocol + shared HTTP plumbing for news providers.

A provider turns a query into one *page* of :class:`NewsRow` results plus the
scan-state needed to fetch the next page. Two scan-state shapes exist:

* **GDELT** pages by a *time window*: the externalized state is the oldest
  ``seendate`` fetched so far; the next call asks for an older window. The
  GDELT DOC API caps a single response (``maxrecords`` <= 250) and does NOT
  cursor-paginate deeply, so window-paging is the honest way to go further
  back. This is documented in the provider and the README.
* **NewsAPI** pages by an integer ``page`` number (1-based).

Both are carried in the table function's serializable scan-state field.

HTTP discipline (network-worker rules): a per-call timeout and a small bounded
retry with backoff on 429/5xx. Providers never raise raw transport errors at
the caller; they raise :class:`ProviderError` with a clean message, which the
table function surfaces as a DuckDB error.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar, Protocol

import httpx


class ProviderError(RuntimeError):
    """A clean, user-facing provider error (bad request, exhausted retries, ...)."""


@dataclass(slots=True)
class NewsRow:
    """One article mapped onto the unified schema. Missing fields are ``None``."""

    title: str | None = None
    url: str | None = None
    domain: str | None = None
    language: str | None = None
    seendate: datetime | None = None
    country: str | None = None
    tone: float | None = None
    source: str = ""
    extra: str | None = None


@dataclass(slots=True)
class PageResult:
    """One page of results plus the cursor for the next page.

    Attributes:
        rows: The articles in this page (already mapped to ``NewsRow``).
        next_window_end: For window-paging providers (GDELT): the end of the
            *next* (older) window, i.e. the oldest ``seendate`` seen on this
            page. ``None`` when there is nothing older to fetch.
        next_page: For page-paging providers (NewsAPI): the next 1-based page
            number, or ``None`` when exhausted.
    """

    rows: list[NewsRow] = field(default_factory=list)
    next_window_end: datetime | None = None
    next_page: int | None = None


# Retry on these statuses (rate limiting + transient upstream failures).
_RETRY_STATUS = {429, 500, 502, 503, 504}


def http_get_json(
    url: str,
    *,
    params: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    backoff: float = 0.5,
    _sleep: Any = time.sleep,
) -> Any:
    """GET ``url`` and return parsed JSON, with bounded retry on 429/5xx.

    Raises:
        ProviderError: On a non-retryable HTTP error, exhausted retries, a
            transport/timeout failure, or a non-JSON body.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = httpx.get(url, params=params, timeout=timeout, headers=headers or {})
        except httpx.HTTPError as exc:  # timeouts, connection errors, ...
            last_exc = ProviderError(f"request to {url} failed: {exc}")
            _sleep(backoff * (2**attempt))
            continue

        if resp.status_code in _RETRY_STATUS and attempt < max_retries - 1:
            _sleep(backoff * (2**attempt))
            continue
        if resp.status_code >= 400:
            body = resp.text[:200].replace("\n", " ")
            raise ProviderError(f"{url} returned HTTP {resp.status_code}: {body}")

        try:
            return resp.json()
        except ValueError:
            body = resp.text[:200].replace("\n", " ")
            raise ProviderError(f"{url} returned non-JSON body: {body}") from None

    raise last_exc or ProviderError(f"request to {url} failed after {max_retries} attempts")


class Provider(Protocol):
    """The pluggable provider surface.

    Implementations are constructed with an optional ``base_url`` override (for
    tests) and a per-call ``timeout``.
    """

    #: Stable provider name used in SQL (``provider := 'gdelt'``) and as the
    #: ``source`` column value.
    name: ClassVar[str]

    #: Whether this provider needs an API key (resolved via the secret provider).
    requires_key: ClassVar[bool]

    def __init__(self, *, base_url: str | None = None, timeout: float = 20.0) -> None: ...

    def search(
        self,
        query: str,
        *,
        count: int,
        timespan: str,
        window_end: datetime | None,
        page: int | None,
        country: str | None,
        language: str | None,
        api_key: str | None,
    ) -> PageResult:
        """Fetch one page of results for ``query``.

        Args:
            query: Free-text search query.
            count: Max rows to return in this page.
            timespan: Look-back window (e.g. ``'1d'``, ``'2w'``) — provider-mapped.
            window_end: For window-paging providers, fetch articles *older* than
                this instant (the prior page's oldest ``seendate``). ``None`` for
                the first page.
            page: For page-paging providers, the 1-based page number. ``None``
                for the first page.
            country / language: Optional filters (provider-mapped; ignored if
                unsupported).
            api_key: Resolved API key for key-based providers, else ``None``.
        """
        ...
