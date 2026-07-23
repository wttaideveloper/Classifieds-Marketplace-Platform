"""add onboarding form enterprise_type and registration_type

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-01 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "onboarding_forms",
        sa.Column("enterprise_type", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "onboarding_forms",
        sa.Column("registration_type", sa.String(length=20), nullable=True),
    )
    op.create_index(
        "ix_onboarding_forms_enterprise_type",
        "onboarding_forms",
        ["enterprise_type"],
    )
    op.create_index(
        "ix_onboarding_forms_registration_type",
        "onboarding_forms",
        ["registration_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_onboarding_forms_registration_type", table_name="onboarding_forms")
    op.drop_index("ix_onboarding_forms_enterprise_type", table_name="onboarding_forms")
    op.drop_column("onboarding_forms", "registration_type")
    op.drop_column("onboarding_forms", "enterprise_type")
