#!/usr/bin/env bash
set -euo pipefail

CONTAINER_NAME="${STRIXPER_CONTAINER_NAME:-strixper}"
IMAGE="${STRIXPER_IMAGE:-strixper-dashboard:latest}"
BIND_PORT="${BIND_PORT:-8000}"
ACTION="${1:-start}"

usage() {
  echo "Usage: $0 {start|stop|restart|recreate|status}" >&2
  exit 2
}

[[ $# -le 1 ]] || usage
case "$ACTION" in
  start|stop|restart|recreate|status) ;;
  *) usage ;;
esac

for command in docker curl python3; do
  command -v "$command" >/dev/null 2>&1 || {
    echo "[strixper] ERROR: required command not found: $command" >&2
    exit 1
  }
done

docker info >/dev/null

container_exists() {
  docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")" == "true" ]]
}

container_port() {
  local configured
  configured="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "$CONTAINER_NAME" | sed -n 's/^BIND_PORT=//p' | head -n 1)"
  printf '%s' "${configured:-$BIND_PORT}"
}

wait_for_api() {
  local port="$1" attempt
  for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error --max-time 2 \
      "http://127.0.0.1:${port}/api/v1/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "[strixper] ERROR: dashboard API did not become ready on port ${port}" >&2
  return 1
}

start_container() {
  if container_exists; then
    if container_running; then
      echo "[strixper] ${CONTAINER_NAME} is already running."
      return 0
    fi
    echo "[strixper] Starting existing container ${CONTAINER_NAME}."
    docker start "$CONTAINER_NAME" >/dev/null
    wait_for_api "$(container_port)"
    return
  fi

  docker image inspect "$IMAGE" >/dev/null 2>&1 || {
    echo "[strixper] ERROR: image not found: ${IMAGE}. Build it first or set STRIXPER_IMAGE." >&2
    exit 1
  }

  [[ -c /dev/kfd ]] || {
    echo "[strixper] ERROR: /dev/kfd is missing; ROCm device access is unavailable." >&2
    exit 1
  }
  [[ -d /dev/dri ]] || {
    echo "[strixper] ERROR: /dev/dri is missing; ROCm device access is unavailable." >&2
    exit 1
  }

  local video_gid render_gid
  video_gid="$(getent group video | cut -d: -f3)"
  render_gid="$(getent group render | cut -d: -f3)"
  [[ -n "$video_gid" && -n "$render_gid" ]] || {
    echo "[strixper] ERROR: could not resolve host video/render group IDs." >&2
    exit 1
  }

  echo "[strixper] Creating ${CONTAINER_NAME} from ${IMAGE}."
  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --network=host \
    -e BIND_HOST=0.0.0.0 \
    -e "BIND_PORT=${BIND_PORT}" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /etc/group:/etc/group:ro \
    -v strixper-data:/app/backend/data \
    --device=/dev/kfd \
    --device=/dev/dri \
    --group-add "$video_gid" \
    --group-add "$render_gid" \
    "$IMAGE" >/dev/null
  wait_for_api "$BIND_PORT"
  echo "[strixper] Dashboard is ready at http://<host-lan-ip>:${BIND_PORT}"
}

stop_stack() {
  if ! container_exists; then
    echo "[strixper] ${CONTAINER_NAME} does not exist; nothing to stop."
    return 0
  fi

  # Startup adoption discovers a configured engine that outlived the
  # dashboard process, allowing the normal Runner stop path to stop it.
  if ! container_running; then
    echo "[strixper] Starting dashboard briefly to locate any running engine."
    docker start "$CONTAINER_NAME" >/dev/null
  fi

  local port active_json run_id engine_names engine_name running
  port="$(container_port)"
  wait_for_api "$port"
  active_json="$(curl --fail --silent --show-error --max-time 10 \
    "http://127.0.0.1:${port}/api/v1/runner/active")" || {
      echo "[strixper] ERROR: could not query the active LLM-Runner process; leaving dashboard running." >&2
      return 1
    }
  run_id="$(printf '%s' "$active_json" | python3 -c \
    'import json,sys; data=json.load(sys.stdin); active=data.get("active"); print(active.get("run_id", "") if isinstance(active, dict) else "")')" || {
      echo "[strixper] ERROR: could not parse the active LLM-Runner response; leaving dashboard running." >&2
      return 1
    }

  engine_names="$(python3 - "$port" <<'PY'
import json
import re
import sys
import urllib.request

base = f"http://127.0.0.1:{sys.argv[1]}/api/v1"

def request(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)

configs = request("/runner/configs").get("configs", [])
names = set()
for config in configs:
    preview = request("/runner/preview", {
        "docker_command": config.get("docker_command", ""),
        "parameters": config.get("parameters") or [],
    })
    match = re.search(r"--name(?:\s+|=)(\S+)", preview.get("command", ""))
    if match and not match.group(1).startswith("{"):
        names.add(match.group(1))

for name in sorted(names):
    print(name)
PY
  )" || {
    echo "[strixper] ERROR: could not resolve saved engine container names; leaving dashboard running." >&2
    return 1
  }

  if [[ -n "$run_id" ]]; then
    echo "[strixper] Stopping active LLM engine run ${run_id}."
    curl --fail --silent --show-error --max-time 45 \
      -X POST "http://127.0.0.1:${port}/api/v1/runner/runs/${run_id}/stop" >/dev/null || {
        echo "[strixper] ERROR: engine stop failed; leaving dashboard running." >&2
        return 1
      }
  else
    echo "[strixper] No active configured LLM engine run found."
  fi

  while IFS= read -r engine_name; do
    [[ -n "$engine_name" ]] || continue
    if running="$(docker inspect --format '{{.State.Running}}' "$engine_name" 2>/dev/null)"; then
      if [[ "$running" == "true" ]]; then
        echo "[strixper] Stopping configured engine container ${engine_name}."
        docker stop --time 30 "$engine_name" >/dev/null || {
          echo "[strixper] ERROR: could not stop engine container ${engine_name}; leaving dashboard running." >&2
          return 1
        }
      fi
    fi
  done <<< "$engine_names"

  echo "[strixper] Stopping dashboard container."
  docker stop --time 30 "$CONTAINER_NAME" >/dev/null
  echo "[strixper] Dashboard and configured engine containers are stopped."
}

case "$ACTION" in
  start)
    start_container
    ;;
  stop)
    stop_stack
    ;;
  restart)
    stop_stack
    start_container
    ;;
  recreate)
    stop_stack
    if container_exists; then
      docker rm "$CONTAINER_NAME" >/dev/null
    fi
    start_container
    ;;
  status)
    if ! container_exists; then
      echo "[strixper] ${CONTAINER_NAME} does not exist."
      exit 0
    fi
    docker ps -a --filter "name=^/${CONTAINER_NAME}$" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
    if container_running; then
      curl --fail --silent --show-error --max-time 10 \
        "http://127.0.0.1:$(container_port)/api/v1/runner/active" | \
        python3 -c 'import json,sys; data=json.load(sys.stdin); active=data.get("active"); print("[strixper] Active engine:", active.get("container_name") or active.get("run_id") if active else "none")'
    fi
    ;;
esac
