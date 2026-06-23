"""Shared test fixtures: a tiny canned-response mock HTTP server.

The mock serves provider responses so providers can be pointed at it via
``base_url`` — deterministic, no network, no keys. Used by the provider mock
E2E tests and by the haybarn SQL E2E (the worker reads the mock URL from
``VGI_NEWS_MOCK_URL``).
"""

from __future__ import annotations

import json
import pathlib
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


class _Handler(BaseHTTPRequestHandler):
    # Set by make_mock_server: maps URL path -> (status, json-callable(query)->dict)
    routes: dict[str, Callable[[dict[str, list[str]]], tuple[int, Any]]] = {}

    def log_message(self, *args: Any) -> None:  # silence per-request logging
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        handler = self.routes.get(parsed.path)
        if handler is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error":"no route"}')
            return
        query = parse_qs(parsed.query)
        status, payload = handler(query)
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(routes: dict[str, Callable[[dict[str, list[str]]], tuple[int, Any]]]) -> tuple[HTTPServer, str]:
    """Start a threaded HTTP server with the given routes; return (server, base_url)."""
    handler = type("BoundHandler", (_Handler,), {"routes": routes})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}"


@pytest.fixture
def mock_gdelt() -> Iterator[str]:
    """A mock GDELT DOC endpoint at ``<base>/doc`` serving the ArtList fixture.

    Window-paging aware: the *first* request (no enddatetime) returns the full
    fixture; a *second* request carrying ``enddatetime`` returns one older
    article, so a multi-page scan terminates deterministically.
    """
    fixture = load_fixture("gdelt_artlist.json")
    older = {
        "articles": [
            {
                "url": "https://example.org/older",
                "title": "An older article from the next window",
                "seendate": "20240614T080000Z",
                "domain": "example.org",
                "language": "English",
                "sourcecountry": "United States",
            }
        ]
    }

    def route(query: dict[str, list[str]]) -> tuple[int, Any]:
        if query.get("enddatetime"):
            return 200, older
        return 200, fixture

    server, base = make_server({"/doc": route})
    try:
        yield f"{base}/doc"
    finally:
        server.shutdown()


@pytest.fixture
def mock_newsapi() -> Iterator[str]:
    """A mock NewsAPI endpoint at ``<base>/everything`` serving the fixture."""
    fixture = load_fixture("newsapi_everything.json")

    def route(query: dict[str, list[str]]) -> tuple[int, Any]:
        if not query.get("apiKey"):
            return 401, load_fixture("newsapi_error.json")
        return 200, fixture

    server, base = make_server({"/everything": route})
    try:
        yield f"{base}/everything"
    finally:
        server.shutdown()
