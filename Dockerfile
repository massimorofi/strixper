# syntax=docker/dockerfile:1
#
# Halogen Strix Halo Operations Dashboard
#
# Stage 1 builds the React frontend; stage 2 runs the FastAPI backend which
# also serves the built frontend as static files.
#
# The backend supervises LLM engine containers through the docker CLI
# (LLM-Runner), reads GPU telemetry through ROCm, and reaches engine
# containers on 127.0.0.1. See the README for the complete run command.
#
# ---------- Stage 1: frontend build ----------
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Install each bundled Node.js MCP server under the shared installation root.
FROM node:22-bookworm-slim AS mcp-server-runtime
WORKDIR /mcp-servers
COPY mcp-servers/ ./
RUN npm ci --omit=dev --prefix /mcp-servers/tieline

# ---------- Stage 2: backend runtime ----------
FROM rocm/dev-ubuntu-24.04:7.2.2
WORKDIR /app/backend

# docker CLI: the LLM-Runner starts/stops engine containers through it,
# talking to the host daemon via the mounted /var/run/docker.sock.
COPY --from=docker:cli /usr/local/bin/docker /usr/local/bin/docker
# Node.js MCP servers and their dependencies live under the shared root.
COPY --from=mcp-server-runtime /usr/local /usr/local
COPY --from=mcp-server-runtime /mcp-servers /mcp-servers

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend-build /build/frontend/dist /app/frontend/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
ENV BIND_HOST=0.0.0.0 \
    BIND_PORT=8000

# Liveness probe against the backend's own health endpoint (independent of
# the Halogen engine). Reads BIND_PORT so it follows a port override.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; port=__import__('os').environ.get('BIND_PORT','8000'); r=urllib.request.urlopen('http://127.0.0.1:'+port+'/api/v1/healthz', timeout=4); sys.exit(0 if r.status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
