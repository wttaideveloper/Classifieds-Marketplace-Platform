"""add provider user linkage to services

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-23 16:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "services",
        sa.Column("provider_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_services_provider_user_id", "services", ["provider_user_id"])


def downgrade() -> None:
    op.drop_index("ix_services_provider_user_id", table_name="services")
    op.drop_column("services", "provider_user_id")
