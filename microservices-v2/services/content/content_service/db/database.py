"""The content service's own database (DATABASE_URL) and declarative Base."""
import os

from sqlalchemy.orm import declarative_base

from mskit.db import Database
from mskit.settings import settings

# CONTENT_DATABASE_URL wins over DATABASE_URL (several services in one process, e.g. integration tests)
database = Database(os.environ.get("CONTENT_DATABASE_URL") or settings.DATABASE_URL)
engine = database.engine
SessionLocal = database.SessionLocal
Base = declarative_base()


def get_db():
    yield from database.get_db()
