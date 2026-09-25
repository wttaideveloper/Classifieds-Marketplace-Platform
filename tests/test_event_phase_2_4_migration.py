"""
Event Management Phase 2.4 — migration tests for the ``event_session_attendance`` table.

Covers the strategy decisions rather than just "it runs":

- the revision is chained onto the Phase 2.2 event head (``adae1a2909cc``): a linear advance of that head, no
  merge, no new head, the other three heads and all history untouched;
- both tables it references are in the ancestry of the revision, and no other revision uses the table name;
- the ORM model and the migration declare the same columns, constraints and indexes;
- it is purely additive: one new table, no data written, nothing backfilled;
- upgrade and downgrade actually execute (SQLite, foreign keys ON): an existing registration survives, the
  unique constraint refuses a duplicate attendance, the indexes are exactly the intended ones, and the foreign
  keys cascade;
- the exact PostgreSQL DDL, generated offline (no database needed).

Run:
    pytest tests/test_event_phase_2_4_migration.py -v
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
from app.models.event_aux_models import EventSessionAttendance

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"

REVISION = "1e40e81e2937"
PARENT = "adae1a2909cc"  # Phase 2.2: the head that carries the event-domain schema work
OTHER_HEADS = {"a2b3c4d5e6f7", "d5e6f7a8b9c0", "f7a8b9c0d1e2"}
TABLE = "event_session_attendance"

# UUID-typed columns are created with the PostgreSQL UUID type; SQLite gives such a column NUMERIC affinity and
# would turn an all-digit id into a float, so the raw inserts below use ids that contain letters.
EVENT_ID = "e" * 32
EVENT_2 = "d" * 32
REG_1 = "a" * 32
REG_2 = "b" * 32


@pytest.fixture(scope="module")
def script_dir():
    return ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))


@pytest.fixture(scope="module")
def migration_module():
    path = next(VERSIONS.glob(f"{REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location("event_session_attendance_migration", path)
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
# 45. graph position
# ===========================================================================


class TestGraph:
    def test_revision_is_chained_onto_the_phase_2_2_head(self, script_dir):
        revision = script_dir.get_revision(REVISION)
        assert revision.down_revision == PARENT
        assert isinstance(revision.down_revision, str)  # a single parent == not a merge revision

    def test_it_advances_that_head_and_creates_no_new_one(self, script_dir):
        heads = set(script_dir.get_heads())
        assert PARENT not in heads
        assert [r.revision for r in script_dir.walk_revisions() if r.down_revision == PARENT] == [REVISION]
        # Later phases may extend the chain (Phase 2.5 chains onto this revision) but it must stay linear and end in
        # a head; the other three heads are exactly as they were and there are still four heads.
        node = REVISION
        while node not in heads:
            descendants = [r.revision for r in script_dir.walk_revisions() if r.down_revision == node]
            assert len(descendants) == 1, f"{node} became a branch point: {descendants}"
            node = descendants[0]
        assert heads == {node} | OTHER_HEADS

    def test_nothing_was_merged_and_history_is_untouched(self, script_dir):
        merges = [r.revision for r in script_dir.walk_revisions() if isinstance(r.down_revision, tuple) and REVISION in r.down_revision]
        assert merges == []
        parent = script_dir.get_revision(PARENT)
        assert parent.down_revision == "e2b1c2d3e4f7"  # Phase 2.2's revision still points where it did
        assert any(VERSIONS.glob(f"{PARENT}_*.py"))

    def test_both_referenced_tables_exist_in_its_ancestry(self, script_dir):
        ancestry = {r.revision for r in script_dir.iterate_revisions(REVISION, "base")}
        assert "n4o5p6q7r8s9" in ancestry  # events
        assert "o5p6q7r8s9t0" in ancestry  # event_registrations

    def test_no_other_revision_uses_the_table_name(self):
        for path in VERSIONS.glob("*.py"):
            if path.name.startswith(REVISION):
                continue
            assert TABLE not in path.read_text(encoding="utf-8"), path.name

    def test_exactly_one_revision_file_was_added_for_this_change(self):
        assert len(list(VERSIONS.glob(f"{REVISION}_*.py"))) == 1


# ===========================================================================
# model <-> migration
# ===========================================================================


class TestModelMatchesMigration:
    @pytest.fixture
    def migrated(self, migration_module):
        engine = make_engine()
        run(engine, migration_module, "upgrade")
        return engine

    def test_same_columns_nullability_and_types(self, migrated):
        model = {c.name: c for c in EventSessionAttendance.__table__.columns}
        db = {c["name"]: c for c in sa.inspect(migrated).get_columns(TABLE)}
        assert set(model) == set(db) == {"id", "event_id", "registration_id", "session_id", "checked_in_at", "checked_in_by",
                                         "checked_out_at", "checked_out_by", "created_at", "updated_at"}
        for name, column in model.items():
            assert db[name]["nullable"] == column.nullable, name
        assert not model["session_id"].nullable and model["session_id"].type.length == 100
        assert model["checked_out_at"].nullable and model["checked_out_by"].nullable and model["checked_in_by"].nullable
        assert not model["checked_in_at"].nullable and not model["created_at"].nullable and not model["updated_at"].nullable

    def test_same_unique_constraint(self, migrated):
        model = {tuple(c.columns.keys()) for c in EventSessionAttendance.__table__.constraints if isinstance(c, sa.UniqueConstraint)}
        assert model == {("event_id", "registration_id", "session_id")}
        db = sa.inspect(migrated).get_unique_constraints(TABLE)
        assert [(u["name"], tuple(u["column_names"])) for u in db] == [("uq_event_session_attendance", ("event_id", "registration_id", "session_id"))]
        assert any(c.name == "uq_event_session_attendance" for c in EventSessionAttendance.__table__.constraints)

    def test_same_indexes(self, migrated):
        model = {i.name: tuple(c.name for c in i.columns) for i in EventSessionAttendance.__table__.indexes}
        db = {i["name"]: tuple(i["column_names"]) for i in sa.inspect(migrated).get_indexes(TABLE)}
        assert model == db == {
            "ix_event_session_attendance_event_session": ("event_id", "session_id"),
            "ix_event_session_attendance_registration": ("registration_id",),
        }

    def test_no_index_duplicates_what_the_unique_constraint_already_covers(self, migrated):
        """(event_id, registration_id) lookups use the unique constraint's index; event_id alone is a leading column."""
        indexed = [tuple(i["column_names"]) for i in sa.inspect(migrated).get_indexes(TABLE)]
        assert ("event_id", "registration_id") not in indexed and ("event_id",) not in indexed

    def test_foreign_keys_cascade_and_session_id_has_none(self, migrated):
        foreign_keys = sa.inspect(migrated).get_foreign_keys(TABLE)
        assert {(tuple(f["constrained_columns"]), f["referred_table"], f["options"].get("ondelete")) for f in foreign_keys} == {
            (("event_id",), "events", "CASCADE"),
            (("registration_id",), "event_registrations", "CASCADE"),
        }
        model_fks = {(fk.parent.name, fk.column.table.name, fk.ondelete) for fk in EventSessionAttendance.__table__.foreign_keys}
        assert model_fks == {("event_id", "events", "CASCADE"), ("registration_id", "event_registrations", "CASCADE")}
        assert not EventSessionAttendance.__table__.columns["session_id"].foreign_keys  # an id inside the events.sessions JSONB


