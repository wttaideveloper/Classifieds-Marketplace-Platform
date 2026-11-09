"""Event and Training forms in the common contract: review tracking on form versions, and a separate column for enterprise-admin-only custom values.

Revision ID: l9c4e7a1d2f6
Revises: k8b3d6f9c2e5
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "l9c4e7a1d2f6"
down_revision = "k8b3d6f9c2e5"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("event_form_configuration_versions", "training_form_configuration_versions"):
        op.add_column(table, sa.Column("reviewed_hash", sa.String(64), nullable=True))
        op.add_column(table, sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        op.add_column(table, sa.Column("reviewed_by", sa.String(255), nullable=True))
    op.add_column("events", sa.Column("private_custom_values", postgresql.JSONB(), nullable=True))
    op.add_column("trainings", sa.Column("private_custom_values", postgresql.JSONB(), nullable=True))


def downgrade():
    op.drop_column("trainings", "private_custom_values")
    op.drop_column("events", "private_custom_values")
    for table in ("training_form_configuration_versions", "event_form_configuration_versions"):
        op.drop_column(table, "reviewed_by")
        op.drop_column(table, "reviewed_at")
        op.drop_column(table, "reviewed_hash")
