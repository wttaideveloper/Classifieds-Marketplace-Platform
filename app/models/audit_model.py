import uuid
from sqlalchemy import Column, String, DateTime, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from enum import Enum
from app.db.database import Base

class UserRoleEnum(str, Enum):
    ADMIN = "admin"
    MERCHANT = "merchant"
    CUSTOMER = "customer"


class ActionStatusEnum(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"

class LoginTypeEnum(str, Enum):
    LOGIN = "login"
    LOGOUT = "logout"
    FAILED_LOGIN = "failed_login"

class LoginStatusEnum(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    user_id = Column(UUID(as_uuid=True), nullable=False)

    user_role = Column(
        Enum(UserRoleEnum, name="user_role_enum"),
        nullable=False
    )

    action_type = Column(String(100), nullable=False)

    module_name = Column(String(100), nullable=False)

    entity_type = Column(String(100), nullable=False)

    entity_id = Column(UUID(as_uuid=True), nullable=False)

    old_value = Column(JSONB, nullable=True)

    new_value = Column(JSONB, nullable=True)

    ip_address = Column(String(100), nullable=True)

    device_info = Column(String(500), nullable=True)

    action_status = Column(
        Enum(ActionStatusEnum, name="action_status_enum"),
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

class AuthenticationLog(Base):
    __tablename__ = "authentication_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    user_id = Column(
        UUID(as_uuid=True),
        nullable=False
    )

    login_type = Column(
        SQLEnum(LoginTypeEnum),
        nullable=False
    )

    ip_address = Column(
        String(100),
        nullable=True
    )

    device_info = Column(
        String(500),
        nullable=True
    )

    login_status = Column(
        SQLEnum(LoginStatusEnum),
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )