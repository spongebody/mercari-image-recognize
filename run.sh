#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8008}"

echo "Starting server on http://localhost:${API_PORT} ..."
echo "The test UI is served by the same server, so the API endpoint field can be left blank."

cat <<EOF

Once it is up:
- Test UI: http://localhost:${API_PORT}/
- API:     http://localhost:${API_PORT}/api/v1/mercari/image/analyze
- Price:   http://localhost:${API_PORT}/api/v1/mercari/image/price
- Logs:    http://localhost:${API_PORT}/logs
- Config:  http://localhost:${API_PORT}/config

Press Ctrl+C to stop.
EOF

exec uv run uvicorn main:app --host "${API_HOST}" --port "${API_PORT}"
