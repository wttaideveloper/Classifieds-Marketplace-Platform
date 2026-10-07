"""Training waitlist: remember the learner's application user id so promotion keeps it."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d8f2b4a9c1e6"
down_revision = "c5e8a1f3d7b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("training_waitlist", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_training_waitlist_user_id", "training_waitlist", ["user_id"])


def downgrade():
    op.drop_index("ix_training_waitlist_user_id", table_name="training_waitlist")
    op.drop_column("training_waitlist", "user_id")
