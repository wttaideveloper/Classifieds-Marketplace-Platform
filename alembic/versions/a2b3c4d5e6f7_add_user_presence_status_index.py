"""add composite index on user_presence(status, updated_at)

GET /api/v1/presence/online filters WHERE status = 'online' AND updated_at >=
cutoff with no supporting index — status and updated_at were both plain,
unindexed columns, forcing a full sequential scan of user_presence on every
call. This is the primary suspect for that endpoint's 20-30s production
latency under load. Adding a composite index lets the planner satisfy the
filter directly instead of scanning every row.

Revision ID: a2b3c4d5e6f7
Revises: z1a2b3c4d5e6
Create Date: 2026-09-21 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a2b3c4d5e6f7'
down_revision = 'z1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        'ix_user_presence_status_updated_at',
        'user_presence',
        ['status', 'updated_at'],
    )


def downgrade():
    op.drop_index('ix_user_presence_status_updated_at', table_name='user_presence')
