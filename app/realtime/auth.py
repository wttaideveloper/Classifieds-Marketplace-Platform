from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qs

from app.core.config import settings
from app.core.token_auth import resolve_chat_user_from_token_or_raise


def get_dev_user() -> dict:
    return {
        "id": settings.DEV_DEFAULT_USER_ID,
        "role": settings.DEV_DEFAULT_USER_ROLE,
        "email": "dev@localhost",
    }


def authenticate_token(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        return resolve_chat_user_from_token_or_raise(token)
    except Exception:
        return None


def extract_token_from_environ(environ: dict, auth: dict | None) -> str | None:
    # A Web Auth BFF can resolve its HttpOnly session and inject the same
    # server-side Bearer header used for REST. Never require browser JS to
    # read the primary token. ASGI headers also cover native ASGI adapters.
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in (environ.get("asgi.scope", {}).get("headers") or [])
    }
    authorization = environ.get("HTTP_AUTHORIZATION") or headers.get("authorization", "")
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and credential.strip():
            return credential.strip()

    if auth and isinstance(auth, dict):
        token = auth.get("token")
        if token:
            return token

    cookie_header = environ.get("HTTP_COOKIE") or headers.get("cookie", "")
    if cookie_header:
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header)
        except (CookieError, ValueError):
            cookies = SimpleCookie()
        for name in settings.web_session_cookie_names:
            session_cookie = cookies.get(name)
            if session_cookie and session_cookie.value:
                return session_cookie.value

    query_string = environ.get("QUERY_STRING", "")
    if query_string:
        params = parse_qs(query_string)
        tokens = params.get("token")
        if tokens:
            return tokens[0]

    return None
