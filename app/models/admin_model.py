# app/models/admin_model.py

from sqlalchemy import Column, Integer, String, DateTime
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

    createdAt = Column(DateTime, default=datetime.utcnow)