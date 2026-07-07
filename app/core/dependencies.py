from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def get_dev_user() -> dict:
    return {
        "id": settings.DEV_DEFAULT_USER_ID,
        "role": settings.DEV_DEFAULT_USER_ROLE,
        "email": "dev@localhost",
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
):
    if credentials is None or not credentials.credentials:
        if settings.is_production:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
            )
        return get_dev_user()

    token = credentials.credentials
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = payload.get("id") or payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    return {
        "id": user_id,
        "role": payload.get("role"),
        "email": payload.get("email"),
    }


def require_roles(allowed_roles: list):
    def role_checker(current_user=Depends(get_current_user)):
        user_role = current_user.get("role")
        if user_role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Not authorized")
        return current_user

    return role_checker


def get_current_admin(current_user=Depends(get_current_user)):
    if current_user.get("role") != "admin":
        if not settings.is_production and current_user.get("id") == settings.DEV_DEFAULT_USER_ID:
            return {**current_user, "role": "admin"}
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
