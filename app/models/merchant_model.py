from sqlalchemy import Column, String, Boolean, DateTime
from datetime import datetime
from app.db.database import Base

class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(String, primary_key=True, index=True)
    fullName = Column(String)
    businessEmail = Column(String, unique=True, index=True)
    mobileNumber = Column(String)
    businessName = Column(String)
    password = Column(String)
    acceptTerms = Column(Boolean)
    acceptPrivacyPolicy = Column(Boolean)

    resetToken = Column(String, nullable=True)
    resetTokenExpiry = Column(DateTime, nullable=True)

    isEmailVerified = Column(Boolean, default=False)
    verificationToken = Column(String, nullable=True)

    createdAt = Column(DateTime, default=datetime.utcnow)