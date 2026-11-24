"""Create this service's tables in a NEW, empty database (its own DATABASE_URL).

    python -m events_service.create_schema
"""
from events_service import model_registry  # noqa: F401
from events_service.db.database import Base, engine

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("created:", sorted(Base.metadata.tables))
