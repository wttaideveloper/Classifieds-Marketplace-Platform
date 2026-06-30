"""add enterprise locations, tenant support, status enums, and search indexes

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-06-30 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "enterprise_locations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enterprise_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("location_name", sa.String(length=255), nullable=False),
        sa.Column("address_line_1", sa.Text(), nullable=True),
        sa.Column("address_line_2", sa.Text(), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=100), nullable=True),
        sa.Column("postal_code", sa.String(length=20), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["enterprise_id"], ["enterprises.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enterprise_locations_enterprise_id", "enterprise_locations", ["enterprise_id"])
    op.create_index("ix_enterprise_locations_city", "enterprise_locations", ["city"])
    op.create_index("ix_enterprise_locations_status", "enterprise_locations", ["status"])
    op.create_index(
        "ix_enterprise_locations_enterprise_status",
        "enterprise_locations",
        ["enterprise_id", "status"],
    )

    op.add_column("enterprises", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("enterprises", sa.Column("banner_url", sa.Text(), nullable=True))
    op.add_column("enterprises", sa.Column("website", sa.Text(), nullable=True))
    op.add_column(
        "enterprises",
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.execute("UPDATE enterprises SET website = website_url WHERE website IS NULL AND website_url IS NOT NULL")
    op.execute(
        "UPDATE enterprises SET banner_url = business_images "
        "WHERE banner_url IS NULL AND business_images IS NOT NULL"
    )

    op.add_column(
        "enterprises",
        sa.Column("status_new", sa.String(length=20), server_default="draft", nullable=False),
    )
    op.execute(
        "UPDATE enterprises SET status_new = CASE "
        "WHEN status::text IN ('t', 'true', '1') THEN 'active' "
        "WHEN status::text IN ('f', 'false', '0') THEN 'inactive' "
        "WHEN status::text IN ('draft', 'active', 'inactive') THEN status::text "
        "ELSE 'draft' END"
    )
    op.drop_column("enterprises", "status")
    op.alter_column("enterprises", "status_new", new_column_name="status")

    op.create_index("ix_enterprises_tenant_id", "enterprises", ["tenant_id"])
    op.create_index("ix_enterprises_business_short_name", "enterprises", ["business_short_name"])
    op.create_index("ix_enterprises_business_category", "enterprises", ["business_category"])
    op.create_index("ix_enterprises_status", "enterprises", ["status"])
    op.create_index("ix_enterprises_tenant_status", "enterprises", ["tenant_id", "status"])

    op.add_column("products", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("products", sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "products",
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
    )
    op.add_column(
        "products",
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute(
        "UPDATE products SET status = CASE "
        "WHEN product_status IS TRUE THEN 'active' "
        "WHEN product_status IS FALSE THEN 'inactive' "
        "ELSE 'draft' END"
    )
    op.create_foreign_key(
        "fk_products_location_id",
        "products",
        "enterprise_locations",
        ["location_id"],
        ["id"],
    )
    op.create_index("ix_products_tenant_id", "products", ["tenant_id"])
    op.create_index("ix_products_enterprise_id", "products", ["enterprise_id"])
    op.create_index("ix_products_location_id", "products", ["location_id"])
    op.create_index("ix_products_product_name", "products", ["product_name"])
    op.create_index("ix_products_product_category", "products", ["product_category"])
    op.create_index("ix_products_status", "products", ["status"])
    op.create_index("ix_products_tenant_enterprise", "products", ["tenant_id", "enterprise_id"])
    op.create_index("ix_products_enterprise_location", "products", ["enterprise_id", "location_id"])

    op.add_column("services", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("services", sa.Column("location_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "services",
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
    )
    op.add_column(
        "services",
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.execute(
        "UPDATE services SET status = CASE "
        "WHEN service_status IS TRUE THEN 'active' "
        "WHEN service_status IS FALSE THEN 'inactive' "
        "ELSE 'draft' END"
    )
    op.create_foreign_key(
        "fk_services_location_id",
        "services",
        "enterprise_locations",
        ["location_id"],
        ["id"],
    )
    op.create_index("ix_services_tenant_id", "services", ["tenant_id"])
    op.create_index("ix_services_enterprise_id", "services", ["enterprise_id"])
    op.create_index("ix_services_location_id", "services", ["location_id"])
    op.create_index("ix_services_service_name", "services", ["service_name"])
    op.create_index("ix_services_service_category", "services", ["service_category"])
    op.create_index("ix_services_status", "services", ["status"])
    op.create_index("ix_services_tenant_enterprise", "services", ["tenant_id", "enterprise_id"])
    op.create_index("ix_services_enterprise_location", "services", ["enterprise_id", "location_id"])

    op.add_column("dynamic_attributes", sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "dynamic_attributes",
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "dynamic_attributes",
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
    )
    op.add_column(
        "dynamic_attributes",
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("dynamic_attributes", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.execute(
        "UPDATE dynamic_attributes SET created_at = NOW() WHERE created_at IS NULL"
    )
    op.alter_column("dynamic_attributes", "created_at", nullable=False)
    op.create_index("ix_dynamic_attributes_tenant_id", "dynamic_attributes", ["tenant_id"])
    op.create_index("ix_dynamic_attributes_entity_type", "dynamic_attributes", ["entity_type"])
    op.create_index("ix_dynamic_attributes_entity_id", "dynamic_attributes", ["entity_id"])
    op.create_index("ix_dynamic_attributes_status", "dynamic_attributes", ["status"])
    op.create_index(
        "ix_dynamic_attributes_entity",
        "dynamic_attributes",
        ["entity_type", "entity_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_dynamic_attributes_entity", table_name="dynamic_attributes")
    op.drop_index("ix_dynamic_attributes_status", table_name="dynamic_attributes")
    op.drop_index("ix_dynamic_attributes_entity_id", table_name="dynamic_attributes")
    op.drop_index("ix_dynamic_attributes_entity_type", table_name="dynamic_attributes")
    op.drop_index("ix_dynamic_attributes_tenant_id", table_name="dynamic_attributes")
    op.drop_column("dynamic_attributes", "created_at")
    op.drop_column("dynamic_attributes", "is_deleted")
    op.drop_column("dynamic_attributes", "status")
    op.drop_column("dynamic_attributes", "is_required")
    op.drop_column("dynamic_attributes", "tenant_id")

    op.drop_index("ix_services_enterprise_location", table_name="services")
    op.drop_index("ix_services_tenant_enterprise", table_name="services")
    op.drop_index("ix_services_status", table_name="services")
    op.drop_index("ix_services_service_category", table_name="services")
    op.drop_index("ix_services_service_name", table_name="services")
    op.drop_index("ix_services_location_id", table_name="services")
    op.drop_index("ix_services_enterprise_id", table_name="services")
    op.drop_index("ix_services_tenant_id", table_name="services")
    op.drop_constraint("fk_services_location_id", "services", type_="foreignkey")
    op.drop_column("services", "is_deleted")
    op.drop_column("services", "status")
    op.drop_column("services", "location_id")
    op.drop_column("services", "tenant_id")

    op.drop_index("ix_products_enterprise_location", table_name="products")
    op.drop_index("ix_products_tenant_enterprise", table_name="products")
    op.drop_index("ix_products_status", table_name="products")
    op.drop_index("ix_products_product_category", table_name="products")
    op.drop_index("ix_products_product_name", table_name="products")
    op.drop_index("ix_products_location_id", table_name="products")
    op.drop_index("ix_products_enterprise_id", table_name="products")
    op.drop_index("ix_products_tenant_id", table_name="products")
    op.drop_constraint("fk_products_location_id", "products", type_="foreignkey")
    op.drop_column("products", "is_deleted")
    op.drop_column("products", "status")
    op.drop_column("products", "location_id")
    op.drop_column("products", "tenant_id")

    op.drop_index("ix_enterprises_tenant_status", table_name="enterprises")
    op.drop_index("ix_enterprises_status", table_name="enterprises")
    op.drop_index("ix_enterprises_business_category", table_name="enterprises")
    op.drop_index("ix_enterprises_business_short_name", table_name="enterprises")
    op.drop_index("ix_enterprises_tenant_id", table_name="enterprises")

    op.add_column("enterprises", sa.Column("status_bool", sa.Boolean(), nullable=True))
    op.execute(
        "UPDATE enterprises SET status_bool = CASE "
        "WHEN status = 'active' THEN TRUE "
        "WHEN status = 'inactive' THEN FALSE "
        "ELSE TRUE END"
    )
    op.drop_column("enterprises", "status")
    op.alter_column("enterprises", "status_bool", new_column_name="status")

    op.drop_column("enterprises", "is_deleted")
    op.drop_column("enterprises", "website")
    op.drop_column("enterprises", "banner_url")
    op.drop_column("enterprises", "tenant_id")

    op.drop_index("ix_enterprise_locations_enterprise_status", table_name="enterprise_locations")
    op.drop_index("ix_enterprise_locations_status", table_name="enterprise_locations")
    op.drop_index("ix_enterprise_locations_city", table_name="enterprise_locations")
    op.drop_index("ix_enterprise_locations_enterprise_id", table_name="enterprise_locations")
    op.drop_table("enterprise_locations")
