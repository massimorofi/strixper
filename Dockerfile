# syntax=docker/dockerfile:1
#
# Halogen Strix Halo Operations Dashboard
#
# Stage 1 builds the React frontend; stage 2 runs the FastAPI backend which
# also serves the built frontend as static files.

# ---------- Stage 1: frontend build ----------
FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: backend runtime ----------
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

EXPOSE 8000
ENV BIND_HOST=0.0.0.0 \
    BIND_PORT=8000

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
