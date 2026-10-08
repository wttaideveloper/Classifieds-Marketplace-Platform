"""Training reviews: moderation status and updated_at.

New reviews start as pending and are shown publicly only once approved, like product and service reviews.
Reviews that already exist were public, so they are marked approved and stay visible.
"""
from alembic import op
import sqlalchemy as sa

revision = "e9a1c3b5d7f2"
down_revision = "d8f2b4a9c1e6"
branch_labels = None
depends_on = None


def upgrade():
    # Added with 'approved' as the default so every existing row keeps showing, then the default is
    # switched to 'pending' for rows created from now on.
    op.add_column(
        "training_reviews",
        sa.Column("moderation_status", sa.String(length=20), nullable=False, server_default="approved"),
    )
    op.alter_column("training_reviews", "moderation_status", server_default="pending")
    op.create_index("ix_training_reviews_moderation_status", "training_reviews", ["moderation_status"])

    op.add_column("training_reviews", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE training_reviews SET updated_at = created_at")
    op.alter_column("training_reviews", "updated_at", nullable=False, server_default=sa.func.now())


def downgrade():
    op.drop_column("training_reviews", "updated_at")
    op.drop_index("ix_training_reviews_moderation_status", table_name="training_reviews")
    op.drop_column("training_reviews", "moderation_status")
