"""add unique qr_code"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, None] = "z0a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
def upgrade() -> None:
    op.create_index(op.f("ix_event_registrations_qr_code"), "event_registrations", ["qr_code"], unique=True)
def downgrade() -> None:
    op.drop_index(op.f("ix_event_registrations_qr_code"), table_name="event_registrations")
