"""add_assignment_submission_files

Adds a JSONB `files` column to training_assignment_submissions to support
multi-media assignment submissions (images/videos/documents/links).

Revision ID: b3c4d5e6f7a8
Revises: a0b1c2d3e4f6
Create Date: 2026-09-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'b3c4d5e6f7a8'
down_revision = 'a0b1c2d3e4f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'training_assignment_submissions',
        sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('training_assignment_submissions', 'files')