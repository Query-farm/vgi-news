"""Discovery table function: ``news_providers()``.

Lists the available providers and whether each needs an API key, so callers can
introspect the worker from SQL:

    SELECT * FROM news.news_providers();
"""

from __future__ import annotations

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
            "vgi.columns_md": (
                "| column | type | description |\n"
                "|---|---|---|\n"
                "| `provider` | VARCHAR | Provider name to pass as `provider := '...'` "
                "(e.g. `gdelt`, `newsapi`). |\n"
                "| `requires_key` | BOOLEAN | Whether the provider needs an API key supplied via "
                "the secret provider. |"
            ),
        }
        examples = [
            FunctionExample(
                sql="SELECT * FROM news.news_providers() ORDER BY provider",
                description="List providers and their key requirements",
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
