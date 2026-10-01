"""add event_session_attendance

Phase 2.4 (session check-in): one new table recording an attendee's attendance at one session of an Event.

Event sessions live in ``events.sessions`` (JSONB), so ``session_id`` is an id inside that list and is
deliberately NOT a foreign key. The two real foreign keys are ``events.id`` and ``event_registrations.id``;
both cascade, because an attendance row is meaningless without them.

- ``UNIQUE (event_id, registration_id, session_id)`` — one row per attendee per session. Its index also
  serves (event_id, registration_id) lookups through its leading columns, so no separate index is created
  for that pair.
- ``(event_id, session_id)`` — per-session counts for the dashboard / attendance report.
- ``(registration_id)`` — per-attendee lookups (attendee detail) and the cascade from event_registrations.
  ``event_id`` alone is covered by the leading column of the two composite indexes above.

Purely additive: a new table, no change to any existing table, no data written or backfilled. Event-level
check-in (``event_registrations.status`` / ``checked_in_at`` / ``session_id``) is untouched.

Migration graph note: chained onto ``adae1a2909cc`` (Phase 2.2), the head that already carries the
event-domain schema work, so it advances that head instead of creating or merging one. It depends only on
``events`` (``n4o5p6q7r8s9``) and ``event_registrations`` (``o5p6q7r8s9t0``), both in the ancestry of every
head, and no other revision uses this table name. Deployments already run ``alembic upgrade heads``.

Revision ID: 1e40e81e2937
Revises: adae1a2909cc
Create Date: 2026-09-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1e40e81e2937'
down_revision: Union[str, None] = 'adae1a2909cc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'event_session_attendance',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('registration_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', sa.String(length=100), nullable=False),
        sa.Column('checked_in_at', sa.DateTime(), nullable=False),
        sa.Column('checked_in_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('checked_out_at', sa.DateTime(), nullable=True),
        sa.Column('checked_out_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['registration_id'], ['event_registrations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', 'registration_id', 'session_id', name='uq_event_session_attendance'),
    )
    op.create_index('ix_event_session_attendance_event_session', 'event_session_attendance', ['event_id', 'session_id'])
    op.create_index('ix_event_session_attendance_registration', 'event_session_attendance', ['registration_id'])


def downgrade() -> None:
    op.drop_index('ix_event_session_attendance_registration', table_name='event_session_attendance')
    op.drop_index('ix_event_session_attendance_event_session', table_name='event_session_attendance')
    op.drop_table('event_session_attendance')
