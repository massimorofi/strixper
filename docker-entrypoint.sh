#!/bin/sh
# Container entrypoint for the Strixper dashboard.
#
# exec is deliberate: it replaces this shell with uvicorn so uvicorn runs as
# PID 1 and receives docker's SIGTERM directly on `docker stop`. Without
# it the shell stays as PID 1, the backend never sees the stop signal, and
# the graceful lifespan shutdown (reaping the run pump, closing streams)
# never runs -- docker has to SIGKILL the whole tree after its grace period.
data_dir="${STRIXPER_DATA_DIR:-/app/backend/data}"
mcp_config="${data_dir}/mcp_servers.json"
if [ ! -e "$mcp_config" ]; then
  mkdir -p "$data_dir"
  cp /mcp-servers/tieline/strixper-mcp-config.json "$mcp_config"
  chmod 600 "$mcp_config"
fi
exec uvicorn app.main:app --host "${BIND_HOST}" --port "${BIND_PORT}"
