"""Rewrite http:// links to https:// in Training API JSON responses.

Pure ASGI (not BaseHTTPMiddleware) so nothing but matching JSON responses is ever buffered: file
downloads, streams and every other route pass straight through. The body is re-serialized only if a
link actually changed, so unaffected responses are byte-for-byte identical.
"""
import json

from app.core.config import settings
from app.utils.public_urls import deep_https

# Every training API: learner + admin routes, the Course alias, and search (which returns trainings).
HTTPS_REWRITE_PATH_PREFIXES = (
    "/api/v1/trainings",
    "/api/v1/courses",
    "/api/v1/admin/trainings",
    "/api/v1/admin/courses",
    "/api/v1/search",
)


def _matches(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in HTTPS_REWRITE_PATH_PREFIXES)


class HttpsLinkRewriteMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _matches(scope.get("path", "")) or not settings.force_https_media_urls:
            await self.app(scope, receive, send)
            return

        start_message = None
        chunks: list[bytes] = []
        passthrough = False

        async def rewriting_send(message):
            nonlocal start_message, passthrough
            if passthrough:
                await send(message)
                return

            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                is_json = headers.get(b"content-type", b"").lower().startswith(b"application/json")
                if not is_json or headers.get(b"content-encoding") or message.get("status") in (204, 304):
                    passthrough = True
                    await send(message)
                    return
                start_message = message  # hold until the whole body is known
                return

            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                try:
                    data = json.loads(body)
                    if deep_https(data, enabled=True):
                        body = json.dumps(
                            data, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")
                        ).encode("utf-8")
                except ValueError:
                    pass  # not valid JSON after all — send as is
                headers = [(k, v) for k, v in start_message["headers"] if k.lower() != b"content-length"]
                headers.append((b"content-length", str(len(body)).encode("latin-1")))
                await send({**start_message, "headers": headers})
                await send({"type": "http.response.body", "body": body})
                return

            await send(message)

        await self.app(scope, receive, rewriting_send)
