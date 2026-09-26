"""add events.accommodation and event_registrations.accommodation_selections

Phase 2.7 (accommodation): two nullable JSONB columns, one on each table, exactly like the Phase 2.6 meals columns.

- ``events.accommodation``                        JSONB: ``{"options": [{id, name, description?, active}]}``
- ``event_registrations.accommodation_selections`` JSONB: a JSON list of the accommodation option ids the attendee selected

Deliberately additive and behaviour-neutral:

- both columns are NULLABLE with NO server default and NO backfill. Every existing event keeps ``accommodation = NULL``
  (read as "no options"; accommodation stays off unless ``modules.accommodation`` says otherwise, exactly as before) and
  every existing registration keeps ``accommodation_selections = NULL`` (read as "none selected"). No row is rewritten or
  even touched.
- no index: attendee lists and counts are always scoped to one event, which is already indexed, and the counts are one
  grouped query over that event's registrations.
- no table: accommodation options are event configuration and selections are a property of the registration.
- whether accommodation is enabled is NOT stored here; it stays ``modules.accommodation`` (Phase 2.2), the single truth.

Migration graph note: chained onto ``d3a5cd7d0a58`` (Phase 2.6), the head that already carries the event-domain schema
work, so it advances that head instead of creating or merging one (the project's other heads are untouched; deployments
already run ``alembic upgrade heads``). It depends only on ``events`` (``n4o5p6q7r8s9``) and ``event_registrations``
(``o5p6q7r8s9t0``), both in the ancestry of every head, and neither column name is used by any other revision.

Revision ID: 119f4bd408a4
Revises: d3a5cd7d0a58
Create Date: 2026-09-26 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '119f4bd408a4'
down_revision: Union[str, None] = 'd3a5cd7d0a58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('accommodation', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('event_registrations', sa.Column('accommodation_selections', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('event_registrations', 'accommodation_selections')
    op.drop_column('events', 'accommodation')
