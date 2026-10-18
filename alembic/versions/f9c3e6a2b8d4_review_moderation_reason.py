"""keep the Super Admin's reason on the review, so its author can read it

When a Super Admin rejects, holds back (pending) or deletes a review, the reason is now shown to the review's author
(my_review / GET .../reviews/me) as `reason`, in every module. It is kept on the review itself and cleared when the review
is approved again or when its author resubmits it.

Existing reviews have no reason (NULL).

Revision ID: f9c3e6a2b8d4
Revises: e8b2d5f1a7c3
Create Date: 2026-10-18 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f9c3e6a2b8d4'
down_revision = 'e8b2d5f1a7c3'
branch_labels = None
depends_on = None

TABLES = ('training_reviews', 'product_reviews', 'service_reviews', 'event_feedback')


def upgrade():
    for table in TABLES:
        op.add_column(table, sa.Column('moderation_reason', sa.String(length=500), nullable=True))


def downgrade():
    for table in reversed(TABLES):
        op.drop_column(table, 'moderation_reason')
