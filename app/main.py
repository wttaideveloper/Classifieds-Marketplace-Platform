from fastapi import FastAPI
from app.api.v1.router import api_router
from app.db.database import connect_to_mongo, close_mongo_connection
from app.core.middleware import AuthMiddleware

app = FastAPI(title="User Management API")

app.add_middleware(AuthMiddleware)
app.include_router(api_router, prefix="/api/v1")


@app.on_event("startup")
def startup():
    connect_to_mongo()


@app.on_event("shutdown")
def shutdown():
    close_mongo_connection()