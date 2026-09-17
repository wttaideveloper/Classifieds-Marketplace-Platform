"""Add the single-provider assignment used by Services to Products.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("provider_user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("products", sa.Column("provider_name", sa.String(255), nullable=True))
    op.create_index("ix_products_provider_user_id", "products", ["provider_user_id"])


def downgrade():
    op.drop_index("ix_products_provider_user_id", table_name="products")
    op.drop_column("products", "provider_name")
    op.drop_column("products", "provider_user_id")
