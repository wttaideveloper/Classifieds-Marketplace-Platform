#!/usr/bin/env python3
"""Block until Redis is reachable (used before multi-worker Gunicorn start)."""

import os
import sys
import time

import redis


def main() -> int:
    redis_url = os.environ.get("SOCKETIO_REDIS_URL", "").strip()
    if not redis_url:
        return 0

    attempts = int(os.environ.get("REDIS_WAIT_ATTEMPTS", "30"))
    delay = float(os.environ.get("REDIS_WAIT_DELAY", "1"))

    for attempt in range(1, attempts + 1):
        try:
            client = redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            print(f"Redis is ready at {redis_url}")
            return 0
        except Exception as exc:
            print(
                f"Waiting for Redis ({attempt}/{attempts}) at {redis_url}: {exc}",
                file=sys.stderr,
            )
            time.sleep(delay)

    print(f"ERROR: Redis not available at {redis_url}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
