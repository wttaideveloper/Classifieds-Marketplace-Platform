"""Create each v2 service's tables in its own NEW database - one subprocess per service, so each one sees only
its own DATABASE_URL and its own declarative Base.

Refuses to touch a database that already has tables (pass --allow-existing to only add missing tables).
Never point it at the monolith's database.
"""
import os
import subprocess
import sys

SERVICES = ["identity", "catalog", "booking", "events", "learning", "commerce", "communication",
            "notifications", "content", "meetings", "moderation"]
ROOT = os.environ.get("V2_SERVICES_DIR", "/srv/services")
HOST = os.environ.get("DB_HOST", "db")
USER = os.environ.get("V2_DB_USER", "postgres")
PASSWORD = os.environ.get("V2_DB_PASSWORD", "postgres")

CHECK = (
    "import sqlalchemy as sa, os, sys; e = sa.create_engine(os.environ['DATABASE_URL']); "
    "n = len(sa.inspect(e).get_table_names()); print(n); sys.exit(3 if n and '--allow-existing' not in sys.argv else 0)"
)


def main() -> int:
    allow = "--allow-existing" in sys.argv
    failed = []
    for name in SERVICES:
        url = os.environ.get(f"{name.upper()}_DATABASE_URL") or f"postgresql://{USER}:{PASSWORD}@{HOST}:5432/{name}_db"
        env = dict(os.environ, DATABASE_URL=url, PYTHONPATH=os.path.join(ROOT, name))
        env.pop(f"{name.upper()}_DATABASE_URL", None)
        check = subprocess.run([sys.executable, "-c", CHECK] + (["--allow-existing"] if allow else []), env=env)
        if check.returncode == 3:
            print(f"{name}: database already has tables - skipped (use --allow-existing to add missing ones)")
            continue
        if check.returncode != 0:
            failed.append(name)
            continue
        r = subprocess.run([sys.executable, "-m", f"{name}_service.create_schema"], env=env)
        if r.returncode != 0:
            failed.append(name)
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("all service schemas created")
    return 0


if __name__ == "__main__":
    sys.exit(main())
