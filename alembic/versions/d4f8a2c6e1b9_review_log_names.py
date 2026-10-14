"""show the customer and the enterprise by name in the review moderation history

The moderation history now says whose review it was and which enterprise owns the item, not only the item and the
moderator. Both names are saved with each history row when the action happens (never an e-mail address), and
GET /reviews/audit returns them as customer_name and business_name.

Existing rows have no saved names (NULL) and keep showing null for these two fields.

Revision ID: d4f8a2c6e1b9
Revises: a9c4e1b7d3f2
Create Date: 2026-10-14 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'd4f8a2c6e1b9'
down_revision = 'a9c4e1b7d3f2'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('review_moderation_log', sa.Column('customer_name', sa.String(length=255), nullable=True))
    op.add_column('review_moderation_log', sa.Column('business_name', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('review_moderation_log', 'business_name')
    op.drop_column('review_moderation_log', 'customer_name')
