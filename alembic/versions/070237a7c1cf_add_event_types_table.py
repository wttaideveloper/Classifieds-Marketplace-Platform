"""add event_types table and seed the existing 7 Event Types

Phase 2.2 (configurable events), evolved: Event Types move from a hardcoded Python table
(``app/utils/event_modules.EVENT_TYPE_DEFAULT_MODULES``, removed) into backend-authoritative,
super-admin-manageable configuration rows.

- ``event_types``: one row per Event Type — ``key`` (the stable identifier ``events.event_type``
  stores), ``name``, ``active``, and three full 8-module-key JSONB dicts (``default_modules``,
  ``allowed_modules``, ``required_modules``), same shape as ``Event.modules``.
- Seeds the 7 Event Types that existed as hardcoded Python constants before this migration, with
  ``default_modules`` copied EXACTLY from the removed ``EVENT_TYPE_DEFAULT_MODULES`` table, so an
  already-created event of any of these types is completely unaffected (its own ``modules`` column was
  already computed and persisted at creation time — see the "compute once, never re-derive on read"
  rule this system has followed since Phase 2.2). ``allowed_modules`` is seeded as "everything allowed"
  and ``required_modules`` as "nothing required" for all 7, matching the fact that nothing was ever
  restricted or required by type before this migration — only the pre-existing, orthogonal, generic
  rules (`online_meeting` needs an online/hybrid `delivery_mode`; `tickets` cannot be off for a paid
  event) applied, and those are unchanged.

Deliberately additive and behaviour-neutral:

- ``events.event_type`` is UNCHANGED: still a plain ``VARCHAR(30)``, no foreign key added. An Event
  Type can be deactivated, or even deleted once unused, without ever touching an existing Event row —
  reading an Event never re-validates its stored ``event_type``/``modules`` against this table.
- No column on any other table changes. No backfill of any Event. No index beyond the new table's own
  primary key and the unique index on ``key`` (every lookup is scoped to that one indexed column).

Migration graph note: chained onto ``119f4bd408a4`` (Phase 2.7), the head that already carries the
event-domain schema work, so it advances that head instead of creating or merging one. Deployments
already run ``alembic upgrade heads``.

Revision ID: 070237a7c1cf
Revises: 119f4bd408a4
Create Date: 2026-09-27 15:00:00.000000

"""
import json
import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '070237a7c1cf'
down_revision: Union[str, None] = '119f4bd408a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MODULE_KEYS = (
    "registration", "tickets", "sessions", "check_in",
    "online_meeting", "custom_questions", "meals", "accommodation",
)


def _modules(**flags: bool) -> dict:
    assert set(flags) == set(MODULE_KEYS)
    return {key: flags[key] for key in MODULE_KEYS}


_ALL_ALLOWED = {key: True for key in MODULE_KEYS}
_NONE_REQUIRED = {key: False for key in MODULE_KEYS}

# Exactly the removed app.utils.event_modules.EVENT_TYPE_DEFAULT_MODULES table.
SEED_DEFAULT_MODULES = {
    "conference": _modules(
        registration=True, tickets=True, sessions=True, check_in=True,
        online_meeting=False, custom_questions=False, meals=True, accommodation=True,
    ),
    "workshop": _modules(
        registration=True, tickets=True, sessions=True, check_in=True,
        online_meeting=False, custom_questions=False, meals=False, accommodation=False,
    ),
    "marathon": _modules(
        registration=True, tickets=True, sessions=False, check_in=True,
        online_meeting=False, custom_questions=False, meals=False, accommodation=False,
    ),
    "camp": _modules(
        registration=True, tickets=False, sessions=False, check_in=True,
        online_meeting=False, custom_questions=False, meals=True, accommodation=True,
    ),
    "private_function": _modules(
        registration=True, tickets=False, sessions=False, check_in=True,
        online_meeting=False, custom_questions=True, meals=True, accommodation=False,
    ),
    "webinar": _modules(
        registration=True, tickets=False, sessions=True, check_in=False,
        online_meeting=True, custom_questions=False, meals=False, accommodation=False,
    ),
    "other": _modules(
        registration=True, tickets=False, sessions=False, check_in=False,
        online_meeting=False, custom_questions=False, meals=False, accommodation=False,
    ),
}
SEED_NAMES = {
    "conference": "Conference",
    "workshop": "Workshop",
    "marathon": "Marathon",
    "camp": "Camp",
    "private_function": "Private Function",
    "webinar": "Webinar",
    "other": "Other",
}
# The Phase 2.2 default order (also the order every prior migration/test file lists them in).
SEED_ORDER = ("conference", "workshop", "marathon", "camp", "private_function", "webinar", "other")


def upgrade() -> None:
    op.create_table(
        'event_types',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('key', sa.String(length=30), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('default_modules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('allowed_modules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('required_modules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_event_types_key', 'event_types', ['key'], unique=True)

    # The insert-side column types are deliberately sa.Text (not JSONB): op.bulk_insert renders literal
    # SQL text for `alembic upgrade --sql` (offline mode), and SQLAlchemy has no literal renderer for a
    # Python dict bound as JSONB. A JSON-formatted text literal assigns straight into a jsonb column
    # (Postgres) or the JSON-shimmed column tests use (SQLite) without any explicit cast either way.
    event_types = sa.table(
        'event_types',
        sa.column('id', postgresql.UUID(as_uuid=True)),
        sa.column('key', sa.String),
        sa.column('name', sa.String),
        sa.column('active', sa.Boolean),
        sa.column('default_modules', sa.Text),
        sa.column('allowed_modules', sa.Text),
        sa.column('required_modules', sa.Text),
        sa.column('created_at', sa.DateTime),
        sa.column('updated_at', sa.DateTime),
    )
    now = datetime.utcnow()
    op.bulk_insert(event_types, [
        {
            "id": uuid.uuid4(),
            "key": key,
            "name": SEED_NAMES[key],
            "active": True,
            "default_modules": json.dumps(SEED_DEFAULT_MODULES[key], sort_keys=True),
            "allowed_modules": json.dumps(_ALL_ALLOWED, sort_keys=True),
            "required_modules": json.dumps(_NONE_REQUIRED, sort_keys=True),
            "created_at": now,
            "updated_at": now,
        }
        for key in SEED_ORDER
    ])


def downgrade() -> None:
    op.drop_index('ix_event_types_key', table_name='event_types')
    op.drop_table('event_types')
