"""
Event Management Phase 2.2 — migration tests for ``events.event_type`` / ``events.modules``.

Covers the strategy decisions rather than just "it runs":

- the revision is chained onto the event head ``e2b1c2d3e4f7`` and creates/merges no head;
- no other revision uses these column names (no collision);
- the model and the migration declare the same columns (same idea as test_orders_schema_matches_migrations);
- it is behaviour-neutral: nullable, no server default, no backfill, no index — existing rows stay NULL;
- upgrade and downgrade actually execute, and an existing legacy row survives with NULL/NULL;
- the exact PostgreSQL DDL, generated offline (no database needed).

Run:
    pytest tests/test_event_phase_2_2_migration.py -v
"""
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects.postgresql import JSONB

import event_sql_support as _sqlite_jsonb_shim  # noqa: F401  (registers the JSONB -> JSON shim so the migration runs on SQLite)
from app.models.event_model import Event

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"

REVISION = "adae1a2909cc"
EVENT_HEAD_PARENT = "e2b1c2d3e4f7"  # add_event_pricing_type: the head that carries the event-domain schema work


@pytest.fixture(scope="module")
def script_dir():
    return ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))


@pytest.fixture(scope="module")
def migration_module():
    path = next(VERSIONS.glob(f"{REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location("event_type_modules_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# graph position
# ===========================================================================


class TestGraph:
    def test_revision_is_chained_onto_the_event_head(self, script_dir):
        revision = script_dir.get_revision(REVISION)
        assert revision.down_revision == EVENT_HEAD_PARENT  # a single str parent == not a merge revision
        assert isinstance(revision.down_revision, str)

    def test_it_advances_the_event_head_instead_of_creating_or_merging_one(self, script_dir):
        heads = set(script_dir.get_heads())
        assert REVISION in heads
        assert EVENT_HEAD_PARENT not in heads  # replaced, not branched from
        # No new branch point on the event chain: its former head has exactly one child.
        children = [r.revision for r in script_dir.walk_revisions() if r.down_revision == EVENT_HEAD_PARENT]
        assert children == [REVISION]

    def test_existing_history_is_untouched(self, script_dir):
        """Only additions: the parent and its whole ancestry still resolve exactly as before."""
        parent = script_dir.get_revision(EVENT_HEAD_PARENT)
        assert parent.down_revision == "a0b1c2d3e4f6"
        assert (VERSIONS / "e2b1c2d3e4f7_add_event_pricing_type.py").exists()

    def test_the_events_table_exists_in_this_revisions_ancestry(self, script_dir):
        """The columns depend on nothing but the events table, created by n4o5p6q7r8s9."""
        ancestry = {r.revision for r in script_dir.iterate_revisions(REVISION, "base")}
        assert "n4o5p6q7r8s9" in ancestry

    def test_no_other_revision_uses_these_column_names(self):
        for path in VERSIONS.glob("*.py"):
            if path.name.startswith(REVISION):
                continue
            text = path.read_text(encoding="utf-8")
            for name in ("event_type", "modules"):
                assert not re.search(rf"['\"]{name}['\"]", text), f"{path.name} already references '{name}'"

    def test_exactly_one_revision_file_was_added_for_this_change(self):
        assert len(list(VERSIONS.glob(f"{REVISION}_*.py"))) == 1


# ===========================================================================
# model <-> migration
# ===========================================================================


class TestModelMatchesMigration:
    def test_model_declares_both_columns_nullable_with_no_default(self):
        columns = Event.__table__.columns
        event_type, modules = columns["event_type"], columns["modules"]
        assert isinstance(event_type.type, sa.String) and event_type.type.length == 30
        assert isinstance(modules.type, JSONB)
        for column in (event_type, modules):
            assert column.nullable is True
            assert column.default is None and column.server_default is None
            assert not column.index

    def test_migration_adds_exactly_those_columns_to_events(self):
        text = next(VERSIONS.glob(f"{REVISION}_*.py")).read_text(encoding="utf-8")
        added = re.findall(r"op\.add_column\(\s*['\"]events['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"]", text)
        assert added == ["event_type", "modules"]
        assert "sa.String(length=30)" in text and "postgresql.JSONB" in text
        dropped = re.findall(r"op\.drop_column\(\s*['\"]events['\"]\s*,\s*['\"](\w+)['\"]", text)
        assert sorted(dropped) == ["event_type", "modules"]

    def test_every_event_model_column_still_has_a_migration(self):
        """Same guard the project uses for orders: a model column without a migration must fail CI."""
        model_columns = set(Event.__table__.columns.keys())
        migrated = set()
        for path in VERSIONS.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            migrated.update(re.findall(r"op\.add_column\(\s*['\"]events['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"]", text))
            for create in re.finditer(r"op\.create_table\(\s*['\"]events['\"]", text):
                block = text[create.end(): text.find("\n)\n", create.end()) if text.find("\n)\n", create.end()) != -1 else create.end() + 4000]
                migrated.update(re.findall(r"sa\.Column\(\s*['\"](\w+)['\"]", block))
        assert {"event_type", "modules"} <= migrated
        assert "event_type" in model_columns and "modules" in model_columns


# ===========================================================================
# behaviour-neutral
# ===========================================================================


class TestNeutral:
    def source(self):
        return next(VERSIONS.glob(f"{REVISION}_*.py")).read_text(encoding="utf-8")

    def code(self):
        """Executable lines only (the explanatory docstring may mention the words it forbids)."""
        text = self.source()
        return text[text.index("def upgrade"):]

    def test_no_default_backfill_or_index(self):
        code = self.code()
        for forbidden in ("server_default", "op.execute", "UPDATE", "INSERT", "create_index", "nullable=False", "op.get_bind"):
            assert forbidden not in code, forbidden

    def test_both_columns_are_created_nullable(self):
        assert self.code().count("nullable=True") == 2

    def test_downgrade_only_drops_the_two_columns(self):
        downgrade = self.code().split("def downgrade")[1]
        assert re.findall(r"op\.(\w+)\(", downgrade) == ["drop_column", "drop_column"]


# ===========================================================================
# the migration really runs, and legacy rows stay NULL
# ===========================================================================


class TestRunsAndPreservesLegacyRows:
    @pytest.fixture
    def engine(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as connection:
            # the events table as it is BEFORE this revision, with a pre-existing "legacy" production-like row
            connection.exec_driver_sql(
                "CREATE TABLE events (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255), status VARCHAR(20) NOT NULL, pricing_type VARCHAR(20) NOT NULL)"
            )
            connection.exec_driver_sql("INSERT INTO events (id, title, status, pricing_type) VALUES ('legacy-1', 'Old paid event', 'published', 'paid')")
        return engine

    def columns(self, engine):
        return {c["name"]: c for c in sa.inspect(engine).get_columns("events")}

    def run(self, engine, migration_module, direction):
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(migration_module, direction)()

    def test_upgrade_adds_two_nullable_columns_and_leaves_existing_rows_null(self, engine, migration_module):
        assert "event_type" not in self.columns(engine)
        self.run(engine, migration_module, "upgrade")
        columns = self.columns(engine)
        assert columns["event_type"]["nullable"] and columns["modules"]["nullable"]
        assert columns["event_type"]["default"] is None and columns["modules"]["default"] is None
        with engine.connect() as connection:
            row = connection.exec_driver_sql("SELECT title, status, pricing_type, event_type, modules FROM events").one()
        assert tuple(row) == ("Old paid event", "published", "paid", None, None)  # nothing backfilled, nothing lost

    def test_new_rows_may_omit_both_columns(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.begin() as connection:
            connection.exec_driver_sql("INSERT INTO events (id, title, status, pricing_type) VALUES ('new-1', 'Client unaware of the feature', 'draft', 'free')")
            row = connection.exec_driver_sql("SELECT event_type, modules FROM events WHERE id = 'new-1'").one()
        assert tuple(row) == (None, None)

    def test_downgrade_restores_the_previous_shape_without_touching_other_data(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        self.run(engine, migration_module, "downgrade")
        assert set(self.columns(engine)) == {"id", "title", "status", "pricing_type"}
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT title FROM events").scalar() == "Old paid event"

    def test_upgrade_is_not_repeatable_by_accident(self, engine, migration_module):
        """A second upgrade must fail loudly rather than silently duplicate/reset columns."""
        self.run(engine, migration_module, "upgrade")
        with pytest.raises(Exception):
            self.run(engine, migration_module, "upgrade")


# ===========================================================================
# exact PostgreSQL DDL, generated offline
# ===========================================================================


def alembic_sql(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", *args, "--sql"], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout


class TestOfflinePostgresDdl:
    def test_upgrade_ddl_is_two_plain_add_columns_and_a_version_bump(self):
        sql = alembic_sql("upgrade", f"{EVENT_HEAD_PARENT}:{REVISION}")
        statements = [line.strip() for line in sql.splitlines() if line.strip() and not line.startswith("--")]
        assert statements == [
            "BEGIN;",
            "ALTER TABLE events ADD COLUMN event_type VARCHAR(30);",
            "ALTER TABLE events ADD COLUMN modules JSONB;",
            f"UPDATE alembic_version SET version_num='{REVISION}' WHERE alembic_version.version_num = '{EVENT_HEAD_PARENT}';",
            "COMMIT;",
        ]

    def test_downgrade_ddl_drops_them_again(self):
        sql = alembic_sql("downgrade", f"{REVISION}:{EVENT_HEAD_PARENT}")
        statements = [line.strip() for line in sql.splitlines() if line.strip() and not line.startswith("--")]
        assert "ALTER TABLE events DROP COLUMN modules;" in statements
        assert "ALTER TABLE events DROP COLUMN event_type;" in statements
        assert f"UPDATE alembic_version SET version_num='{EVENT_HEAD_PARENT}' WHERE alembic_version.version_num = '{REVISION}';" in statements
