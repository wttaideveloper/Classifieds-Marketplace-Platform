"""add duration to trainings"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "z0a1b2c3d4e5f6"
down_revision: Union[str, None] = "y9z0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade() -> None:
    op.add_column("trainings", sa.Column("duration", sa.String(length=50), nullable=True))
def downgrade() -> None:
    op.drop_column("trainings", "duration")
