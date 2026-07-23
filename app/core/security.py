# # app/core/security.py

# from datetime import datetime, timedelta
# from jose import jwt
# from passlib.context import CryptContext
# from app.core.config import settings
# import hashlib

# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# def _normalize_password(password: str) -> str:
#     # Fix bcrypt 72-byte limitation safely
#     return hashlib.sha256(password.encode()).hexdigest()

# def hash_password(password: str):
#     safe_password = _normalize_password(password)
#     return pwd_context.hash(safe_password)


# def verify_password(plain, hashed):
#     safe_password = _normalize_password(plain)
#     return pwd_context.verify(safe_password, hashed)

# def create_access_token(data: dict):
#     to_encode = data.copy()
#     expire = datetime.utcnow() + timedelta(
#         minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
#     )
#     to_encode.update({"exp": expire})
#     return jwt.encode(
#         to_encode,
#         settings.SECRET_KEY,
#         algorithm=settings.ALGORITHM
#     )

from passlib.context import CryptContext
from jose import jwt
from datetime import datetime, timedelta
from uuid import uuid4
from app.core.config import settings
import bcrypt as _bcrypt

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode('utf-8'), _bcrypt.gensalt()).decode('utf-8')

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
    except Exception:
        return False

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

def create_refresh_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(days=7)
    payload["type"] = "refresh"

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM
    )


def create_chat_access_token(user: dict) -> str:
    """Issue a short-lived token usable only by marketplace chat endpoints."""
    now = datetime.utcnow()
    payload = {
        "id": str(user["id"]),
        "sub": str(user["id"]),
        "role": user.get("role"),
        "tenant_id": str(user["tenant_id"]) if user.get("tenant_id") else None,
        "token_use": "chat",
        "scope": ["chat"],
        "iat": now,
        "exp": now + timedelta(seconds=settings.CHAT_TOKEN_EXPIRE_SECONDS),
        "jti": str(uuid4()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
