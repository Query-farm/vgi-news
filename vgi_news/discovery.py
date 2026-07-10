"""Discovery table function: ``news_providers()``.

Lists the available providers and whether each needs an API key, so callers can
introspect the worker from SQL:

    SELECT * FROM news.news_providers();
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar

import pyarrow as pa
from vgi.metadata import FunctionExample
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
    bind_fixed_schema,
    init_single_worker,
)
from vgi_rpc.rpc import OutputCollector

from .providers import get_provider, provider_names
from .schema_utils import field


@dataclass(kw_only=True)
class _NoArgs:
    """``news_providers()`` takes no arguments."""


_PROVIDERS_SCHEMA = pa.schema(
    [
        field("provider", pa.string(), "Provider name to pass as provider := '...'.", nullable=False),
        field(
            "requires_key",
            pa.bool_(),
            "Whether the provider needs an API key (via the secret provider).",
            nullable=False,
        ),
    ]
)


@init_single_worker
@bind_fixed_schema
class NewsProviders(TableFunctionGenerator[_NoArgs]):
    """List available news providers and whether each requires an API key."""

    FIXED_SCHEMA: ClassVar[pa.Schema] = _PROVIDERS_SCHEMA

    class Meta:
        """Function metadata."""

        name = "news_providers"
        description = "List available news providers and whether each requires an API key"
        categories = ["news", "metadata"]
        tags = {
            "vgi.title": "List News Providers",
            "vgi.category": "discovery",
            "vgi.doc_llm": (
                "# news_providers\n\n"
                "Introspection table function that lists every news provider this worker can route "
                "`news_search` to, and whether each one needs an API key. It contacts no external "
                "service, so it always runs offline — use it to discover valid `provider :=` values "
                "and to decide whether you must create a secret before searching.\n\n"
                "## When to use\n\n"
                "- Discover which providers are available (e.g. before picking `provider :=`).\n"
                "- Check whether a provider needs an API key (so you know to create a DuckDB "
                "secret first).\n"
                "- As a cheap, backend-free smoke test that the worker is attached and healthy.\n\n"
                "## Inputs\n\n"
                "None — call it with empty parentheses: `news_providers()`.\n\n"
                "## Outputs\n\n"
                "One row per provider with `provider` (the name to pass to `news_search`) and "
                "`requires_key` (whether an API key must be supplied via the secret provider). "
                "`gdelt` returns `false` (free, no key); `newsapi` returns `true`."
            ),
            "vgi.doc_md": (
                "# News Providers\n\n"
                "`news_providers()` lists the news providers this worker supports and whether each "
                "requires an API key. It takes no arguments and makes no network calls.\n\n"
                "## Notes\n\n"
                "- `gdelt` is free and needs no key (`requires_key = false`); it is the default "
                "provider for `news_search`.\n"
                "- `newsapi` needs an API key (`requires_key = true`) supplied via a `TYPE newsapi` "
                "DuckDB secret.\n"
                "- This function is a safe, backend-free way to verify the worker is reachable.\n\n"
                "Runnable queries are attached as this object's example queries."
            ),
            "vgi.keywords": (
                "news providers, list providers, discovery, metadata, gdelt, newsapi, api key, "
                "requires key, introspection, capabilities, available sources"
            ),
            # Structured static result schema (VGI307/VGI321): the paren-less
            # provider list always returns these two columns. Replaces the retired
            # free-form vgi.result_columns_md (VGI414).
            "vgi.result_columns_schema": json.dumps(
                [
                    {
                        "name": "provider",
                        "type": "VARCHAR",
                        "description": "Provider name to pass as provider := '...' (e.g. gdelt, newsapi).",
                    },
                    {
                        "name": "requires_key",
                        "type": "BOOLEAN",
                        "description": "Whether the provider needs an API key supplied via the secret provider.",
                    },
                ]
            ),
        }
        examples = [
            FunctionExample(
                sql="SELECT provider, requires_key FROM news.main.news_providers() ORDER BY provider",
                description="List each provider and whether it requires an API key",
            ),
        ]

    @classmethod
    def cardinality(cls, params: BindParams[_NoArgs]) -> TableCardinality:
        """Return the fixed provider-count cardinality."""
        n = len(provider_names())
        return TableCardinality(estimate=n, max=n)

    @classmethod
    def process(cls, params: ProcessParams[_NoArgs], state: None, out: OutputCollector) -> None:
        """Emit one row per provider with its key requirement."""
        names = provider_names()
        out.emit(
            pa.RecordBatch.from_pydict(
                {
                    "provider": names,
                    "requires_key": [get_provider(n).requires_key for n in names],
                },
                schema=params.output_schema,
            )
        )
        out.finish()


DISCOVERY_FUNCTIONS: list[type] = [NewsProviders]
