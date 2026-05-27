from sqlalchemy import Column, String, Boolean, ForeignKey
from app.db.database import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid

class Address(Base):
    __tablename__ = "addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    customerId = Column("customer_id", UUID(as_uuid=True), ForeignKey("customers.id"))

    addressLine1 = Column("address_line1", String)
    addressLine2 = Column("address_line2", String)
    city = Column(String)
    state = Column(String)
    zipCode = Column("zip_code", String)
    country = Column(String)
    isDefault = Column("is_default", Boolean, default=False)
