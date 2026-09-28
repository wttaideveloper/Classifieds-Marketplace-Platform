"""Add TrainingCategory table — Super Admin-managed Training category/subcategory taxonomy, separate from EventCategory."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "dfa304b56b99"
down_revision = "8e1805a6e5e2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "training_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("training_categories.id"), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_training_categories_name", "training_categories", ["name"], unique=True)
    op.create_index("ix_training_categories_parent_id", "training_categories", ["parent_id"])


def downgrade():
    op.drop_table("training_categories")
