"""HTTP concurrency control for the form builders: `ETag` on the answers and `If-Match` on the changes, for every module.

`ETagRoute` is the route class of the routers that serve forms. Before the handler runs it reads the `If-Match` header into a context variable that the form services use as
the version the caller loaded (an explicit `base_revision` / `base_etag` in the request wins); after it, a successful JSON answer that carries a form's `revision` (Blog) or `etag`
(Event, Training) also gets the matching `ETag` header. A change made on an old copy is 409 `VERSION_CONFLICT` and changes nothing.
"""
import json
from contextvars import ContextVar

from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import Response

IF_MATCH: ContextVar[str | None] = ContextVar("form_if_match", default=None)


def parse_if_match(value: str | None) -> str | None:
    if value is None or value.strip() in ("", "*"):
        return None
    text = value.strip()
    if text[:2].upper() == "W/":
        text = text[2:]
    return text.strip().strip('"') or None


def current_if_match() -> str | None:
    return IF_MATCH.get()


class ETagRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        forms_path = "/forms" in self.path or "/form-configuration" in self.path

        async def handler(request: Request) -> Response:
            token = IF_MATCH.set(parse_if_match(request.headers.get("if-match")) if forms_path else None)
            try:
                response = await original(request)
            finally:
                IF_MATCH.reset(token)
            if forms_path and response.status_code < 300 and "etag" not in response.headers and "json" in (response.media_type or ""):
                try:
                    body = json.loads(response.body)
                except (ValueError, TypeError, AttributeError):
                    return response
                tag = body.get("etag") if isinstance(body, dict) and body.get("etag") else (body.get("revision") if isinstance(body, dict) and "definition" in body else None)
                if tag is not None:
                    response.headers["ETag"] = f'"{tag}"'
            return response

        return handler
