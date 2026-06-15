from uuid import UUID
from sqlalchemy.orm import Session
from app.models.communication_model import Communication

def create_communication(
        db: Session,
        payload
):
    communication = Communication(**payload.dict())

    db.add(communication)
    db.commit()
    db.refresh(communication)

    return communication

def get_all_communications(db: Session):
    return db.query(Communication).all()

def get_communication_by_id(
        db: Session,
        communication_id: UUID
):
    return (
        db.query(Communication)
        .filter(
            Communication.id == communication_id
        )
        .first()
    )

def update_communication(
        db: Session,
        communication
):
    db.commit()
    db.refresh(communication)

    return communication

def delete_communication(
        db: Session,
        communication
):
    db.delete(communication)
    db.commit()