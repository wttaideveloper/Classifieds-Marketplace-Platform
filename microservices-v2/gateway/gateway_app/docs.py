"""API documentation served by the gateway from snapshots of the monolith's own docs.

``openapi.snapshot.json`` is the monolith's ``app.openapi()`` and ``docs_pages.snapshot.json`` the
Swagger UI / ReDoc pages it served. Regenerate both when the public API changes
(``python microservices-v2/tools/snapshot_docs.py``). The JSON is rendered the way FastAPI's
``JSONResponse`` renders it, so the bytes match the monolith's ``/openapi.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS_PATHS = ("/docs", "/api/docs", "/redoc", "/api/redoc", "/openapi.json", "/api/openapi.json")


class DocsSnapshot:
    def __init__(self):
        self._openapi: bytes | None = None
        self._pages: dict | None = None

    def _load(self):
        if self._openapi is None:
            schema = json.loads((HERE / "openapi.snapshot.json").read_text(encoding="utf-8"))
            self._openapi = json.dumps(schema, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")
            self._pages = json.loads((HERE / "docs_pages.snapshot.json").read_text(encoding="utf-8"))

    async def serve(self, scope, send):
        if scope["method"] not in ("GET", "HEAD"):
            body = b'{"detail":"Method Not Allowed"}'
            headers = [(b"content-type", b"application/json"), (b"allow", b"GET, HEAD"), (b"content-length", str(len(body)).encode())]
            await send({"type": "http.response.start", "status": 405, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return
        self._load()
        path = scope["path"]
        if path.endswith("openapi.json"):
            body, ctype = self._openapi, b"application/json"
        else:
            body, ctype = self._pages[path].encode("utf-8"), b"text/html; charset=utf-8"
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", ctype), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else body})
