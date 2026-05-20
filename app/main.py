from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Depends
from fastapi.security import HTTPBearer

security = HTTPBearer()
import os

from app.db.database import Base, engine
from app.exceptions.custom_exception import CustomException
from app.api.v1.router import api_router

app = FastAPI(
    title="Marketplace API",
    version="1.0.0"
)

@app.get("/protected")
def protected_route(credentials=Depends(security)):
    return {"message": "Authorized"}

# CORS (important for frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware (if using JWT auth globally)
# from app.core.middleware import AuthMiddleware
# app.add_middleware(AuthMiddleware)

# Exception handler
@app.exception_handler(CustomException)
def custom_exception_handler(request, exc: CustomException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# DB init (optional; Postgres only — SQLite cannot compile some models e.g. ARRAY)
# @app.on_event("startup")
# def startup():
#     if os.getenv("AUTO_CREATE_TABLES", "false").lower() == "true":
#         # Register all models on Base.metadata before create_all
#         import app.models.address_model  # noqa: F401
#         import app.models.admin_model  # noqa: F401
#         import app.models.blog_model  # noqa: F401
#         import app.models.category_model  # noqa: F401
#         import app.models.customer_model  # noqa: F401
#         import app.models.merchant_model  # noqa: F401
#         import app.models.review_model  # noqa: F401
#         import app.models.review_moderation_history_model  # noqa: F401
#         Base.metadata.create_all(bind=engine)

@app.on_event("startup")
def startup():
    print("Creating tables...")

    import app.models.address_model
    import app.models.admin_model
    import app.models.blog_model
    import app.models.category_model
    import app.models.customer_model
    import app.models.merchant_model
    import app.models.review_model
    import app.models.review_moderation_history_model

    Base.metadata.create_all(bind=engine)

    print("Tables created.")

app.include_router(api_router, prefix="/api/v1")

# Health check
@app.get("/health")
def health():
    return {"status": "ok"}