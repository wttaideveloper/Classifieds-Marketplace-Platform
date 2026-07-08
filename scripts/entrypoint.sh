#!/bin/sh
set -e

echo "Running database migrations..."
alembic upgrade head

WORKERS="${WEB_CONCURRENCY:-1}"
echo "Configured WEB_CONCURRENCY=${WORKERS}"

if [ "$WORKERS" -gt 1 ] && [ -z "$SOCKETIO_REDIS_URL" ]; then
  echo "ERROR: WEB_CONCURRENCY > 1 requires SOCKETIO_REDIS_URL for Socket.IO session sharing." >&2
  exit 1
fi

if [ -n "$SOCKETIO_REDIS_URL" ]; then
  echo "Waiting for Redis at ${SOCKETIO_REDIS_URL}..."
  python scripts/wait_for_redis.py
fi

echo "Starting Gunicorn with ${WORKERS} worker(s)..."
exec gunicorn app.main:socket_app -c gunicorn.conf.py
