"""Serve one unchanged public recording, or explicitly select live GDELT."""

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display

ENVIRONMENT = ("VGI_NEWS_GDELT_BASE_URL", "VGI_NEWS_NEWSAPI_BASE_URL", "VGI_NEWS_TIMEOUT")


class ReplayHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        args = parse_qs(parsed.query)
        self.server.requests.append(self.path)
        expected = {
            "query": ["climate"],
            "mode": ["ArtList"],
            "format": ["json"],
            "sort": ["DateDesc"],
            "timespan": ["1d"],
        }
        valid = (
            parsed.path == "/doc"
            and args.keys() == expected.keys() | {"maxrecords"}
            and all(args.get(k) == v for k, v in expected.items())
        )
        try:
            limit = int(args.get("maxrecords", ["0"])[0])
            valid = valid and 1 <= limit <= 100
        except ValueError:
            valid = False
        if valid:
            payload = dict(self.server.payload)
            payload["articles"] = payload["articles"][:limit]
            body = json.dumps(payload).encode()
        else:
            body = b'{"error":"request outside recorded contract"}'
        self.send_response(200 if valid else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def configure(mode, limit):
    if mode not in ("replay", "live"):
        raise ValueError("DATA_MODE must be 'replay' or 'live'")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("ROW_LIMIT must be an integer from 1 through 100")
    previous = {key: os.environ.pop(key, None) for key in ENVIRONMENT}
    os.environ["VGI_NEWS_TIMEOUT"] = "45"
    directory = Path(__file__).resolve().parent / "recordings"
    provenance = json.loads((directory / "provenance.json").read_text())
    document = (directory / "gdelt-climate.json").read_bytes()
    if hashlib.sha256(document).hexdigest() != provenance["sha256"]:
        raise ValueError("Public recording checksum mismatch")
    server = thread = None
    if mode == "replay":
        server = ThreadingHTTPServer(("127.0.0.1", 0), ReplayHandler)
        server.payload = json.loads(document)
        server.requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        for key in ENVIRONMENT[:2]:
            os.environ[key] = f"http://127.0.0.1:{server.server_port}/doc"
    return server, thread, previous, provenance, datetime.now(UTC).isoformat()


def cleanup(server, thread, previous):
    if server:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def prose(text: str) -> None:
    display(Markdown(text))


def theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.dpi": 145,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#E8EDE9",
            "text.color": "#243B2F",
            "axes.labelcolor": "#405047",
            "text.parse_math": False,
            "legend.frameon": False,
        },
    )


def chart(fig, title: str, subtitle: str, source: str) -> None:
    fig.text(0.08, 0.98, title, ha="left", va="top", fontsize=17, fontweight="semibold")
    fig.text(0.08, 0.90, subtitle, ha="left", va="top", fontsize=10, color="#58685F")
    fig.tight_layout(rect=(0.02, 0.11, 1, 0.82))
    fig.text(0.08, 0.03, source, fontsize=8, color="#6C7D73")
    plt.show()
