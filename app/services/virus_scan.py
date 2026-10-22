"""Virus scanning of uploads through a ClamAV daemon (clamd), using its INSTREAM command over TCP.

Set CLAMAV_HOST (and CLAMAV_PORT, default 3310) to turn it on. Without a host nothing is scanned and the file is marked "skipped".
With CLAMAV_REQUIRED=true an upload is refused when the scanner cannot be reached; otherwise it is accepted and marked "unscanned"
so it can be scanned later. This protects against files that carry malware; it is not a substitute for checking the image itself,
which the media service also does.
"""
import logging
import socket
import struct
from dataclasses import dataclass

from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)

CHUNK = 64 * 1024
TIMEOUT_SECONDS = 15


@dataclass
class ScanResult:
    status: str                 # clean | infected | skipped | error
    signature: str | None = None


def scan_bytes(data: bytes) -> ScanResult:
    host = (settings.CLAMAV_HOST or "").strip()
    if not host:
        return ScanResult("skipped")
    try:
        with socket.create_connection((host, settings.CLAMAV_PORT), timeout=TIMEOUT_SECONDS) as sock:
            sock.settimeout(TIMEOUT_SECONDS)
            sock.sendall(b"zINSTREAM\0")
            for start in range(0, len(data), CHUNK):
                chunk = data[start:start + CHUNK]
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
            reply = b""
            while not reply.endswith(b"\0") and len(reply) < 4096:
                part = sock.recv(1024)
                if not part:
                    break
                reply += part
    except OSError as exc:
        logger.warning("virus scan unavailable: %s", exc)
        return ScanResult("error")
    text = reply.rstrip(b"\0\n").decode("utf-8", "replace")
    if text.endswith("OK"):
        return ScanResult("clean")
    if text.endswith("FOUND"):
        return ScanResult("infected", signature=text.split(":", 1)[-1].replace("FOUND", "").strip() or None)
    logger.warning("virus scan gave an unexpected answer: %r", text[:200])
    return ScanResult("error")


def scan_or_refuse(data: bytes) -> str:
    """The scan status to store for the file, or an HTTP error when the file must not be accepted."""
    result = scan_bytes(data)
    if result.status == "infected":
        raise HTTPException(status_code=422, detail="The file was blocked by the virus scan")
    if result.status == "error":
        if settings.CLAMAV_REQUIRED:
            raise HTTPException(status_code=503, detail="Files cannot be accepted right now: the virus scanner is not available. Try again shortly")
        return "unscanned"
    return "clean" if result.status == "clean" else "skipped"
