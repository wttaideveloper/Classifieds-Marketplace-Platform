from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.database import Base, engine
from app.api.v1.router import api_router
from app.exceptions.custom_exception import CustomException

app = FastAPI(
    title="User Management API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
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


@app.on_event("startup")
def startup():
    if not settings.is_production:
        Base.metadata.create_all(bind=engine)

    for route in app.routes:
        print(route.path, route.methods)

# Routes
app.include_router(api_router, prefix="/api/v1")


# Health check
@app.get("/health")
def health():
    return {"status": "ok"}
