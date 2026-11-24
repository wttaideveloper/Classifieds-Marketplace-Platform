"""Copy this service's tables from the monolith database (dry run unless --execute).

    python -m notifications_service.copy_data --source-url postgresql://user@host/marketplace_db [--execute]
"""
import sys

from mskit.datacopy import cli
from mskit.settings import settings

from notifications_service.db.database import Base
from notifications_service.model_registry import OWNED_TABLES, REPLICA_TABLES

if __name__ == "__main__":
    # own tables + the initial fill of replica tables (afterwards kept current by the owner's pushes/sync)
    sys.exit(cli(Base.metadata, OWNED_TABLES + REPLICA_TABLES, settings.DATABASE_URL))  # NEW_TABLES start empty
