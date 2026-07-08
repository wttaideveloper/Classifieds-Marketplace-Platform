import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
worker_class = "uvicorn.workers.UvicornWorker"

# Socket.IO long-polling and websocket connections
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))
worker_connections = int(os.environ.get("GUNICORN_WORKER_CONNECTIONS", "1000"))

# Do not preload — each worker must open its own Redis connection for Socket.IO.
preload_app = False
