"""Per-lesson playback position tracking on TrainingProgress, for video resume."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d5e6f7a8b9c0"
down_revision = "x5y6z7a8b9c0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "training_progress",
        sa.Column("lesson_positions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade():
    op.drop_column("training_progress", "lesson_positions")
