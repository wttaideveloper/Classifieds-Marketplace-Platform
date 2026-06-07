from sqlalchemy import Column, String, Boolean, ForeignKey
from app.db.database import Base
from sqlalchemy.dialects.postgresql import UUID
import uuid

class Address(Base):
    __tablename__ = "addresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("customers.id"))

    address_line_1 = Column(String)
    address_line_2 = Column(String)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    country = Column(String)
    is_default = Column(Boolean, default=False)