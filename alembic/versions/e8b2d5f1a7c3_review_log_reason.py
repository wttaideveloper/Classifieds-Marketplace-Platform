"""keep the reason when a Super Admin rejects or deletes a training or event review

A Super Admin now has to say why when rejecting or deleting a training or event review. The reason is saved with the
moderation history row and returned by GET /reviews/audit as `reason`. Older rows, and rows for other actions or for
product and service reviews, have no reason (NULL).

Revision ID: e8b2d5f1a7c3
Revises: d4f8a2c6e1b9
Create Date: 2026-10-16 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'e8b2d5f1a7c3'
down_revision = 'd4f8a2c6e1b9'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('review_moderation_log', sa.Column('reason', sa.String(length=500), nullable=True))


def downgrade():
    op.drop_column('review_moderation_log', 'reason')
