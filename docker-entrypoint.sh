#!/bin/sh
# Container entrypoint for the Strixper dashboard.
#
# exec is deliberate: it replaces this shell with uvicorn so uvicorn runs as
# PID 1 and receives docker's SIGTERM directly on `docker stop`. Without
# it the shell stays as PID 1, the backend never sees the stop signal, and
# the graceful lifespan shutdown (reaping the run pump, closing streams)
# never runs -- docker has to SIGKILL the whole tree after its grace period.
exec uvicorn app.main:app --host "${BIND_HOST}" --port "${BIND_PORT}"
