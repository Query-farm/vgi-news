#!/usr/bin/env python
"""Drive the haybarn SQL E2E suite against a local mock news API.

The haybarn ``.test`` files ATTACH the real worker, which DuckDB launches as a
subprocess; that worker inherits this process's environment. So we:

1. Start a canned-response mock HTTP server (the same routes the pytest mock
   E2E uses) on an ephemeral port.
2. Export ``VGI_NEWS_GDELT_BASE_URL`` / ``VGI_NEWS_NEWSAPI_BASE_URL`` so the
   worker hits the mock instead of the live GDELT/NewsAPI endpoints, plus
   ``VGI_NEWS_WORKER`` (the ATTACH LOCATION command).
3. Run ``haybarn-unittest`` over ``test/sql/*``.
4. Tear the server down.

No network, no API keys. Deterministic.

Usage:
    python scripts/run_sql_e2e.py            # uses `uv run news_worker.py`
    VGI_NEWS_WORKER='...' python scripts/run_sql_e2e.py
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A GDELT window-paging fixture: page 1 (two newest, a "full" page given
# page_size=2) then an older single-article window. This lets the SQL suite
# assert the window scan-state round-trips across a batch boundary.
_GDELT_PAGE_1 = {
    "articles": [
        {"url": "https://a.test/1", "title": "Newest A", "seendate": "20240615T120000Z",
         "domain": "a.test", "language": "English", "sourcecountry": "United States", "socialimage": "x"},
        {"url": "https://a.test/2", "title": "Newest B", "seendate": "20240615T100000Z",
         "domain": "a.test", "language": "English", "sourcecountry": "United States"},
    ]
}
_GDELT_PAGE_2 = {
    "articles": [
        {"url": "https://a.test/3", "title": "Older C", "seendate": "20240614T080000Z",
         "domain": "a.test", "language": "English", "sourcecountry": "France"},
    ]
}
_NEWSAPI_ERROR = {"status": "error", "code": "apiKeyMissing", "message": "Your API key is missing."}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/doc":  # GDELT
            self._send(200, _GDELT_PAGE_2 if query.get("enddatetime") else _GDELT_PAGE_1)
        elif parsed.path == "/everything":  # NewsAPI
            # No key configured in the SQL suite -> exercise the clean error path.
            self._send(401, _NEWSAPI_ERROR)
        else:
            self._send(404, {"error": "no route"})


def main() -> int:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base = f"http://{host}:{port}"

    env = dict(os.environ)
    env["VGI_NEWS_GDELT_BASE_URL"] = f"{base}/doc"
    env["VGI_NEWS_NEWSAPI_BASE_URL"] = f"{base}/everything"
    env.setdefault("VGI_NEWS_WORKER", f"{sys.executable} {ROOT / 'news_worker.py'}")
    # Keep per-call timeouts short so any accidental real-network call fails fast.
    env.setdefault("VGI_NEWS_TIMEOUT", "10")

    haybarn = env.get("HAYBARN", "haybarn-unittest")
    cmd = [haybarn, "--test-dir", str(ROOT), "test/sql/*"]
    print(f"[run_sql_e2e] mock at {base}; worker = {env['VGI_NEWS_WORKER']}")
    try:
        proc = subprocess.run(cmd, env=env, cwd=str(ROOT))
        return proc.returncode
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
