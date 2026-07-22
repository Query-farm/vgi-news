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

# Minimum delay before retrying a 429 (HTTP "Too Many Requests").
#
# This is NOT the same class of wait as the transient-5xx backoff. A 429 means
# the upstream told us we exceeded a documented request rate, so retrying inside
# that rate window is guaranteed to earn another 429. GDELT — the default (and
# free, keyless) provider — documents ~1 request / 5 seconds and answers 429
# above it, so a sub-second retry is not merely useless: it spends two more
# requests inside the forbidden window and prolongs the penalty.
#
# Measured against the live endpoint: with the old 0.5s/1.0s exponential backoff
# every retry of a 429 also returned 429, so one rate-limited request always
# became a hard failure after three requests in ~1.5s.
_RATE_LIMIT_BACKOFF = 5.0


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Return the response's ``Retry-After`` delay in seconds, if it gives one.

    Only the delta-seconds form is honoured (the HTTP-date form is rare in
    practice and not worth the clock-skew risk). Returns None when the header is
    absent or unparseable, so the caller can fall back to its own backoff.

    Args:
        resp: The HTTP response to inspect.

    Returns:
        The delay in seconds, or None.
    """
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def http_get_json(
    url: str,
    *,
    params: dict[str, Any],
    timeout: float,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    backoff: float = 0.5,
    rate_limit_backoff: float = _RATE_LIMIT_BACKOFF,
    _sleep: Any = time.sleep,
) -> Any:
    """GET ``url`` and return parsed JSON, with bounded retry on 429/5xx.

    A 429 waits at least ``rate_limit_backoff`` (or the server's ``Retry-After``
    when it sends a longer one), because retrying inside the upstream's
    documented rate window just earns another 429.

    Args:
        url: The URL to GET.
        params: Query-string parameters.
        timeout: Per-request timeout in seconds.
        headers: Optional request headers.
        max_retries: Maximum number of attempts before giving up.
        backoff: Base backoff (seconds) for the exponential retry delay.
        rate_limit_backoff: Minimum delay (seconds) before retrying a 429.
        _sleep: Injectable sleep function (for tests).

    Returns:
        The parsed JSON body.

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
            delay = backoff * (2**attempt)
            if resp.status_code == 429:
                # Respect the upstream's own rate window, not our 5xx backoff,
                # and escalate: a server that is still rate-limiting us after
                # one window is telling us to wait longer, not to try again at
                # the same cadence. Gives 5s then 10s at the default.
                delay = max(delay, rate_limit_backoff * (2**attempt))
                retry_after = _retry_after_seconds(resp)
                if retry_after is not None:
                    delay = max(delay, retry_after)
            _sleep(delay)
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

    def __init__(self, *, base_url: str | None = None, timeout: float = 20.0) -> None:
        """Construct the provider with an optional ``base_url`` override and timeout."""
        ...

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
            country: Optional country filter (provider-mapped; ignored if unsupported).
            language: Optional language filter (provider-mapped; ignored if unsupported).
            api_key: Resolved API key for key-based providers, else ``None``.

        Returns:
            One page of results plus the cursor for the next page.
        """
        ...
