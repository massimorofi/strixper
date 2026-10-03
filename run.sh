#!/usr/bin/env bash
# Halogen Strix Halo Operations Dashboard
# Starts the FastAPI backend and the Vite dev server concurrently.
#
# Usage: ./run.sh
#   Frontend: http://localhost:5173
#   Backend:  http://localhost:8000  (API under /api/v1)
#
# Override ports if you have a conflict:
#   BIND_PORT=8010 FRONTEND_PORT=5174 ./run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIND_PORT="${BIND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# ---- backend deps ----
if [ ! -d "$ROOT/backend/.venv" ]; then
  echo "[setup] creating Python venv..."
  python3 -m venv "$ROOT/backend/.venv"
fi
"$ROOT/backend/.venv/bin/pip" install -q -r "$ROOT/backend/requirements.txt"

# ---- frontend deps ----
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "[setup] installing frontend dependencies..."
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund)
fi

cleanup() {
  echo
  echo "[run] shutting down..."
  kill 0 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "[run] backend  -> http://localhost:${BIND_PORT}   (API: /api/v1)"
echo "[run] frontend -> http://localhost:${FRONTEND_PORT}"

(
  cd "$ROOT/backend"
  exec "$ROOT/backend/.venv/bin/python" -m uvicorn app.main:app \
    --host 0.0.0.0 --port "$BIND_PORT"
) &

(
  cd "$ROOT/frontend"
  # Tell the Vite proxy where the backend lives (matches BIND_PORT).
  BACKEND_URL="http://127.0.0.1:${BIND_PORT}" \
    exec npm run dev -- --host --port "$FRONTEND_PORT"
) &

wait
