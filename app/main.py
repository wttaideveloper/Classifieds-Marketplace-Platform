from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Depends
from fastapi.security import HTTPBearer
from dotenv import load_dotenv
from fastapi.openapi.utils import get_openapi
from app.exceptions.custom_exception import CustomException
from app.api.v1.router import api_router

security = HTTPBearer()

# This must happen BEFORE you check the env variables or import database blocks
load_dotenv()

app = FastAPI(
    title="Marketplace API",
    description="""
    Marketplace Management APIs

    Modules:
    - Enterprise Management
    - Product Management
    - Service Management
    - Dynamic Attributes
    - Inventory System
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
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
        try:
            import app.models.attribute_model
            import app.models.enterprise_model
            import app.models.product_model
            import app.models.service_model
            

            Base.metadata.create_all(bind=engine)


        except Exception as e:
            raise e

# Routes
app.include_router(api_router, prefix="/api/v1")


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    for path_item in openapi_schema.get("paths", {}).values():
        for method, operation in path_item.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                operation.setdefault("x-rate-limit", "100 requests/minute/user")

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/health")
def health():
    return {"status": "ok"}
