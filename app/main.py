import logging

import socketio
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db.database import Base, engine
from app.api.v1.router import api_router
from app.exceptions.custom_exception import CustomException
from app.realtime.server import SOCKETIO_PATH, sio

import app.realtime.events  # noqa: F401, E402 — register Socket.IO handlers

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Classifieds Marketplace Platform API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(CustomException)
def custom_exception_handler(request, exc: CustomException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Unhandled error on %s %s",
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.on_event("startup")
def startup():
    if not settings.is_production and settings.AUTO_CREATE_TABLES:
        try:
            Base.metadata.create_all(bind=engine)
            logger.info("Database connected and tables verified.")
        except OperationalError:
            logger.warning(
                "Database not reachable — start PostgreSQL with: docker compose up -d db"
            )


app.include_router(api_router, prefix="/api/v1")


socket_app = socketio.ASGIApp(
    sio,
    other_asgi_app=app,
    socketio_path=SOCKETIO_PATH,
)


@app.get("/health")
def health():
    return {"status": "ok"}
