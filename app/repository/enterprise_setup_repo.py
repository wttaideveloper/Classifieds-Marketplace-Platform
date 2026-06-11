from sqlalchemy.orm import Session
from uuid import UUID
from app.models.enterprise_setup_model import EnterpriseSetup

def create_enterprise_repo(db: Session, data):
    enterprise = EnterpriseSetup(**data.dict())

    db.add(enterprise)
    db.commit()
    db.refresh(enterprise)
    return enterprise

def get_all_enterprises_repo(db: Session):
    return db.query(EnterpriseSetup).all()

def get_enterprise_by_id_repo(
    db: Session,
    enterprise_id: UUID
):
    return (
        db.query(EnterpriseSetup)
        .filter(
            EnterpriseSetup.enterprise_id == enterprise_id
        )
        .first()
    )

def update_enterprise_repo(
    db: Session,
    enterprise,
    update_data
):
    for key, value in update_data.dict(
        exclude_unset=True
    ).items():
        setattr(enterprise, key, value)

    db.commit()
    db.refresh(enterprise)
    return enterprise

def delete_enterprise_repo(
    db: Session,
    enterprise
):
    db.delete(enterprise)
    db.commit()