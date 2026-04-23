from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError, ExpiredSignatureError
from app.core.config import settings
from app.repository.customer_repo import get_customer_by_id
import logging
import time
# If using logout blacklist
from app.core.token_blacklist import TOKEN_BLACKLIST

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
        # PUBLIC ROUTES
        if clean_path in PUBLIC_ROUTES:
            return await call_next(request)
        #  AUTH HEADER 
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(status_code=401, content={"detail": "Missing Authorization header"})
        if not auth_header.lower().startswith("bearer "):
            return JSONResponse(status_code=401, content={"detail": "Invalid Authorization header format"})
        token = auth_header.split(" ")[1]

        # BLACKLIST CHECK (LOGOUT SUPPORT) 
        if token in TOKEN_BLACKLIST:
            return JSONResponse(status_code=401, content={"detail": "Token has been revoked"})
        try:
            #  DECODE TOKEN
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM]
            )
            user_id = payload.get("sub")

            if not user_id:
                return JSONResponse(status_code=401, content={"detail": "Invalid token payload"})
            customer = get_customer_by_id(db=None, cust_id=user_id)
            if not customer:
                return JSONResponse(status_code=401, content={"detail": "Customer not found"})
            request.state.user = {
                "id": customer.id,
                "email": customer.email,
                "role": getattr(customer, "role", "user")
            }
        except ExpiredSignatureError:
            return JSONResponse(status_code=401, content={"detail": "Token expired"})
        except JWTError:
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.info(f"{request.method} {path} | {response.status_code} | {process_time:.4f}s")
        response.headers["X-Process-Time"] = str(process_time)
        return response