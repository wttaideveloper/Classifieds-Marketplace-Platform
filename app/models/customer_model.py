from sqlalchemy import Column, String, Boolean, DateTime
from datetime import datetime
from app.db.database import Base

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

    createdAt = Column(DateTime, default=datetime.utcnow)