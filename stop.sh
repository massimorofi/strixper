#!/usr/bin/env bash
# Halogen Strix Halo Operations Dashboard
# Stops the locally running backend (uvicorn) and frontend (Vite) servers.
#
# Kills by listening port (not by process-name pattern) so it never matches
# its own command line. Sends SIGTERM first, then SIGKILL after 5s if needed.
#
# Override the ports if you changed them from the defaults:
#   BIND_PORT=8010 FRONTEND_PORT=5174 ./stop.sh
set -uo pipefail

BACKEND_PORT="${BIND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

stop_port() {
  local port="$1" label="$2" pids pid
  pids=$(ss -tlnp 2>/dev/null | grep -s ":${port} " | grep -oP 'pid=\K[0-9]+' | sort -u)

  if [ -z "$pids" ]; then
    echo "[stop] ${label} (port ${port}): not running"
    return 0
  fi

  for pid in $pids; do
    echo "[stop] ${label} (port ${port}) -> PID ${pid}: terminating"
    kill "$pid" 2>/dev/null || true
  done

  # Wait up to 5s for graceful shutdown.
  for _ in 1 2 3 4 5; do
    pids=$(ss -tlnp 2>/dev/null | grep -s ":${port} " | grep -oP 'pid=\K[0-9]+' | sort -u)
    [ -z "$pids" ] && { echo "[stop] ${label} stopped."; return 0; }
    sleep 1
  done

  # Still alive -> force kill.
  for pid in $pids; do
    echo "[stop] ${label} (port ${port}) -> PID ${pid}: force killing"
    kill -9 "$pid" 2>/dev/null || true
  done
  echo "[stop] ${label} force stopped."
}

stop_port "$BACKEND_PORT" "backend"
stop_port "$FRONTEND_PORT" "frontend"
echo "[stop] done"
