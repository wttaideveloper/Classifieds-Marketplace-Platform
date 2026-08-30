"""add requires_reapproval column to events"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "y6z7x8w9v0u1"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("requires_reapproval", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("events", "requires_reapproval")