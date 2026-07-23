#!/bin/sh
# Default production entrypoint — combined REST + Socket.IO on one port, single worker.
set -e

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

export SOCKET_WORKERS=1
export WEB_CONCURRENCY=1

exec "$SCRIPT_DIR/entrypoint-socket.sh"
