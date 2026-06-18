from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi import Depends
from fastapi.security import HTTPBearer
import os
from dotenv import load_dotenv
from app.db.database import Base, engine
from app.exceptions.custom_exception import CustomException
from app.api.v1.router import api_router

security = HTTPBearer()

# This must happen BEFORE you check the env variables or import database blocks
load_dotenv()

app = FastAPI(
    title="Marketplace API",
    version="1.0.0"
)

print("--- DEBUG: AUTO_CREATE_TABLES is set to:", os.getenv("AUTO_CREATE_TABLES"))

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
            
            print("REGISTERED TABLES:")
            print(Base.metadata.tables.keys())

            Base.metadata.create_all(bind=engine)

            print("Tables created successfully")

        except Exception as e:
            print("TABLE CREATION ERROR:", e)

# Routes
app.include_router(api_router, prefix="/api/v1")

@app.get("/health")
def health():
    return {"status": "ok"}