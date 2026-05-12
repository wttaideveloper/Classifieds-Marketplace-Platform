from sqlalchemy import Column, String, Boolean, DateTime, Float, Text, Integer, ARRAY, ForeignKey
from datetime import datetime
from app.db.database import Base
import uuid
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

class Customer(Base):
    __tablename__ = "customers"

    id = Column(String, primary_key=True, index=True)
    firstName = Column(String)
    lastName = Column(String)
    email = Column(String, unique=True, index=True)
    mobileNumber = Column(String)
    password = Column(String)
    acceptTerms = Column(Boolean)
    acceptPrivacyPolicy = Column(Boolean)

    resetToken = Column(String, nullable=True)
    resetTokenExpiry = Column(DateTime, nullable=True)

    isEmailVerified = Column(Boolean, default=False)
    verificationToken = Column(String, nullable=True)
    status = Column(String, default="active")

    created_at = Column(DateTime, default=datetime.utcnow)
    