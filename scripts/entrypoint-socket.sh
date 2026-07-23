#!/bin/sh
# Socket.IO + FastAPI combined entrypoint — MUST be 1 worker (Engine.IO polling sessions are in-memory).
set -e

echo "Running database migrations..."
alembic upgrade head

if [ -n "${WEB_CONCURRENCY}" ] && [ "${WEB_CONCURRENCY}" -gt 1 ]; then
  echo "WARNING: WEB_CONCURRENCY=${WEB_CONCURRENCY} breaks Engine.IO polling." >&2
  echo "         Forcing SOCKET_WORKERS=1. Use scripts/entrypoint-api.sh + entrypoint-socket.sh for scale." >&2
fi

WORKERS="${SOCKET_WORKERS:-1}"
echo "Starting Socket.IO server with ${WORKERS} worker(s) on port ${SOCKET_PORT:-8000}..."

if [ -n "$SOCKETIO_REDIS_URL" ]; then
  echo "Waiting for Redis at ${SOCKETIO_REDIS_URL}..."
  python scripts/wait_for_redis.py
fi

exec gunicorn app.main:socket_app \
  -w "$WORKERS" \
  -k uvicorn.workers.UvicornWorker \
  -b "0.0.0.0:${SOCKET_PORT:-8000}" \
  -c gunicorn.conf.py
