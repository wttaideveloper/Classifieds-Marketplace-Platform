"""phase_a_unique_indexes_registration_waitlist

Adds partial unique index on event_registrations to prevent duplicate active
registrations, and a unique index on event_waitlist to prevent duplicate
waitlist entries.

IMPORTANT — run this AFTER verifying no duplicate active registrations or
waitlist entries exist in production. See README / Phase A report for the
data-safety queries to run first.

Revision ID: a0b1c2d3e4f5
Revises: z0a1b2c3d4e5f6
Create Date: 2026-09-15

"""
from alembic import op

# revision identifiers
revision = 'a0b1c2d3e4f5'
down_revision = 'z0a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial unique index: one *active* registration per participant per event.
    # Cancelled registrations are intentionally excluded so a participant can
    # cancel and re-register. Uses lower() for case-insensitive email matching.
    #
    # Before applying, verify no violations exist:
    #   SELECT event_id, lower(participant_email), count(*)
    #   FROM event_registrations
    #   WHERE status IN ('confirmed', 'attended')
    #   GROUP BY event_id, lower(participant_email)
    #   HAVING count(*) > 1;
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_event_reg_active
        ON event_registrations (event_id, lower(participant_email))
        WHERE status IN ('confirmed', 'attended')
    """)

    # Unique index: one waitlist entry per participant per event.
    # Uses lower() for case-insensitive email matching.
    #
    # Before applying, verify no violations exist:
    #   SELECT event_id, lower(participant_email), count(*)
    #   FROM event_waitlist
    #   GROUP BY event_id, lower(participant_email)
    #   HAVING count(*) > 1;
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_event_waitlist_entry
        ON event_waitlist (event_id, lower(participant_email))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_event_waitlist_entry")
    op.execute("DROP INDEX IF EXISTS uq_event_reg_active")
