"""add event_type and modules to events

Phase 2.2 (configurable events): two nullable columns on ``events``.

- ``event_type`` String(30): conference|workshop|marathon|camp|private_function|webinar|other
- ``modules``    JSONB: {registration, tickets, sessions, check_in, online_meeting, custom_questions,
                 meals, accommodation} -> bool

Deliberately additive and behaviour-neutral:

- both columns are NULLABLE with NO server default and NO backfill. Existing production events keep
  NULL/NULL and are resolved on read from their actual behaviour (app/utils/event_modules.py), so no
  event gains or loses a capability because of this migration.
- no index: nothing filters on event_type yet.

Migration graph note: this revision is chained onto ``e2b1c2d3e4f7`` (the head that already carries the
event-domain schema work), so it advances that head instead of creating or merging one. The columns
depend only on the ``events`` table (``n4o5p6q7r8s9``), which is in the ancestry of every head, and
neither name is used by any other revision, so it is order-independent with respect to the other
heads. Deployments already run ``alembic upgrade heads``.

Revision ID: adae1a2909cc
Revises: e2b1c2d3e4f7
Create Date: 2026-09-25 00:47:50.632162

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'adae1a2909cc'
down_revision: Union[str, None] = 'e2b1c2d3e4f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('events', sa.Column('event_type', sa.String(length=30), nullable=True))
    op.add_column('events', sa.Column('modules', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('events', 'modules')
    op.drop_column('events', 'event_type')
