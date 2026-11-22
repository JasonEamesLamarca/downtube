#!/bin/bash
# Run a local demo instance for screenshots.
#   run.sh <instance> <port> [seed]
# instance "a" stays in setup mode; any other instance is seeded with demo data.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEMO="${DEMO_DIR:?set DEMO_DIR to a scratch folder}"
PY="${PYTHON:-python}"
mkdir -p "$DEMO/$1"/{data,temp,logs,archive} "$DEMO/static"
[ -f "$DEMO/static/index.html" ] || cp -r "$REPO/frontend/dist/." "$DEMO/static/"
mkdir -p "$DEMO/static/demo"
export DOWNTUBE_SECRET_KEY=demo-secret-key-0123456789abcdef-0123456789 DOWNTUBE_STATIC_DIR="$DEMO/static" \
  DOWNTUBE_DATA_DIR="$DEMO/$1/data" DOWNTUBE_TEMP_DIR="$DEMO/$1/temp" DOWNTUBE_LOG_DIR="$DEMO/$1/logs" \
  DOWNTUBE_STORAGE_MOUNTS="$DEMO/$1/archive" DOWNTUBE_ARCHIVE_ROOT="$DEMO/$1/archive" \
  DOWNTUBE_HOST_ARCHIVE_PATH=/volume1/video/DownTube DOWNTUBE_BASE_URL="http://localhost:$2" DOWNTUBE_DISABLE_BACKGROUND=1
cd "$REPO/backend"
if [ "${3:-}" = seed ]; then exec "$PY" "$HERE/seed.py" "$DEMO/static"; fi
exec env PYTHONPATH="$HERE" "$PY" -m uvicorn demo_app:create_app --factory --port "$2"
