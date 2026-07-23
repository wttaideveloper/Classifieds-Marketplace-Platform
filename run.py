from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

_ROOT = Path(__file__).resolve().parent
_ENV_FILE = _ROOT / ".env"

if not _ENV_FILE.exists():
    raise SystemExit(
        "Missing .env file.\n"
        "Copy .env.example to .env and set your local values:\n"
        "  copy .env.example .env"
    )

load_dotenv(_ENV_FILE)


def _check_database() -> bool:
    from app.db.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def _print_db_setup_help():
    print(
        "\nPostgreSQL is not running or not reachable.\n"
        "\nOption 1 — Docker (recommended):\n"
        "  docker compose up -d db\n"
        "  alembic upgrade head\n"
        "  python run.py\n"
        "\nOption 2 — Install PostgreSQL on Windows:\n"
        "  https://www.postgresql.org/download/windows/\n"
        "  Create a database named: marketplace_db\n"
        "  Update DATABASE_URL in .env with your credentials\n"
        "  alembic upgrade head\n"
        "  python run.py\n"
    )


if __name__ == "__main__":
    if not _check_database():
        _print_db_setup_help()
        raise SystemExit(1)

    uvicorn.run("app.main:socket_app", reload=True)