from urllib.parse import parse_qs

from jose import JWTError, jwt

from app.core.config import settings


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
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except JWTError:
        return None

    user_id = payload.get("id") or payload.get("sub")
    if not user_id:
        return None

    return {
        "id": str(user_id),
        "role": payload.get("role"),
        "email": payload.get("email"),
    }


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
