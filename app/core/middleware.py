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
PUBLIC_ROUTES = [
    "/auth/customer/login",
    "/auth/customer/register",
    "/auth/customer/google",
    "/auth/forgot-password",
    "/auth/reset-password",
    "/docs",
    "/openapi.json"
]
class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        path = request.url.path
        # Normalize path (remove version prefix if present)
        clean_path = path.replace("/api/v1", "")

        # Allow public routes without token
        if any(clean_path.startswith(route) for route in PUBLIC_ROUTES):
            return await call_next(request)
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing Authorization header"}
            )
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid Authorization header format"}
            )
        token = auth_header.split(" ")[1]
        try:
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            cust_id = payload.get("user_id")
            print("JWT PAYLOAD:", payload)
            if not cust_id:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid token payload"}
                )
            customer = get_customer_by_id(ObjectId(cust_id))
            if not customer:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Customer not found"}
                )
            request.state.user = {
                "id": str(customer["_id"]),
                "email": customer["email"],
                "role": customer.get("role", "user")
            }
            print("USER SET:", request.state.user)
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
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(f"{request.method} {path} | {response.status_code} | {process_time:.4f}s")
        response.headers["X-Process-Time"] = str(process_time)
        return response