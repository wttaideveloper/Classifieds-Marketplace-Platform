"""
Event Management Phase 2.7 — migration tests for ``events.accommodation`` and
``event_registrations.accommodation_selections``.

Covers the strategy decisions rather than just "it runs":

- the revision is chained onto the Phase 2.6 head (``d3a5cd7d0a58``): a linear advance of that head, no merge, no new
  head, the other three heads and all history untouched;
- no other revision uses either column name, and it adds nothing but those two columns;
- the models and the migration declare the same columns;
- it is behaviour-neutral: both nullable JSONB, no server default, no backfill, no index, no table — every existing
  event keeps accommodation NULL (no options, accommodation off) and every existing registration keeps
  accommodation_selections NULL;
- upgrade and downgrade really execute against tables holding production-like rows, and the rows survive untouched;
- the exact PostgreSQL DDL, generated offline (no database needed).

Run:
    pytest tests/test_event_phase_2_7_migration.py -v
"""
import importlib.util
import json
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
from app.models.event_aux_models import EventRegistration
from app.models.event_model import Event

REPO = Path(__file__).resolve().parents[1]
VERSIONS = REPO / "alembic" / "versions"

REVISION = "119f4bd408a4"
PARENT = "d3a5cd7d0a58"  # Phase 2.6: the head that carries the event-domain schema work
OTHER_HEADS = {"a2b3c4d5e6f7", "d5e6f7a8b9c0", "f7a8b9c0d1e2"}


@pytest.fixture(scope="module")
def script_dir():
    return ScriptDirectory.from_config(Config(str(REPO / "alembic.ini")))


@pytest.fixture(scope="module")
def migration_module():
    path = next(VERSIONS.glob(f"{REVISION}_*.py"))
    spec = importlib.util.spec_from_file_location("event_accommodation_migration", path)
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
    def test_revision_is_chained_onto_the_phase_2_6_head(self, script_dir):
        revision = script_dir.get_revision(REVISION)
        assert revision.down_revision == PARENT
        assert isinstance(revision.down_revision, str)  # a single parent == not a merge revision

    def test_it_advances_that_head_and_creates_no_new_one(self, script_dir):
        heads = set(script_dir.get_heads())
        assert PARENT not in heads
        assert [r.revision for r in script_dir.walk_revisions() if r.down_revision == PARENT] == [REVISION]
        # Later phases may extend the chain (e.g. the Phase 2.2 Event Type evolution, 070237a7c1cf, chains
        # onto this very revision) but it must stay linear and end in a head; the other three heads are
        # exactly as they were and there are still four heads.
        node = REVISION
        while node not in heads:
            descendants = [r.revision for r in script_dir.walk_revisions() if r.down_revision == node]
            assert len(descendants) == 1, f"{node} became a branch point: {descendants}"
            node = descendants[0]
        assert heads == {node} | OTHER_HEADS

    def test_nothing_was_merged_and_history_is_untouched(self, script_dir):
        assert [r.revision for r in script_dir.walk_revisions() if isinstance(r.down_revision, tuple) and REVISION in r.down_revision] == []
        assert script_dir.get_revision(PARENT).down_revision == "fe61c574e6f5"  # Phase 2.6 still points where it did
        assert script_dir.get_revision("fe61c574e6f5").down_revision == "1e40e81e2937"  # ... Phase 2.5
        assert script_dir.get_revision("1e40e81e2937").down_revision == "adae1a2909cc"  # ... Phase 2.4

    def test_the_tables_it_alters_exist_in_its_ancestry(self, script_dir):
        ancestry = {r.revision for r in script_dir.iterate_revisions(REVISION, "base")}
        assert "n4o5p6q7r8s9" in ancestry  # events
        assert "o5p6q7r8s9t0" in ancestry  # event_registrations

    def test_no_other_revision_uses_either_column_name(self):
        for path in VERSIONS.glob("*.py"):
            if path.name.startswith(REVISION):
                continue
            text = path.read_text(encoding="utf-8")
            assert "accommodation_selections" not in text, path.name
            # A column definition specifically (sa.Column('accommodation', ...)) — not just any mention of
            # the word, which the Phase 2.2 Event Type seed data (070237a7c1cf) legitimately enumerates as
            # one of the eight MODULE KEY NAMES (not a column) shared with every other event_type consumer.
            assert not re.search(r"sa\.Column\(\s*['\"]accommodation['\"]", text), path.name

    def test_exactly_one_revision_file_was_added_for_this_change(self):
        assert len(list(VERSIONS.glob(f"{REVISION}_*.py"))) == 1


# ===========================================================================
# model <-> migration
# ===========================================================================


