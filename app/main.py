from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Depends
from fastapi.security import HTTPBearer
from fastapi.openapi.utils import get_openapi
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
