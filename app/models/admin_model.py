# app/models/admin_model.py
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.models.merchant_model import Merchant
from app.models.customer_model import Category
import uuid
from datetime import datetime
from app.db.database import Base


class Admin(Base):
    __tablename__ = "admins"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String, nullable=True)   # NEW
    email = Column(String, unique=True, nullable=False)
    password = Column(String, nullable=False)

    resetToken = Column(String, nullable=True)
    resetTokenExpiry = Column(DateTime, nullable=True)

    isEmailVerified = Column(Boolean, default=False)
    verificationToken = Column(String, nullable=True)
    status = Column(String, default="active")

    createdAt = Column(DateTime, default=datetime.utcnow)

class Business(Base):
    __tablename__ = "businesses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    name = Column(String, nullable=False)
    category = Column(String, nullable=True)

    status = Column(String, default="pending", index=True)

    is_deleted = Column(Boolean, default=False)

    approved_at = Column(DateTime, nullable=True)
    rejection_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime, nullable=True)
    suspension_reason = Column(Text, nullable=True)
    suspended_at = Column(DateTime, nullable=True)

    merchant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("merchants.id"),
        nullable=False,
        index=True
    )

    merchant = relationship("Merchant", back_populates="businesses")

    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<Business {self.name} ({self.status})>"

