"""Gateway settings (environment variables only; no secrets are needed by the gateway)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from mskit.http_client import CallPolicy

SERVICE_NAMES = (
    "identity", "catalog", "booking", "events", "learning", "commerce",
    "communication", "notifications", "content", "meetings", "moderation",
)


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class GatewaySettings:
    # <NAME>_SERVICE_URL, e.g. CATALOG_SERVICE_URL=http://catalog:8000
    service_urls: dict[str, str] = field(default_factory=dict)
    policy: CallPolicy = field(default_factory=CallPolicy)
    socketio_path: str = "/socket.io"
    environment: str = "development"
    socketio_standalone: bool = False
    echo_request_id: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        urls = {}
        for name in SERVICE_NAMES:
            value = os.environ.get(f"{name.upper()}_SERVICE_URL", f"http://{name}:8000")
            urls[name] = value.rstrip("/")
        return cls(
            service_urls=urls,
            policy=CallPolicy.from_env("GATEWAY_"),
            socketio_path=os.environ.get("SOCKETIO_PATH", "/socket.io"),
            environment=os.environ.get("ENVIRONMENT", "development"),
            socketio_standalone=_flag("SOCKETIO_STANDALONE", False),
            echo_request_id=_flag("GATEWAY_ECHO_REQUEST_ID", True),
            log_level=os.environ.get("LOG_LEVEL", "INFO"),
        )
