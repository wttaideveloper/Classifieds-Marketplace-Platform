import uuid

from sqlalchemy import Column
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import Boolean

from sqlalchemy.dialects.postgresql import UUID

from app.db.database import Base


class Enterprise(Base):
    __tablename__ = "enterprises"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    business_short_name = Column(String(100), nullable=False)

    business_legal_name = Column(String(255), nullable=False)

    business_description = Column(Text)

    business_email = Column(
        String(255),
        unique=True,
        nullable=False
    )

    business_phone = Column(String(30))

    registered_address = Column(Text)

    business_address = Column(Text)

    communication_address = Column(Text)

    logo_url = Column(Text)

    business_images = Column(Text)

    status = Column(
        Boolean,
        default=True
    )