# ===========================================================================
# purely additive
# ===========================================================================


class TestNeutral:
    def test_no_data_is_written_or_backfilled_and_no_existing_table_is_altered(self):
        for forbidden in ("op.execute", "UPDATE", "INSERT", "op.get_bind", "add_column", "alter_column", "drop_column", "batch_alter_table"):
            assert forbidden not in code(), forbidden

    def test_upgrade_only_creates_the_table_and_its_two_indexes(self):
        upgrade = code().split("def downgrade")[0]
        assert re.findall(r"op\.(\w+)\(", upgrade) == ["create_table", "create_index", "create_index"]

    def test_downgrade_drops_exactly_what_upgrade_created_in_reverse(self):
        downgrade = code().split("def downgrade")[1]
        assert re.findall(r"op\.(\w+)\(", downgrade) == ["drop_index", "drop_index", "drop_table"]
        assert downgrade.index("ix_event_session_attendance_registration") < downgrade.index("ix_event_session_attendance_event_session") < downgrade.index("drop_table")


# ===========================================================================
# 45-48. the migration really runs
# ===========================================================================


def make_engine():
    """The two parent tables as they are BEFORE this revision (with a production-like existing registration)."""
    engine = sa.create_engine("sqlite://", poolclass=sa.pool.StaticPool, connect_args={"check_same_thread": False})

    @sa.event.listens_for(engine, "connect")
    def _foreign_keys_on(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # so ON DELETE CASCADE is really enforced

    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE events (id CHAR(32) PRIMARY KEY, title VARCHAR(255))")
        connection.exec_driver_sql(
            "CREATE TABLE event_registrations (id CHAR(32) PRIMARY KEY, event_id CHAR(32) REFERENCES events(id), "
            "participant_email VARCHAR(255), status VARCHAR(20), session_id VARCHAR(100), checked_in_at DATETIME)"
        )
        connection.exec_driver_sql(f"INSERT INTO events VALUES ('{EVENT_ID}', 'Tech Conference'), ('{EVENT_2}', 'Other')")
        connection.exec_driver_sql(
            f"INSERT INTO event_registrations VALUES ('{REG_1}', '{EVENT_ID}', 'a@example.com', 'attended', 'legacy-session', '2026-03-01 09:00:00'), "
            f"('{REG_2}', '{EVENT_ID}', 'b@example.com', 'confirmed', NULL, NULL)"
        )
    return engine


def run(engine, migration_module, direction):
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration_module, direction)()


