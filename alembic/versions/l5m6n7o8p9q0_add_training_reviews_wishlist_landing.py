"""add training reviews, wishlist, and course-landing-page fields

Revision ID: l5m6n7o8p9q0
Revises: k4l5m6n7o8p9
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "l5m6n7o8p9q0"
down_revision: Union[str, Sequence[str], None] = "k4l5m6n7o8p9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("target_audience", sa.Text(), nullable=True))
    op.add_column("trainings", sa.Column("level", sa.String(20), nullable=True))
    op.add_column("trainings", sa.Column("language", sa.String(50), nullable=True, server_default="English"))

    op.create_table(
        "training_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("training_id", UUID(as_uuid=True), sa.ForeignKey("trainings.id"), nullable=False),
        sa.Column("participant_email", sa.String(255), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_training_reviews_training_id", "training_reviews", ["training_id"])
    op.create_index(
        "ix_training_reviews_training_email", "training_reviews",
        ["training_id", "participant_email"], unique=True,
    )

    op.create_table(
        "training_wishlist_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("training_id", UUID(as_uuid=True), sa.ForeignKey("trainings.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_training_wishlist_items_user_id", "training_wishlist_items", ["user_id"])
    op.create_index("ix_training_wishlist_items_training_id", "training_wishlist_items", ["training_id"])
    op.create_index(
        "ix_training_wishlist_user_training", "training_wishlist_items",
        ["user_id", "training_id"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_training_wishlist_user_training", table_name="training_wishlist_items")
    op.drop_index("ix_training_wishlist_items_training_id", table_name="training_wishlist_items")
    op.drop_index("ix_training_wishlist_items_user_id", table_name="training_wishlist_items")
    op.drop_table("training_wishlist_items")

    op.drop_index("ix_training_reviews_training_email", table_name="training_reviews")
    op.drop_index("ix_training_reviews_training_id", table_name="training_reviews")
    op.drop_table("training_reviews")

    op.drop_column("trainings", "language")
    op.drop_column("trainings", "level")
    op.drop_column("trainings", "target_audience")
