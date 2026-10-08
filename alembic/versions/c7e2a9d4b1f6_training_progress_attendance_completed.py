"""remember which lesson completions were created by attendance

Marking a learner "attended" on a lesson (manual roster or QR scan) now completes that lesson for them. Reversing
the attendance must undo only the completion that attendance created, never one the learner earned themselves, so
the lessons completed by attendance are listed on the learner's progress row.

Existing rows start with an empty list: nothing already completed is treated as attendance-created, so no existing
completion can be undone by a later attendance change.

Revision ID: c7e2a9d4b1f6
Revises: a3c5e7f9b1d4
Create Date: 2026-10-09 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c7e2a9d4b1f6'
down_revision = 'a3c5e7f9b1d4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'training_progress',
        sa.Column(
            'attendance_completed_lessons',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade():
    op.drop_column('training_progress', 'attendance_completed_lessons')
