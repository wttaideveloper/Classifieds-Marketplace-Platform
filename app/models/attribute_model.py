import uuid

from sqlalchemy import (
    Column,
    String,
    Text
)

from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class DynamicAttribute(Base):
    __tablename__ = "dynamic_attributes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    entity_type = Column(
        String(50),
        nullable=False
    )

    entity_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    attribute_name = Column(
        String(255),
        nullable=False
    )

    attribute_value = Column(
        Text,
        nullable=False
    )

    attribute_type = Column(
        String(50),
        nullable=False
    )