"""Pluggable news-search providers.

Each provider maps a third-party news API onto the unified ``NEWS_SCHEMA``
(see :mod:`vgi_news.schema_utils`). A provider implements the :class:`Provider`
protocol: a ``name``, a configurable ``base_url`` (so tests can point it at a
mock HTTP server), and a ``search(...)`` method that fetches one *page* of
results and reports the scan-state needed to fetch the next page.

v1 ships:

* **gdelt** — GDELT 2.0 DOC API (FREE, no key). The default.
* **newsapi** — NewsAPI.org (API key via the SDK secret provider).
"""

from __future__ import annotations

from .base import NewsRow, PageResult, Provider, ProviderError
from .gdelt import GDELTProvider
from .newsapi import NewsAPIProvider

#: Registry of provider *factories* keyed by provider name.
_PROVIDERS: dict[str, type[Provider]] = {
    GDELTProvider.name: GDELTProvider,
    NewsAPIProvider.name: NewsAPIProvider,
}


def provider_names() -> list[str]:
    """Return the sorted list of known provider names."""
    return sorted(_PROVIDERS)


def get_provider(name: str, *, base_url: str | None = None, timeout: float = 20.0) -> Provider:
    """Instantiate a provider by name.

    Args:
        name: Provider name (case-insensitive), e.g. ``'gdelt'``.
        base_url: Override the provider's API base URL (used by tests to point
            at a mock HTTP server). ``None`` uses the provider default.
        timeout: Per-request timeout in seconds.

    Returns:
        A new provider instance for ``name``.

    Raises:
        ProviderError: If ``name`` is not a known provider.
    """
    key = (name or "").strip().lower()
    cls = _PROVIDERS.get(key)
    if cls is None:
        known = ", ".join(provider_names())
        raise ProviderError(f"unknown news provider {name!r}; known providers: {known}")
    return cls(base_url=base_url, timeout=timeout)


__all__ = [
    "GDELTProvider",
    "NewsAPIProvider",
    "NewsRow",
    "PageResult",
    "Provider",
    "ProviderError",
    "get_provider",
    "provider_names",
]
