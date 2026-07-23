from urllib.parse import parse_qs

from app.core.config import settings
from app.core.token_auth import resolve_user_from_token


def get_dev_user() -> dict:
    return {
        "id": settings.DEV_DEFAULT_USER_ID,
        "role": settings.DEV_DEFAULT_USER_ROLE,
        "email": "dev@localhost",
    }


def authenticate_token(token: str | None) -> dict | None:
    return resolve_user_from_token(token)


def extract_token_from_environ(environ: dict, auth: dict | None) -> str | None:
    if auth and isinstance(auth, dict):
        token = auth.get("token")
        if token:
            return token

    query_string = environ.get("QUERY_STRING", "")
    if query_string:
        params = parse_qs(query_string)
        tokens = params.get("token")
        if tokens:
            return tokens[0]

    return None
