"""add extended fields for enterprise product service

Revision ID: a1b2c3d4e5f6
Revises: 334131df0950
Create Date: 2026-06-19 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "334131df0950"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enterprise extended fields
    op.add_column("enterprises", sa.Column("suite_unit", sa.String(length=100), nullable=True))
    op.add_column("enterprises", sa.Column("registration_number", sa.String(length=100), nullable=True))
    op.add_column("enterprises", sa.Column("business_category", sa.String(length=100), nullable=True))
    op.add_column("enterprises", sa.Column("website_url", sa.Text(), nullable=True))
    op.add_column("enterprises", sa.Column("year_founded", sa.Integer(), nullable=True))
    op.add_column("enterprises", sa.Column("primary_contact_name", sa.String(length=255), nullable=True))
    op.add_column("enterprises", sa.Column("primary_contact_title", sa.String(length=100), nullable=True))
    op.add_column("enterprises", sa.Column("secondary_email", sa.String(length=255), nullable=True))
    op.add_column("enterprises", sa.Column("secondary_phone", sa.String(length=30), nullable=True))
    op.add_column("enterprises", sa.Column("brand_color", sa.String(length=20), nullable=True))
    op.add_column("enterprises", sa.Column("tagline", sa.String(length=255), nullable=True))
    op.add_column(
        "enterprises",
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    # Product extended fields
    op.add_column("products", sa.Column("sku", sa.String(length=100), nullable=True))
    op.add_column("products", sa.Column("barcode_upc", sa.String(length=100), nullable=True))
    op.add_column("products", sa.Column("weight", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("dimensions", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("sale_price", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("cost_price", sa.Float(), nullable=True))
    op.add_column("products", sa.Column("tax_class", sa.String(length=50), nullable=True))
    op.add_column("products", sa.Column("currency", sa.String(length=3), server_default="USD", nullable=True))
    op.add_column("products", sa.Column("stock_quantity", sa.Integer(), server_default="0", nullable=True))
    op.add_column("products", sa.Column("low_stock_alert_threshold", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("stock_management", sa.String(length=50), nullable=True))
    op.add_column(
        "products",
        sa.Column("publish_status", sa.String(length=50), server_default="draft", nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )

    # Service extended fields
    op.add_column("services", sa.Column("max_participants", sa.Integer(), nullable=True))
    op.add_column("services", sa.Column("provider_name", sa.String(length=255), nullable=True))
    op.add_column("services", sa.Column("instructor_name", sa.String(length=255), nullable=True))
    op.add_column("services", sa.Column("delivery_format", sa.String(length=100), nullable=True))
    op.add_column("services", sa.Column("package_price", sa.Float(), nullable=True))
    op.add_column("services", sa.Column("currency", sa.String(length=3), server_default="USD", nullable=True))
    op.add_column("services", sa.Column("cancellation_policy", sa.Text(), nullable=True))
    op.add_column(
        "services",
        sa.Column("availability_schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "services",
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("services", "created_at")
    op.drop_column("services", "availability_schedule")
    op.drop_column("services", "cancellation_policy")
    op.drop_column("services", "currency")
    op.drop_column("services", "package_price")
    op.drop_column("services", "delivery_format")
    op.drop_column("services", "instructor_name")
    op.drop_column("services", "provider_name")
    op.drop_column("services", "max_participants")

    op.drop_column("products", "created_at")
    op.drop_column("products", "publish_status")
    op.drop_column("products", "stock_management")
    op.drop_column("products", "low_stock_alert_threshold")
    op.drop_column("products", "stock_quantity")
    op.drop_column("products", "currency")
    op.drop_column("products", "tax_class")
    op.drop_column("products", "cost_price")
    op.drop_column("products", "sale_price")
    op.drop_column("products", "dimensions")
    op.drop_column("products", "weight")
    op.drop_column("products", "barcode_upc")
    op.drop_column("products", "sku")

    op.drop_column("enterprises", "created_at")
    op.drop_column("enterprises", "tagline")
    op.drop_column("enterprises", "brand_color")
    op.drop_column("enterprises", "secondary_phone")
    op.drop_column("enterprises", "secondary_email")
    op.drop_column("enterprises", "primary_contact_title")
    op.drop_column("enterprises", "primary_contact_name")
    op.drop_column("enterprises", "year_founded")
    op.drop_column("enterprises", "website_url")
    op.drop_column("enterprises", "business_category")
    op.drop_column("enterprises", "registration_number")
    op.drop_column("enterprises", "suite_unit")
