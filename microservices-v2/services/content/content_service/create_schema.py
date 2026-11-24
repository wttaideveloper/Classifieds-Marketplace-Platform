"""Create this service's tables in a NEW, empty database (its own DATABASE_URL).

    python -m content_service.create_schema
"""
from content_service import model_registry  # noqa: F401
from content_service.db.database import Base, engine

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("created:", sorted(Base.metadata.tables))
