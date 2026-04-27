from sqlalchemy import Column, String, Boolean, ForeignKey
from app.db.database import Base

class Address(Base):
    __tablename__ = "addresses"

    id = Column(String, primary_key=True)
    customerId = Column(String, ForeignKey("customers.id"))

    addressLine1 = Column(String)
    addressLine2 = Column(String)
    city = Column(String)
    state = Column(String)
    zipCode = Column(String)
    country = Column(String)
    isDefault = Column(Boolean, default=False)