def insert(connection, registration, session, event=EVENT_ID, checked_in="2026-09-25 10:00:00"):
    connection.exec_driver_sql(
        f"INSERT INTO {TABLE} (id, event_id, registration_id, session_id, checked_in_at) "
        f"VALUES (lower(hex(randomblob(16))), '{event}', '{registration}', '{session}', '{checked_in}')"
    )


class TestRuns:
    @pytest.fixture
    def engine(self):
        return make_engine()

    def tables(self, engine):
        return set(sa.inspect(engine).get_table_names())

    def test_upgrade_creates_the_table_and_touches_nothing_else(self, engine, migration_module):
        assert TABLE not in self.tables(engine)
        run(engine, migration_module, "upgrade")
        assert self.tables(engine) == {"events", "event_registrations", TABLE}
        with engine.connect() as connection:
            legacy = connection.exec_driver_sql("SELECT status, session_id, checked_in_at FROM event_registrations WHERE id = ?", (REG_1,)).one()
            assert tuple(legacy) == ("attended", "legacy-session", "2026-03-01 09:00:00")  # event-level state and legacy session_id intact
            assert connection.exec_driver_sql(f"SELECT COUNT(*) FROM {TABLE}").scalar() == 0  # nothing backfilled
        assert {c["name"] for c in sa.inspect(engine).get_columns("event_registrations")} == {
            "id", "event_id", "participant_email", "status", "session_id", "checked_in_at"}

    def test_the_unique_constraint_refuses_a_duplicate_attendance(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        with engine.begin() as connection:
            insert(connection, REG_1, "keynote")
            insert(connection, REG_1, "workshop")  # another session: fine
            insert(connection, REG_2, "keynote")  # another attendee: fine
            insert(connection, REG_1, "keynote", event=EVENT_2)  # another event (its own registration would differ; the key is the triple)
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    f"INSERT INTO {TABLE} (id, event_id, registration_id, session_id, checked_in_at) "
                    f"VALUES ('{'f' * 32}', '{EVENT_ID}', '{REG_1}', 'keynote', '2026-09-25 11:00:00')"
                )
        with engine.connect() as connection:
            assert connection.exec_driver_sql(f"SELECT COUNT(*) FROM {TABLE}").scalar() == 4

    def test_required_columns_are_enforced_and_optional_ones_may_be_omitted(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        with engine.begin() as connection:
            insert(connection, REG_1, "keynote")
            row = connection.exec_driver_sql(f"SELECT checked_in_by, checked_out_at, checked_out_by, created_at, updated_at FROM {TABLE}").one()
            assert tuple(row[:3]) == (None, None, None) and row[3] is not None and row[4] is not None  # timestamps default
        for missing in ("event_id", "registration_id", "session_id", "checked_in_at"):
            columns = {"event_id": f"'{EVENT_ID}'", "registration_id": f"'{REG_2}'", "session_id": "'keynote'", "checked_in_at": "'2026-09-25 10:00:00'"}
            columns.pop(missing)
            with pytest.raises(sa.exc.IntegrityError):
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        f"INSERT INTO {TABLE} (id, {', '.join(columns)}) VALUES ('{'c' * 32}', {', '.join(columns.values())})")

    def test_the_foreign_keys_cascade(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        with engine.begin() as connection:
            insert(connection, REG_1, "keynote")
            insert(connection, REG_1, "workshop")
            insert(connection, REG_2, "keynote")
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM event_registrations WHERE id = ?", (REG_1,))
            assert connection.exec_driver_sql(f"SELECT registration_id FROM {TABLE}").scalars().all() == [REG_2]  # only that attendee's rows went
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM event_registrations")
            connection.exec_driver_sql(f"DELETE FROM events WHERE id = '{EVENT_ID}'")
            assert connection.exec_driver_sql(f"SELECT COUNT(*) FROM {TABLE}").scalar() == 0

    def test_an_attendance_row_cannot_reference_a_registration_that_does_not_exist(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as connection:
                insert(connection, "9" * 31 + "f", "keynote")

    def test_downgrade_removes_the_table_and_its_indexes_and_nothing_else(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        with engine.begin() as connection:
            insert(connection, REG_1, "keynote")
        run(engine, migration_module, "downgrade")
        assert self.tables(engine) == {"events", "event_registrations"}
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM event_registrations").scalar() == 2  # registrations survive
            assert connection.exec_driver_sql("SELECT status FROM event_registrations WHERE id = ?", (REG_1,)).scalar() == "attended"

    def test_upgrade_downgrade_upgrade_round_trips(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        run(engine, migration_module, "downgrade")
        run(engine, migration_module, "upgrade")
        assert TABLE in self.tables(engine)
        assert {i["name"] for i in sa.inspect(engine).get_indexes(TABLE)} == {
            "ix_event_session_attendance_event_session", "ix_event_session_attendance_registration"}

    def test_upgrade_is_not_repeatable_by_accident(self, engine, migration_module):
        run(engine, migration_module, "upgrade")
        with pytest.raises(Exception):
            run(engine, migration_module, "upgrade")


# ===========================================================================
# exact PostgreSQL DDL, generated offline
# ===========================================================================


def alembic_sql(*args):
    proc = subprocess.run([sys.executable, "-m", "alembic", *args, "--sql"], cwd=REPO, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout


def statements(sql):
    return " ".join(line.strip() for line in sql.splitlines() if line.strip() and not line.startswith("--"))


class TestOfflinePostgresDdl:
    def test_upgrade_ddl(self):
        sql = statements(alembic_sql("upgrade", f"{PARENT}:{REVISION}"))
        assert sql.startswith("BEGIN; CREATE TABLE event_session_attendance (")
        for fragment in (
            "id UUID NOT NULL", "event_id UUID NOT NULL", "registration_id UUID NOT NULL", "session_id VARCHAR(100) NOT NULL",
            "checked_in_at TIMESTAMP WITHOUT TIME ZONE NOT NULL", "checked_in_by UUID,", "checked_out_at TIMESTAMP WITHOUT TIME ZONE,",
            "checked_out_by UUID,", "created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL",
            "updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now() NOT NULL", "PRIMARY KEY (id)",
            "FOREIGN KEY(event_id) REFERENCES events (id) ON DELETE CASCADE",
            "FOREIGN KEY(registration_id) REFERENCES event_registrations (id) ON DELETE CASCADE",
            "CONSTRAINT uq_event_session_attendance UNIQUE (event_id, registration_id, session_id)",
            "CREATE INDEX ix_event_session_attendance_event_session ON event_session_attendance (event_id, session_id);",
            "CREATE INDEX ix_event_session_attendance_registration ON event_session_attendance (registration_id);",
        ):
            assert fragment in sql, fragment
        assert sql.endswith(f"UPDATE alembic_version SET version_num='{REVISION}' WHERE alembic_version.version_num = '{PARENT}'; COMMIT;")
        assert sql.count("CREATE TABLE") == 1 and "ALTER TABLE" not in sql and "INSERT" not in sql

    def test_downgrade_ddl(self):
        sql = statements(alembic_sql("downgrade", f"{REVISION}:{PARENT}"))
        assert sql == (
            "BEGIN; DROP INDEX ix_event_session_attendance_registration; DROP INDEX ix_event_session_attendance_event_session; "
            "DROP TABLE event_session_attendance; "
            f"UPDATE alembic_version SET version_num='{PARENT}' WHERE alembic_version.version_num = '{REVISION}'; COMMIT;"
        )
