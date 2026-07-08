#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

WORKERS="${WEB_CONCURRENCY:-1}"
echo "Starting application with ${WORKERS} worker(s)..."
if [ "$WORKERS" -gt 1 ] && [ -z "$SOCKETIO_REDIS_URL" ]; then
  echo "ERROR: WEB_CONCURRENCY > 1 requires SOCKETIO_REDIS_URL for Socket.IO session sharing." >&2
  exit 1
fi

exec gunicorn app.main:socket_app \
  -w "$WORKERS" \
  -k uvicorn.workers.UvicornWorker \
  -b 0.0.0.0:8000
