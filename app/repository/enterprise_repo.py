from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enterprise_model import Enterprise


def create_enterprise(
        db: Session,
        enterprise_data
):
    enterprise = Enterprise(**enterprise_data.dict())

    db.add(enterprise)
    db.commit()
    db.refresh(enterprise)

    return enterprise


def get_enterprises(db: Session):
    return db.query(Enterprise).all()


def get_enterprise_by_id(
        db: Session,
        enterprise_id: UUID
):
    return (
        db.query(Enterprise)
        .filter(Enterprise.id == enterprise_id)
        .first()
    )


def update_enterprise(
        db: Session,
        enterprise,
        update_data
):
    for key, value in update_data.dict(
            exclude_unset=True
    ).items():
        setattr(
            enterprise,
            key,
            value
        )

    db.commit()
    db.refresh(enterprise)

    return enterprise


def delete_enterprise(
        db: Session,
        enterprise
):
    enterprise.status = False

    db.commit()
    db.refresh(enterprise)

    return enterprise