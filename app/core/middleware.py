# app/core/middleware.py

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError, ExpiredSignatureError
from app.core.config import settings
from app.repository.user_repo import get_user_by_id
import logging
import time

logger = logging.getLogger(__name__)

PUBLIC_ROUTES = [
    "/api/v1/users/login",
    "/api/v1/users/register",
    "/docs",
    "/openapi.json"
]

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):

        start_time = time.time()
        path = request.url.path

        # Skip public routes
        if path in PUBLIC_ROUTES:
            response = await call_next(request)
            return response

        auth_header = request.headers.get("Authorization")

        if not auth_header or not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing Authorization header"}
            )

        token = auth_header.split(" ")[1]

        try:
            # Decode JWT
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )

            user_id = payload.get("user_id")

            if not user_id:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid token payload"}
                )

            # Fetch user from DB
            user = get_user_by_id(user_id)

            if not user:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "User not found"}
                )

            # Attach user to request
            request.state.user = user

        except ExpiredSignatureError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Token expired"}
            )

        except JWTError:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid token"}
            )

        # Continue request
        response = await call_next(request)

        # Logging
        process_time = time.time() - start_time
        logger.info(f"{request.method} {path} completed in {process_time:.4f}s")

        # Optional response header
        response.headers["X-Process-Time"] = str(process_time)

        return response