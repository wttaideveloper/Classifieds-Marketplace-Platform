"""Persist administrator-supplied training notes PDF URL."""
from alembic import op
import sqlalchemy as sa

revision = "x5y6z7a8b9c0"
down_revision = "w4x5y6z7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("trainings", sa.Column("notes_pdf_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("trainings", "notes_pdf_url")
