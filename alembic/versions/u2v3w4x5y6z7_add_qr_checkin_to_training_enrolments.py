"""add qr_code, checked_out_at, checked_in_by to training_enrolments;
backfill qr_code for legacy rows before adding the unique constraint

Revision ID: r1s2t3u4v5w6
Revises: q0r1s2t3u4v5
Create Date: 2026-09-14
"""
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

revision: str = "u2v3w4x5y6z7"
down_revision: Union[str, Sequence[str], None] = "q0r1s2t3u4v5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("training_enrolments", sa.Column("qr_code", sa.String(255), nullable=True))
    op.add_column("training_enrolments", sa.Column("checked_out_at", sa.DateTime(), nullable=True))
    op.add_column("training_enrolments", sa.Column("checked_in_by", PG_UUID(as_uuid=True), nullable=True))

    # Backfill legacy rows — each needs a distinct code before the unique index can be added.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id FROM training_enrolments WHERE qr_code IS NULL")).fetchall()
    for (row_id,) in rows:
        code = str(uuid.uuid4())[:12].upper()
        conn.execute(sa.text("UPDATE training_enrolments SET qr_code = :code WHERE id = :id"), {"code": code, "id": row_id})

    op.create_unique_constraint("uq_training_enrolments_qr_code", "training_enrolments", ["qr_code"])
    op.create_index("ix_training_enrolments_qr_code", "training_enrolments", ["qr_code"])


def downgrade() -> None:
    op.drop_index("ix_training_enrolments_qr_code", table_name="training_enrolments")
    op.drop_constraint("uq_training_enrolments_qr_code", "training_enrolments", type_="unique")
    op.drop_column("training_enrolments", "checked_in_by")
    op.drop_column("training_enrolments", "checked_out_at")
    op.drop_column("training_enrolments", "qr_code")
