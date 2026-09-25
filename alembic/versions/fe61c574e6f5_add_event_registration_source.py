"""add registration_source to event_registrations

Phase 2.5 (walk-in registration): one nullable column on ``event_registrations``.

- ``registration_source`` String(20): ``walk_in`` for a registration an organizer made at the venue.

Deliberately additive and behaviour-neutral:

- NULLABLE with NO server default and NO backfill. Every existing row, and every online registration or
  checkout made afterwards (those code paths do not write it), stays NULL, which the API reads as "online".
  Nothing about an existing registration changes.
- no index: attendee lists are always scoped to one event, which is already indexed.
- no CHECK constraint, matching how the other status-like columns of this table are handled; the API
  validates the value.
- no ``registered_by`` column: the acting operator is recorded on the ``event_audits`` row.

Migration graph note: chained onto ``1e40e81e2937`` (Phase 2.4), the head that already carries the event-domain
schema work, so it advances that head instead of creating or merging one. It depends only on the
``event_registrations`` table (``o5p6q7r8s9t0``), which is in the ancestry of every head, and no other
revision uses the column name. Deployments already run ``alembic upgrade heads``.

Revision ID: fe61c574e6f5
Revises: 1e40e81e2937
Create Date: 2026-09-25 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fe61c574e6f5'
down_revision: Union[str, None] = '1e40e81e2937'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('event_registrations', sa.Column('registration_source', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('event_registrations', 'registration_source')
