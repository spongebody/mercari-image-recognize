#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8000}"
UI_HOST="${UI_HOST:-0.0.0.0}"
UI_PORT="${UI_PORT:-8001}"

if [[ -f "$ROOT_DIR/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.venv/bin/activate"
fi

cleanup() {
  if [[ -n "${API_PID:-}" ]] && kill -0 "$API_PID" >/dev/null 2>&1; then
    kill "$API_PID" >/dev/null 2>&1 || true
  fi
  if [[ -n "${UI_PID:-}" ]] && kill -0 "$UI_PID" >/dev/null 2>&1; then
    kill "$UI_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Starting API server on http://localhost:${API_PORT} ..."
python -m uvicorn main:app --host "${API_HOST}" --port "${API_PORT}" &
API_PID=$!

echo "Starting UI server on http://localhost:${UI_PORT} ..."
python -m http.server "${UI_PORT}" --directory "${ROOT_DIR}/web" --bind "${UI_HOST}" &
UI_PID=$!

cat <<EOF

Servers are up:
- API: http://localhost:${API_PORT}
- UI:  http://localhost:${UI_PORT}

UI tips:
- Set API endpoint to: http://localhost:${API_PORT}/api/v1/mercari/image/analyze

Press Ctrl+C to stop.
EOF

wait "$API_PID" "$UI_PID"
