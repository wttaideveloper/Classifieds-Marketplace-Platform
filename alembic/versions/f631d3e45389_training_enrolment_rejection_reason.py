"""Add rejection_reason to TrainingEnrolment — distinct 'rejected' status, with a reason, separate from self-service 'cancelled'."""
from alembic import op
import sqlalchemy as sa

revision = "f631d3e45389"
down_revision = "dfa304b56b99"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("training_enrolments", sa.Column("rejection_reason", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("training_enrolments", "rejection_reason")