class TestModelMatchesMigration:
    @pytest.mark.parametrize("model, column", [(Event, "accommodation"), (EventRegistration, "accommodation_selections")])
    def test_the_model_declares_a_nullable_jsonb_without_default_or_index(self, model, column):
        declared = model.__table__.columns[column]
        assert isinstance(declared.type, JSONB)
        assert declared.nullable is True and declared.default is None and declared.server_default is None
        assert not declared.index and not declared.unique and not declared.foreign_keys

    def test_the_migration_adds_exactly_those_two_columns(self):
        text = source()
        added = re.findall(r"op\.add_column\(\s*['\"](\w+)['\"]\s*,\s*sa\.Column\(\s*['\"](\w+)['\"]", text)
        assert added == [("events", "accommodation"), ("event_registrations", "accommodation_selections")]
        assert text.count("postgresql.JSONB") == 2 and text.count("nullable=True") == 2
        dropped = re.findall(r"op\.drop_column\(\s*['\"](\w+)['\"]\s*,\s*['\"](\w+)['\"]", text)
        assert sorted(dropped) == [("event_registrations", "accommodation_selections"), ("events", "accommodation")]

    def test_there_is_no_second_enabled_flag_and_no_accommodation_table(self):
        assert "accommodation_enabled" not in {c.name for c in Event.__table__.columns}  # the switch is modules.accommodation
        assert not [t for t in Event.metadata.tables if "accommodation" in t and t != "events"]


# ===========================================================================
# behaviour-neutral
# ===========================================================================


class TestNeutral:
    def test_no_default_backfill_index_or_table(self):
        for forbidden in ("server_default", "op.execute", "UPDATE", "INSERT", "create_index", "create_table", "nullable=False",
                          "op.get_bind", "alter_column"):
            assert forbidden not in code(), forbidden

    def test_upgrade_adds_two_columns_and_downgrade_drops_them_in_reverse(self):
        upgrade, downgrade = code().split("def downgrade")
        assert re.findall(r"op\.(\w+)\(", upgrade) == ["add_column", "add_column"]
        assert re.findall(r"op\.(\w+)\(", downgrade) == ["drop_column", "drop_column"]
        assert downgrade.index("accommodation_selections") < downgrade.index("'accommodation'")


# ===========================================================================
# the migration really runs, and existing rows stay valid
# ===========================================================================


