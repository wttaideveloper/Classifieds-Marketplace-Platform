"""Consistent log format for the gateway and all services.

Format: ``<time> <level> service=<name> request_id=<id> <logger>: <message>``

Set ``LOG_FORMAT=json`` for one JSON object per line (for log aggregators).
"""
from __future__ import annotations

import json
import logging
import os

from mskit.correlation import current_request_id

_configured = False


class _ContextFilter(logging.Filter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service
        record.request_id = current_request_id()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "service": getattr(record, "service", "-"),
            "request_id": getattr(record, "request_id", "-"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service: str, level: str | None = None) -> None:
    """Configure the root logger once per process.

    Must run before the legacy domain code is imported: the monolith calls
    ``logging.basicConfig`` at import time of ``app.main`` and nowhere else, so without
    this every ``logger.info`` in the domain code would be dropped (root default WARNING).
    """
    global _configured
    if _configured:
        return
    level_name = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter(service))
    if os.environ.get("LOG_FORMAT", "text").lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s service=%(service)s request_id=%(request_id)s %(name)s: %(message)s"
            )
        )
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level_name, logging.INFO))
    _configured = True
