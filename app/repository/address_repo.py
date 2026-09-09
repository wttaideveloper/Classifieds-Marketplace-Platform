from uuid import UUID

from sqlalchemy.orm import Session

from app.models.address_model import UserAddress


def get_addresses(db: Session, user_id: UUID) -> list[UserAddress]:
    return (
        db.query(UserAddress)
        .filter(UserAddress.user_id == user_id, UserAddress.is_deleted.is_(False))
        .order_by(UserAddress.is_default.desc(), UserAddress.created_at.desc())
        .all()
    )


def create_address(db: Session, user_id: UUID, data: dict) -> UserAddress:
    if data.get("is_default"):
        db.query(UserAddress).filter(
            UserAddress.user_id == user_id,
            UserAddress.is_deleted.is_(False),
        ).update({"is_default": False})
    obj = UserAddress(user_id=user_id, **data)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
