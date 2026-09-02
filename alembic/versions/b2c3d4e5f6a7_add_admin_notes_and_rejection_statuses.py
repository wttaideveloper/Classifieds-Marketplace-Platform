"""add admin notes and rejection statuses for event approval flow

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-09-02 14:55:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add last_admin_notes to events — Super Admin's latest reject/request-changes message
    op.add_column("events", sa.Column("last_admin_notes", sa.Text(), nullable=True))

    # Add notes to event_audits — admin reason/message for reject/request_changes
    op.add_column("event_audits", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("event_audits", "notes")
    op.drop_column("events", "last_admin_notes")
