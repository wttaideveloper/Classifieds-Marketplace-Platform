"""
Event Management Phase 2.2 (evolved) — migration tests for the ``event_types`` table and its seed data.

Covers the strategy decisions rather than just "it runs":

- the revision is chained onto the Phase 2.7 head (``119f4bd408a4``): a linear advance of that head, no
  merge, no new head, the other three heads and all history untouched;
- ``events.event_type`` is untouched — no column change, no foreign key added to the new table;
- the model and the migration declare the same columns;
- it seeds the 7 Event Types that were previously hardcoded in ``app.utils.event_modules``, with
  ``default_modules`` byte-identical to the removed table, ``allowed_modules`` fully permissive and
  ``required_modules`` fully empty (behaviourally equivalent to "nothing was ever restricted by type");
- upgrade and downgrade really execute against a table holding production-like rows, and existing
  ``events`` rows (with any ``event_type`` string, matched or not) are completely untouched;
- the exact PostgreSQL DDL, generated offline (no database needed).

Run:
    pytest tests/test_event_type_migration.py -v
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

import event_sql_support as _sqlite_jsonb_shim  # noqa: F401
# (event_sql_support also registers the JSONB -> JSON shim so the migration runs on SQLite)
from event_sql_support import other_heads
from app.models.event_type_model import EventTypeConfig

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"

REVISION = "070237a7c1cf"
PARENT = "119f4bd408a4"  # Phase 2.7: the head that carries the event-domain schema work
OTHER_HEADS = {"a2b3c4d5e6f7", "d5e6f7a8b9c0", "f7a8b9c0d1e2"}

SPEC_TYPES = ["conference", "workshop", "marathon", "camp", "private_function", "webinar", "other"]
SPEC_KEYS = ["registration", "tickets", "sessions", "check_in", "online_meeting", "custom_questions", "meals", "accommodation"]


def flags(*enabled):
    return {key: key in enabled for key in SPEC_KEYS}


# The removed app.utils.event_modules.EVENT_TYPE_DEFAULT_MODULES table, typed out independently here —
# same convention as SPEC_DEFAULTS elsewhere: a real drift is a caught test failure, not a passing test
# that merely imports whatever the migration currently seeds.
SPEC_DEFAULTS = {
    "conference": flags("registration", "tickets", "sessions", "check_in", "meals", "accommodation"),
    "workshop": flags("registration", "tickets", "sessions", "check_in"),
    "marathon": flags("registration", "tickets", "check_in"),
    "camp": flags("registration", "check_in", "meals", "accommodation"),
    "private_function": flags("registration", "check_in", "custom_questions", "meals"),
    "webinar": flags("registration", "sessions", "online_meeting"),
    "other": flags("registration"),
}
ALL_ALLOWED = flags(*SPEC_KEYS)
NONE_REQUIRED = flags()


@pytest.fixture(scope="module")
def script_dir():
    return ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))


@pytest.fixture(scope="module")
def migration_module():
    path = next(VERSIONS.glob(f"{REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location("event_types_migration", path)
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
    def test_revision_is_chained_onto_the_phase_2_7_head(self, script_dir):
        revision = script_dir.get_revision(REVISION)
        assert revision.down_revision == PARENT
        assert isinstance(revision.down_revision, str)  # a single parent == not a merge revision

    def test_it_advances_that_head_and_creates_no_new_one(self, script_dir):
        heads = set(script_dir.get_heads())
        assert PARENT not in heads
        assert [r.revision for r in script_dir.walk_revisions() if r.down_revision == PARENT] == [REVISION]
        assert REVISION in heads  # this revision IS the current event-domain head
        assert heads == {REVISION} | other_heads(script_dir)

    def test_nothing_was_merged_and_history_is_untouched(self, script_dir):
        assert [r.revision for r in script_dir.walk_revisions() if isinstance(r.down_revision, tuple) and REVISION in r.down_revision] == []
        assert script_dir.get_revision(PARENT).down_revision == "d3a5cd7d0a58"  # Phase 2.7 still points where it did

    def test_the_events_table_is_not_touched(self):
        text = code()
        assert "alter_column" not in text
        tables_touched = set(re.findall(r"op\.(?:add_column|create_table|drop_column)\(\s*['\"](\w+)['\"]", text))
        assert "events" not in tables_touched

    def test_no_other_revision_uses_the_event_types_table_name(self):
        for path in VERSIONS.glob("*.py"):
            if path.name.startswith(REVISION):
                continue
            text = path.read_text(encoding="utf-8")
            assert "event_types" not in text, path.name

    def test_exactly_one_revision_file_was_added_for_this_change(self):
        assert len(list(VERSIONS.glob(f"{REVISION}_*.py"))) == 1


# ===========================================================================
# model <-> migration
# ===========================================================================


class TestModelMatchesMigration:
    def test_the_model_declares_the_same_columns_as_the_migration(self):
        columns = {c.name for c in EventTypeConfig.__table__.columns}
        assert columns == {"id", "key", "name", "active", "default_modules", "allowed_modules", "required_modules", "created_at", "updated_at"}

    def test_key_is_unique_and_indexed_not_a_foreign_key_anywhere(self):
        key_column = EventTypeConfig.__table__.columns["key"]
        assert key_column.unique and key_column.index and not key_column.foreign_keys
        # events.event_type has no FK to this table (see app/models/event_model.py) — grep it directly.
        model_source = (REPO / "app" / "models" / "event_model.py").read_text(encoding="utf-8")
        assert "ForeignKey(\"event_types" not in model_source and "ForeignKey('event_types" not in model_source

    def test_the_module_map_columns_are_jsonb_not_null(self):
        for name in ("default_modules", "allowed_modules", "required_modules"):
            column = EventTypeConfig.__table__.columns[name]
            assert isinstance(column.type, JSONB) and column.nullable is False


# ===========================================================================
# seed data
# ===========================================================================


class TestSeedData:
    def test_exactly_the_seven_legacy_types_are_seeded(self, migration_module):
        assert set(migration_module.SEED_ORDER) == set(SPEC_TYPES)

    @pytest.mark.parametrize("key", SPEC_TYPES)
    def test_seeded_default_modules_match_the_removed_hardcoded_table_exactly(self, migration_module, key):
        assert migration_module.SEED_DEFAULT_MODULES[key] == SPEC_DEFAULTS[key]

    @pytest.mark.parametrize("key", SPEC_TYPES)
    def test_seeded_types_are_fully_permissive_and_nothing_required(self, migration_module, key):
        """Behaviourally equivalent to before this migration: nothing was ever restricted or required by
        type, only the generic (Event-Type-independent) online_meeting/tickets rules applied."""
        assert migration_module._ALL_ALLOWED == ALL_ALLOWED
        assert migration_module._NONE_REQUIRED == NONE_REQUIRED


# ===========================================================================
# behaviour-neutral
# ===========================================================================


class TestNeutral:
    def test_no_backfill_of_the_events_table(self):
        upgrade_code = code().split("def downgrade")[0]
        assert "UPDATE events" not in upgrade_code and "events.event_type" not in upgrade_code

    def test_upgrade_creates_the_table_and_seeds_rows_downgrade_drops_it(self):
        upgrade, downgrade = code().split("def downgrade")
        assert "op.create_table" in upgrade and "op.bulk_insert" in upgrade
        assert re.findall(r"op\.(\w+)\(", downgrade) == ["drop_index", "drop_table"]


# ===========================================================================
# the migration really runs
# ===========================================================================


class TestRunsAndPreservesExistingRows:
    @pytest.fixture
    def engine(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as connection:
            # events as it is BEFORE this revision, holding production-like rows — any event_type string,
            # matched by this migration's seed or not, must survive completely untouched.
            connection.exec_driver_sql(
                "CREATE TABLE events (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255), status VARCHAR(20) NOT NULL, "
                "event_type VARCHAR(30), modules JSON)")
            connection.exec_driver_sql(
                "INSERT INTO events VALUES "
                "('legacy-1', 'Old event', 'published', NULL, NULL), "
                "('conf-1', 'Configured event', 'published', 'conference', '{\"registration\": true}'), "
                "('custom-1', 'Not seeded here', 'published', 'not_a_seeded_type', NULL)"
            )
        return engine

    def columns(self, engine, table):
        return {c["name"]: c for c in sa.inspect(engine).get_columns(table)}

    def run(self, engine, migration_module, direction):
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(migration_module, direction)()

    def test_upgrade_creates_the_table(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        assert "event_types" in sa.inspect(engine).get_table_names()
        columns = self.columns(engine, "event_types")
        for name in ("id", "key", "name", "active", "default_modules", "allowed_modules", "required_modules", "created_at", "updated_at"):
            assert name in columns

    def test_the_seven_types_are_seeded_with_the_right_shape(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.connect() as connection:
            rows = connection.exec_driver_sql("SELECT key, name, active FROM event_types ORDER BY key").all()
        assert {r[0] for r in rows} == set(SPEC_TYPES)
        assert all(r[2] in (1, True) for r in rows)  # every seeded type starts active

    def test_key_is_unique(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        inspector = sa.inspect(engine)
        indexes = inspector.get_indexes("event_types")
        key_index = next(i for i in indexes if i["column_names"] == ["key"])
        assert key_index["unique"]

    def test_existing_events_rows_are_completely_untouched(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.connect() as connection:
            events = connection.exec_driver_sql("SELECT id, title, status, event_type, modules FROM events ORDER BY id").all()
        assert [tuple(r) for r in events] == [
            ("conf-1", "Configured event", "published", "conference", '{"registration": true}'),
            ("custom-1", "Not seeded here", "published", "not_a_seeded_type", None),
            ("legacy-1", "Old event", "published", None, None),
        ]  # byte-identical to before the migration, whether or not event_type matches a seeded key

    def test_downgrade_drops_the_table_without_touching_events(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        self.run(engine, migration_module, "downgrade")
        assert "event_types" not in sa.inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar() == 3

    def test_upgrade_downgrade_upgrade_round_trips(self, engine, migration_module):
        for direction in ("upgrade", "downgrade", "upgrade"):
            self.run(engine, migration_module, direction)
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM event_types").scalar() == 7

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


class TestOfflinePostgresDdl:
    def test_upgrade_ddl_creates_the_table_and_a_unique_index(self):
        sql = alembic_sql("upgrade", f"{PARENT}:{REVISION}")
        assert "CREATE TABLE event_types" in sql
        assert "CREATE UNIQUE INDEX ix_event_types_key ON event_types (key)" in sql
        assert f"UPDATE alembic_version SET version_num='{REVISION}'" in sql

    def test_upgrade_ddl_seeds_exactly_seven_rows(self):
        sql = alembic_sql("upgrade", f"{PARENT}:{REVISION}")
        assert sql.count("INSERT INTO event_types") == 7

    def test_downgrade_ddl_drops_the_index_then_the_table(self):
        sql = alembic_sql("downgrade", f"{REVISION}:{PARENT}")
        assert "DROP INDEX ix_event_types_key" in sql or "DROP INDEX IF EXISTS ix_event_types_key" in sql
        assert "DROP TABLE event_types" in sql
        assert sql.index("DROP INDEX") < sql.index("DROP TABLE")
