#!/bin/sh
# REST API only — multiple workers OK (no Engine.IO sessions on this process).
set -e

echo "Running database migrations..."
alembic upgrade head

WORKERS="${WEB_CONCURRENCY:-4}"
echo "Starting REST API with ${WORKERS} worker(s) on port ${API_PORT:-8000}..."

exec gunicorn app.main:app \
  -w "$WORKERS" \
  -k uvicorn.workers.UvicornWorker \
  -b "0.0.0.0:${API_PORT:-8000}" \
  -c gunicorn.conf.py
