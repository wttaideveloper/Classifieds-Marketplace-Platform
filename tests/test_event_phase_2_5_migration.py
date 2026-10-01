"""
Event Management Phase 2.5 — migration tests for ``event_registrations.registration_source``.

Covers the strategy decisions rather than just "it runs":

- the revision is chained onto the Phase 2.4 head (``1e40e81e2937``): a linear advance of that head, no merge,
  no new head, the other three heads and all history untouched;
- no other revision uses the column name, and it adds nothing but that one column;
- the model and the migration declare the same column;
- it is behaviour-neutral: nullable, no server default, no backfill, no index, no CHECK — every existing row
  stays NULL (which the API reads as "online") and nothing about it changes;
- upgrade and downgrade really execute against a table holding production-like rows;
- the exact PostgreSQL DDL, generated offline (no database needed).

Run:
    pytest tests/test_event_phase_2_5_migration.py -v
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

import event_sql_support as _sqlite_shims  # noqa: F401  (registers the JSONB -> JSON shim and every model)
from app.models.event_aux_models import EventRegistration

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"

REVISION = "fe61c574e6f5"
PARENT = "1e40e81e2937"  # Phase 2.4: the head that carries the event-domain schema work
OTHER_HEADS = {"a2b3c4d5e6f7", "d5e6f7a8b9c0", "f7a8b9c0d1e2"}


@pytest.fixture(scope="module")
def script_dir():
    return ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))


@pytest.fixture(scope="module")
def migration_module():
    path = next(VERSIONS.glob(f"{REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location("event_registration_source_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source():
    return next(VERSIONS.glob(f"{REVISION}_*.py")).read_text(encoding="utf-8")


def code():
    """Executable lines only (the explanatory docstring may mention words the code must not use)."""
    text = source()
    return text[text.index("def upgrade"):]


# ===========================================================================
# graph position
# ===========================================================================


class TestGraph:
    def test_revision_is_chained_onto_the_phase_2_4_head(self, script_dir):
        revision = script_dir.get_revision(REVISION)
        assert revision.down_revision == PARENT
        assert isinstance(revision.down_revision, str)  # a single parent == not a merge revision

    def test_it_advances_that_head_and_creates_no_new_one(self, script_dir):
        heads = set(script_dir.get_heads())
        assert PARENT not in heads
        assert [r.revision for r in script_dir.walk_revisions() if r.down_revision == PARENT] == [REVISION]
        # Later phases may extend the chain (Phase 2.6 chains onto this revision) but it must stay linear and end in a
        # head; the other three heads are exactly as they were and there are still four heads.
        node = REVISION
        while node not in heads:
            descendants = [r.revision for r in script_dir.walk_revisions() if r.down_revision == node]
            assert len(descendants) == 1, f"{node} became a branch point: {descendants}"
            node = descendants[0]
        assert heads == {node} | OTHER_HEADS

    def test_nothing_was_merged_and_history_is_untouched(self, script_dir):
        assert [r.revision for r in script_dir.walk_revisions() if isinstance(r.down_revision, tuple) and REVISION in r.down_revision] == []
        assert script_dir.get_revision(PARENT).down_revision == "adae1a2909cc"  # Phase 2.4 still points where it did
        assert script_dir.get_revision("adae1a2909cc").down_revision == "e2b1c2d3e4f7"  # ... and so does Phase 2.2

    def test_the_table_it_alters_exists_in_its_ancestry(self, script_dir):
        ancestry = {r.revision for r in script_dir.iterate_revisions(REVISION, "base")}
        assert "o5p6q7r8s9t0" in ancestry  # event_registrations

    def test_no_other_revision_uses_the_column_name(self):
        for path in VERSIONS.glob("*.py"):
            if path.name.startswith(REVISION):
                continue
            assert "registration_source" not in path.read_text(encoding="utf-8"), path.name

    def test_exactly_one_revision_file_was_added_for_this_change(self):
        assert len(list(VERSIONS.glob(f"{REVISION}_*.py"))) == 1


# ===========================================================================
# model <-> migration
# ===========================================================================


class TestModelMatchesMigration:
    def test_the_model_declares_a_nullable_string_20_without_default_or_index(self):
        column = EventRegistration.__table__.columns["registration_source"]
        assert isinstance(column.type, sa.String) and column.type.length == 20
        assert column.nullable is True and column.default is None and column.server_default is None
        assert not column.index and not column.unique and not column.foreign_keys

    def test_the_migration_adds_exactly_that_column_to_event_registrations(self):
        text = source()
        added = re.findall(r"op\.add_column\(\s*['\"](\w+)['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"]", text)
        assert added == [("event_registrations", "registration_source")]
        assert "sa.String(length=20)" in text and "nullable=True" in text
        assert re.findall(r"op\.drop_column\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]", text) == [("event_registrations", "registration_source")]

    def test_no_registered_by_column_was_added(self):
        assert "registered_by" not in {c.name for c in EventRegistration.__table__.columns}  # the operator is on the audit row


# ===========================================================================
# behaviour-neutral
# ===========================================================================


class TestNeutral:
    def test_no_default_backfill_index_or_constraint(self):
        for forbidden in ("server_default", "op.execute", "UPDATE", "INSERT", "create_index", "create_check_constraint",
                          "nullable=False", "op.get_bind", "alter_column", "create_table"):
            assert forbidden not in code(), forbidden

    def test_upgrade_is_one_add_column_and_downgrade_one_drop(self):
        upgrade, downgrade = code().split("def downgrade")
        assert re.findall(r"op\.(\w+)\(", upgrade) == ["add_column"]
        assert re.findall(r"op\.(\w+)\(", downgrade) == ["drop_column"]


# ===========================================================================
# the migration really runs, and existing rows stay NULL
# ===========================================================================


class TestRunsAndPreservesExistingRows:
    @pytest.fixture
    def engine(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as connection:
            # event_registrations as it is BEFORE this revision, with production-like rows
            connection.exec_driver_sql(
                "CREATE TABLE event_registrations (id VARCHAR(36) PRIMARY KEY, event_id VARCHAR(36), participant_email VARCHAR(255), "
                "status VARCHAR(20), qr_code VARCHAR(255), session_id VARCHAR(100), checked_in_at DATETIME)"
            )
            connection.exec_driver_sql(
                "INSERT INTO event_registrations VALUES "
                "('r1', 'e1', 'online@example.com', 'confirmed', 'QR1', NULL, NULL), "
                "('r2', 'e1', 'attended@example.com', 'attended', 'QR2', 'legacy-session', '2026-03-01 09:00:00'), "
                "('r3', 'e1', 'gone@example.com', 'cancelled', 'QR3', NULL, NULL)"
            )
        return engine

    def columns(self, engine):
        return {c["name"]: c for c in sa.inspect(engine).get_columns("event_registrations")}

    def run(self, engine, migration_module, direction):
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(migration_module, direction)()

    def test_upgrade_adds_one_nullable_column_and_leaves_every_existing_row_null(self, engine, migration_module):
        before = set(self.columns(engine))
        self.run(engine, migration_module, "upgrade")
        columns = self.columns(engine)
        assert set(columns) - before == {"registration_source"}
        assert columns["registration_source"]["nullable"] and columns["registration_source"]["default"] is None
        with engine.connect() as connection:
            rows = connection.exec_driver_sql("SELECT id, registration_source FROM event_registrations ORDER BY id").all()
            assert [tuple(r) for r in rows] == [("r1", None), ("r2", None), ("r3", None)]  # nothing backfilled
            legacy = connection.exec_driver_sql("SELECT status, session_id, checked_in_at FROM event_registrations WHERE id = 'r2'").one()
            assert tuple(legacy) == ("attended", "legacy-session", "2026-03-01 09:00:00")  # nothing else touched
        assert sa.inspect(engine).get_indexes("event_registrations") == []  # no index was created

    def test_rows_written_by_code_that_predates_the_column_still_insert(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.begin() as connection:  # what online registration / checkout do: they never mention the column
            connection.exec_driver_sql(
                "INSERT INTO event_registrations (id, event_id, participant_email, status, qr_code) VALUES ('r4', 'e1', 'new@example.com', 'confirmed', 'QR4')")
            assert connection.exec_driver_sql("SELECT registration_source FROM event_registrations WHERE id = 'r4'").scalar() is None

    def test_a_walk_in_value_round_trips(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE event_registrations SET registration_source = 'walk_in' WHERE id = 'r1'")
            assert connection.exec_driver_sql("SELECT registration_source FROM event_registrations WHERE id = 'r1'").scalar() == "walk_in"

    def test_downgrade_restores_the_previous_shape_without_touching_other_data(self, engine, migration_module):
        original = set(self.columns(engine))
        self.run(engine, migration_module, "upgrade")
        self.run(engine, migration_module, "downgrade")
        assert set(self.columns(engine)) == original
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM event_registrations").scalar() == 3
            assert connection.exec_driver_sql("SELECT status FROM event_registrations WHERE id = 'r2'").scalar() == "attended"

    def test_upgrade_downgrade_upgrade_round_trips(self, engine, migration_module):
        for direction in ("upgrade", "downgrade", "upgrade"):
            self.run(engine, migration_module, direction)
        assert "registration_source" in self.columns(engine)

    def test_upgrade_is_not_repeatable_by_accident(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with pytest.raises(Exception):
            self.run(engine, migration_module, "upgrade")


# ===========================================================================
# exact PostgreSQL DDL, generated offline
# ===========================================================================


def alembic_sql(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", *args, "--sql"], cwd=REPO, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout


def statements(sql):
    return [line.strip() for line in sql.splitlines() if line.strip() and not line.startswith("--")]


class TestOfflinePostgresDdl:
    def test_upgrade_ddl_is_one_plain_add_column_and_a_version_bump(self):
        assert statements(alembic_sql("upgrade", f"{PARENT}:{REVISION}")) == [
            "BEGIN;",
            "ALTER TABLE event_registrations ADD COLUMN registration_source VARCHAR(20);",
            f"UPDATE alembic_version SET version_num='{REVISION}' WHERE alembic_version.version_num = '{PARENT}';",
            "COMMIT;",
        ]

    def test_downgrade_ddl_drops_it_again(self):
        assert statements(alembic_sql("downgrade", f"{REVISION}:{PARENT}")) == [
            "BEGIN;",
            "ALTER TABLE event_registrations DROP COLUMN registration_source;",
            f"UPDATE alembic_version SET version_num='{PARENT}' WHERE alembic_version.version_num = '{REVISION}';",
            "COMMIT;",
        ]
