"""add events.meals and event_registrations.meal_selections

Phase 2.6 (meals): two nullable JSONB columns, one on each table.

- ``events.meals``                        JSONB: ``{"options": [{id, name, description?, date?, active}]}``
- ``event_registrations.meal_selections`` JSONB: a JSON list of the meal option ids the attendee selected

Deliberately additive and behaviour-neutral:

- both columns are NULLABLE with NO server default and NO backfill. Every existing event keeps ``meals = NULL``
  (read as "no options"; meals stay off unless ``modules.meals`` says otherwise, exactly as before) and every existing
  registration keeps ``meal_selections = NULL`` (read as "none selected"). No row is rewritten or even touched.
- no index: attendee lists and meal counts are always scoped to one event, which is already indexed, and the counts
  are one grouped query over that event's registrations.
- no table: meal options are event configuration and selections are a property of the registration.
- whether meals are enabled is NOT stored here; it stays ``modules.meals`` (Phase 2.2), the single truth.

Migration graph note: chained onto ``fe61c574e6f5`` (Phase 2.5), the head that already carries the event-domain schema
work, so it advances that head instead of creating or merging one. It depends only on ``events`` (``n4o5p6q7r8s9``) and
``event_registrations`` (``o5p6q7r8s9t0``), both in the ancestry of every head, and neither column name is used by any
other revision. Deployments already run ``alembic upgrade heads``.

Revision ID: d3a5cd7d0a58
Revises: fe61c574e6f5
Create Date: 2026-09-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd3a5cd7d0a58'
down_revision: Union[str, None] = 'fe61c574e6f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('meals', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('event_registrations', sa.Column('meal_selections', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('event_registrations', 'meal_selections')
    op.drop_column('events', 'meals')
