from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError, ExpiredSignatureError
from app.core.config import settings
from app.repository.customer_repo import get_customer_by_id
from bson import ObjectId
import logging
import time

logger = logging.getLogger(__name__)


PUBLIC_ROUTES = {
    "/auth/customer/login",
    "/auth/customer/register",
    "/auth/customer/google",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/docs",
    "/openapi.json"
}


class AuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        path = request.url.path
        clean_path = path.replace("/api/v1", "")

        # PUBLIC ROUTES CHECK
        if clean_path in PUBLIC_ROUTES:
            return await call_next(request)

        # AUTH HEADER CHECK
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(401, {"detail": "Missing Authorization header"})

        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(401, {"detail": "Invalid Authorization header format"})

        token = auth_header.split(" ")[1]

        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )

            cust_id = (
                payload.get("cust_id")
                or payload.get("sub")
                or payload.get("user_id")
            )

            if not cust_id:
                return JSONResponse(401, {"detail": "Invalid token payload"})

            try:
                customer = get_customer_by_id(ObjectId(cust_id))
            except Exception:
                return JSONResponse(401, {"detail": "Invalid user ID"})

            if not customer:
                return JSONResponse(401, {"detail": "Customer not found"})

            request.state.cust = {
                "id": str(customer["_id"]),
                "email": customer["email"],
                "role": customer.get("role", "user")
            }

        except ExpiredSignatureError:
            return JSONResponse(401, {"detail": "Token expired"})

        except JWTError:
            return JSONResponse(401, {"detail": "Invalid token"})

        response = await call_next(request)

        process_time = time.time() - start_time
        logger.info(f"{request.method} {path} | {response.status_code} | {process_time:.4f}s")

        response.headers["X-Process-Time"] = str(process_time)

        return response