from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Depends
from fastapi.security import HTTPBearer
import os
from app.db.database import Base, engine
from app.exceptions.custom_exception import CustomException
from app.api.v1.router import api_router

security = HTTPBearer()

app = FastAPI(
    title="Marketplace API",
    version="1.0.0"
)

@app.get("/protected")
def protected_route(credentials=Depends(security)):
    return {"message": "Authorized"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(CustomException)
def custom_exception_handler(request, exc: CustomException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# DB init (optional; Postgres only — SQLite cannot compile some models e.g. ARRAY)
@app.on_event("startup")
def startup():
    if os.getenv("AUTO_CREATE_TABLES", "false").lower() == "true":
        import app.models.address_model  # noqa: F401
        import app.models.admin_model  # noqa: F401
        import app.models.blog_model  # noqa: F401
        import app.models.category_model  # noqa: F401
        import app.models.customer_model  # noqa: F401
        import app.models.merchant_model  # noqa: F401
        import app.models.order_model  # noqa: F401
        import app.models.review_model  # noqa: F401
        import app.models.review_moderation_history_model  # noqa: F401
        Base.metadata.create_all(bind=engine)

# Routes
app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
def health():
    return {"status": "ok"}