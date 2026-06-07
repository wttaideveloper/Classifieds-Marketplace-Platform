# app/models/category_model.py
from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
from datetime import datetime
from app.db.database import Base


# CATEGORY
class Category(Base):

    __tablename__ = "categories"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )
    name = Column(
        String,
        nullable=False,
        unique=True
    )
    description = Column(
        String,
        nullable=True
    )
    icon = Column(
        String,
        nullable=True
    )
<<<<<<< HEAD
    parent_category_id = Column(
=======
    parentCategoryId = Column(
        "parent_category_id",
>>>>>>> 04dd8a47f2053fa5dc20b999a276ab54d16b01db
        UUID(as_uuid=True),
        ForeignKey("categories.id"),
        nullable=True
    )
<<<<<<< HEAD
    is_active = Column(
        Boolean,
        default=True
    )
    is_deleted = Column(
=======
    isActive = Column(
        "is_active",
        Boolean,
        default=True
    )
    isDeleted = Column(
        "is_deleted",
>>>>>>> 04dd8a47f2053fa5dc20b999a276ab54d16b01db
        Boolean,
        default=False
    )
    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
    parent = relationship(
        "Category",
        remote_side=[id]
    )
