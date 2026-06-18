"""initial schema

Revision ID: 334131df0950
Revises:
Create Date: 2026-06-18 17:11:04.943885

"""
from typing import Sequence, Union

from alembic import op

from app.db.database import Base
from app.models import (  # noqa: F401
    address_model,
    admin_model,
    customer_model,
    merchant_model,
)

# revision identifiers, used by Alembic.
revision: str = "334131df0950"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
