import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class DynamicAttribute(Base):
    __tablename__ = "dynamic_attributes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    tenant_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    entity_type = Column(
        String(50),
        nullable=False,
        index=True,
    )

    entity_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )

    attribute_name = Column(
        String(255),
        nullable=False,
    )

    attribute_value = Column(
        Text,
        nullable=False,
    )

    attribute_type = Column(
        String(50),
        nullable=False,
    )

    is_required = Column(Boolean, default=False, nullable=False)

    status = Column(String(20), default="active", nullable=False, index=True)

    is_deleted = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index(
            "ix_dynamic_attributes_entity",
            "entity_type",
            "entity_id",
            "status",
        ),
    )
