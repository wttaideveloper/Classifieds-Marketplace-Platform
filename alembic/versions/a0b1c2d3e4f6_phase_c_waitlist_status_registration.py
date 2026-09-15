"""phase_c_waitlist_status_registration

Revision ID: a0b1c2d3e4f6
Revises: a0b1c2d3e4f5
Create Date: 2026-09-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a0b1c2d3e4f6'
down_revision = 'a0b1c2d3e4f5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add status and registration_id columns to event_waitlist
    op.add_column('event_waitlist', sa.Column('status', sa.String(length=20), server_default='waiting', nullable=True))
    op.add_column('event_waitlist', sa.Column('registration_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_event_waitlist_status'), 'event_waitlist', ['status'], unique=False)
    
    # Wait, the unique index uq_event_waitlist_entry currently allows only ONE waitlist entry per participant per event regardless of status.
    # If a participant leaves the waitlist (status='left'), they couldn't rejoin.
    # The requirement is that they can leave and rejoin. The unique index should be changed to a partial index WHERE status = 'waiting'.
    # We must update the index from Phase A to only apply when status = 'waiting'.
    op.execute("DROP INDEX IF EXISTS uq_event_waitlist_entry")
    op.execute("""
        CREATE UNIQUE INDEX uq_event_waitlist_active
        ON event_waitlist (event_id, lower(participant_email))
        WHERE status = 'waiting'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_event_waitlist_active")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_event_waitlist_entry
        ON event_waitlist (event_id, lower(participant_email))
    """)
    op.drop_index(op.f('ix_event_waitlist_status'), table_name='event_waitlist')
    op.drop_column('event_waitlist', 'registration_id')
    op.drop_column('event_waitlist', 'status')
