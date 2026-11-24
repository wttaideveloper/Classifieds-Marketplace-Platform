"""Per-service database access. Every service owns ONE database and its own declarative ``Base``."""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(url, pool_pre_ping=True)
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False)

    def get_db(self):
        db = self.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def ping(self) -> str:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return "ok"
        except Exception as exc:  # noqa: BLE001
            return f"error: {type(exc).__name__}"
