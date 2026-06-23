#!/usr/bin/env bash
# Copyright 2026 Query Farm LLC - https://query.farm
#
# Run this repo's sqllogictest suite (test/sql/*.test) against the vgi-news
# VGI worker, using a prebuilt standalone `haybarn-unittest` and the signed
# community `vgi` extension — no C++ build from source. See ci/README.md.
#
# The GDELT / NewsAPI providers are redirected at a local canned-response mock
# HTTP server (the same routes scripts/run_sql_e2e.py serves) via the
# VGI_NEWS_GDELT_BASE_URL / VGI_NEWS_NEWSAPI_BASE_URL env vars, so the suite is
# deterministic and never egresses to a live API.
#
# Required environment:
#   HAYBARN_UNITTEST  path to the haybarn-unittest binary
#   VGI_NEWS_WORKER   worker LOCATION the .test files ATTACH (a stdio command)
# Optional:
#   STAGE             scratch dir for the preprocessed test tree (default: mktemp)
set -euo pipefail

: "${HAYBARN_UNITTEST:?path to the haybarn-unittest binary}"
: "${VGI_NEWS_WORKER:?worker LOCATION (stdio command or http:// URL)}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
STAGE="${STAGE:-$(mktemp -d)}"

echo "Staging preprocessed tests into $STAGE ..."
mkdir -p "$STAGE/test/sql"
for f in "$REPO"/test/sql/*.test; do
  awk -f "$HERE/preprocess-require.awk" "$f" > "$STAGE/test/sql/$(basename "$f")"
done

# Start the canned-response mock news server — reuse the exact routes/fixtures
# from scripts/run_sql_e2e.py. It prints `URL:<base>` once bound and then blocks;
# we read the URL, redirect both providers at it, and kill it on exit.
MOCK_OUT="$(mktemp)"
( cd "$REPO" && REPO="$REPO" uv run --no-sync python - >"$MOCK_OUT" 2>/dev/null <<'PY' ) &
import importlib.util, os, threading, time
from http.server import HTTPServer

spec = importlib.util.spec_from_file_location(
    "news_sql_e2e", os.path.join(os.environ["REPO"], "scripts", "run_sql_e2e.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

srv = HTTPServer(("127.0.0.1", 0), mod._Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
host, port = srv.server_address
print(f"URL:http://{host}:{port}", flush=True)
while True:
    time.sleep(3600)
PY
MOCK_PID=$!
cleanup() { kill "$MOCK_PID" 2>/dev/null || true; rm -f "$MOCK_OUT"; }
trap cleanup EXIT

BASE=""
for _ in $(seq 1 100); do
  BASE="$(sed -n 's/^URL:\(.*\)$/\1/p' "$MOCK_OUT" | head -n1)"
  [ -n "$BASE" ] && break
  sleep 0.1
done
if [ -z "$BASE" ]; then
  echo "ERROR: mock news server did not report a URL" >&2
  exit 1
fi
echo "Mock news server at $BASE"
export VGI_NEWS_GDELT_BASE_URL="$BASE/doc"
export VGI_NEWS_NEWSAPI_BASE_URL="$BASE/everything"
export VGI_NEWS_TIMEOUT="10"

cd "$STAGE"

# Warm the extension cache once: vgi from the signed community channel. A miss
# here is only a warning — the per-test INSTALL/LOAD (injected by
# preprocess-require.awk) is what actually gates each file.
echo "Warming the extension cache (vgi from community) ..."
mkdir -p "$STAGE/test"
cat > "$STAGE/test/_warm.test" <<'EOF'
# name: test/_warm.test
# group: [warm]
statement ok
INSTALL vgi FROM community;
EOF
"$HAYBARN_UNITTEST" "test/_warm.test" >/dev/null 2>&1 || echo "::warning::extension warm step did not fully succeed"
rm -f "$STAGE/test/_warm.test"

# Run the whole suite in one invocation, streaming the runner's native
# sqllogictest report. Any failed assertion exits non-zero and fails the job.
echo "Running suite (worker: $VGI_NEWS_WORKER) ..."
"$HAYBARN_UNITTEST" "test/sql/*"