class TestRunsAndPreservesExistingRows:
    @pytest.fixture
    def engine(self):
        engine = sa.create_engine("sqlite://")
        with engine.begin() as connection:
            # both tables as they are BEFORE this revision, holding production-like rows (including meals, Phase 2.6)
            connection.exec_driver_sql(
                "CREATE TABLE events (id VARCHAR(36) PRIMARY KEY, title VARCHAR(255), status VARCHAR(20) NOT NULL, "
                "event_type VARCHAR(30), modules JSON, meals JSON)")
            connection.exec_driver_sql(
                "CREATE TABLE event_registrations (id VARCHAR(36) PRIMARY KEY, event_id VARCHAR(36), participant_email VARCHAR(255), "
                "status VARCHAR(20), qr_code VARCHAR(255), registration_source VARCHAR(20), meal_selections JSON)")
            connection.exec_driver_sql(
                "INSERT INTO events VALUES ('legacy-1', 'Old event', 'published', NULL, NULL, NULL), "
                "('conf-1', 'Configured event', 'published', 'conference', "
                "'{\"meals\": true, \"accommodation\": true, \"registration\": true}', "
                "'{\"options\": [{\"id\": \"lunch\", \"name\": \"Lunch\", \"active\": true}]}')")
            connection.exec_driver_sql(
                "INSERT INTO event_registrations VALUES ('r1', 'legacy-1', 'a@example.com', 'confirmed', 'QR1', NULL, NULL), "
                "('r2', 'conf-1', 'b@example.com', 'attended', 'QR2', 'walk_in', '[\"lunch\"]')")
        return engine

    def columns(self, engine, table):
        return {c["name"]: c for c in sa.inspect(engine).get_columns(table)}

    def run(self, engine, migration_module, direction):
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                getattr(migration_module, direction)()

    def test_upgrade_adds_two_nullable_columns(self, engine, migration_module):
        events_before, registrations_before = set(self.columns(engine, "events")), set(self.columns(engine, "event_registrations"))
        self.run(engine, migration_module, "upgrade")
        events, registrations = self.columns(engine, "events"), self.columns(engine, "event_registrations")
        assert set(events) - events_before == {"accommodation"} and set(registrations) - registrations_before == {"accommodation_selections"}
        for column in (events["accommodation"], registrations["accommodation_selections"]):
            assert column["nullable"] and column["default"] is None

    def test_existing_rows_remain_valid_and_null(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.connect() as connection:
            events = connection.exec_driver_sql(
                "SELECT id, title, status, event_type, modules, meals, accommodation FROM events ORDER BY id").all()
            registrations = connection.exec_driver_sql(
                "SELECT id, participant_email, status, registration_source, meal_selections, accommodation_selections "
                "FROM event_registrations ORDER BY id").all()
        assert [tuple(r) for r in events] == [
            ("conf-1", "Configured event", "published", "conference",
             '{"meals": true, "accommodation": true, "registration": true}',
             '{"options": [{"id": "lunch", "name": "Lunch", "active": true}]}', None),
            ("legacy-1", "Old event", "published", None, None, None, None),
        ]  # the Phase 2.2 module flags and Phase 2.6 meals are untouched and nothing was backfilled
        assert [tuple(r) for r in registrations] == [
            ("r1", "a@example.com", "confirmed", None, None, None),
            ("r2", "b@example.com", "attended", "walk_in", '["lunch"]', None),
        ]

    def test_no_destructive_backfill_and_no_index(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        inspector = sa.inspect(engine)
        assert inspector.get_indexes("events") == [] and inspector.get_indexes("event_registrations") == []
        assert set(inspector.get_table_names()) == {"events", "event_registrations"}  # no accommodation table

    def test_code_that_predates_the_columns_still_inserts(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        with engine.begin() as connection:  # what every existing create path does: it never mentions the new columns
            connection.exec_driver_sql("INSERT INTO events (id, title, status) VALUES ('new-event', 'Created by old code', 'draft')")
            connection.exec_driver_sql(
                "INSERT INTO event_registrations (id, event_id, participant_email, status, qr_code) VALUES "
                "('r3', 'new-event', 'c@example.com', 'confirmed', 'QR3')")
            assert connection.exec_driver_sql("SELECT accommodation FROM events WHERE id = 'new-event'").scalar() is None
            assert connection.exec_driver_sql("SELECT accommodation_selections FROM event_registrations WHERE id = 'r3'").scalar() is None

    def test_values_round_trip(self, engine, migration_module):
        self.run(engine, migration_module, "upgrade")
        options = {"options": [{"id": "shared-room", "name": "Shared Room", "active": True}]}
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE events SET accommodation = ? WHERE id = 'conf-1'", (json.dumps(options),))
            connection.exec_driver_sql("UPDATE event_registrations SET accommodation_selections = ? WHERE id = 'r2'", (json.dumps(["shared-room"]),))
            assert json.loads(connection.exec_driver_sql("SELECT accommodation FROM events WHERE id = 'conf-1'").scalar()) == options
            assert json.loads(connection.exec_driver_sql("SELECT accommodation_selections FROM event_registrations WHERE id = 'r2'").scalar()) == ["shared-room"]

    def test_downgrade_restores_the_previous_shape_without_touching_other_data(self, engine, migration_module):
        originals = set(self.columns(engine, "events")), set(self.columns(engine, "event_registrations"))
        self.run(engine, migration_module, "upgrade")
        self.run(engine, migration_module, "downgrade")
        assert (set(self.columns(engine, "events")), set(self.columns(engine, "event_registrations"))) == originals
        with engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT COUNT(*) FROM events").scalar() == 2
            assert connection.exec_driver_sql("SELECT meals FROM events WHERE id = 'conf-1'").scalar() == \
                '{"options": [{"id": "lunch", "name": "Lunch", "active": true}]}'
            assert connection.exec_driver_sql("SELECT registration_source FROM event_registrations WHERE id = 'r2'").scalar() == "walk_in"

    def test_upgrade_downgrade_upgrade_round_trips(self, engine, migration_module):
        for direction in ("upgrade", "downgrade", "upgrade"):
            self.run(engine, migration_module, direction)
        assert "accommodation" in self.columns(engine, "events") and "accommodation_selections" in self.columns(engine, "event_registrations")

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
    def test_upgrade_ddl_is_two_plain_add_columns_and_a_version_bump(self):
        assert statements(alembic_sql("upgrade", f"{PARENT}:{REVISION}")) == [
            "BEGIN;",
            "ALTER TABLE events ADD COLUMN accommodation JSONB;",
            "ALTER TABLE event_registrations ADD COLUMN accommodation_selections JSONB;",
            f"UPDATE alembic_version SET version_num='{REVISION}' WHERE alembic_version.version_num = '{PARENT}';",
            "COMMIT;",
        ]

    def test_downgrade_ddl_drops_them_again(self):
        assert statements(alembic_sql("downgrade", f"{REVISION}:{PARENT}")) == [
            "BEGIN;",
            "ALTER TABLE event_registrations DROP COLUMN accommodation_selections;",
            "ALTER TABLE events DROP COLUMN accommodation;",
            f"UPDATE alembic_version SET version_num='{PARENT}' WHERE alembic_version.version_num = '{REVISION}';",
            "COMMIT;",
        ]
