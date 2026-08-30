"""add discussions to trainings"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "y6z7x8w9v0u1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade() -> None:
    op.add_column("trainings", sa.Column("discussions", JSONB, nullable=True, server_default="[]"))
def downgrade() -> None:
    op.drop_column("trainings", "discussions")
