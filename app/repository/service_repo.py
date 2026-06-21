from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.service_model import Service


def create_service(
    db: Session,
    service_data
):
    payload = service_data.model_dump()
    if payload.get("availability_schedule") is not None:
        payload["availability_schedule"] = [
            entry if isinstance(entry, dict) else entry
            for entry in payload["availability_schedule"]
        ]

    service = Service(**payload)

    db.add(service)
    db.commit()
    db.refresh(service)

    return service


def get_services(db: Session):
    return (
        db.query(Service)
        .options(joinedload(Service.enterprise))
        .all()
    )


def get_service_by_id(
    db: Session,
    service_id: UUID
):
    return (
        db.query(Service)
        .options(joinedload(Service.enterprise))
        .filter(Service.id == service_id)
        .first()
    )


def update_service(
    db: Session,
    service,
    update_data
):
    for key, value in update_data.model_dump(
            exclude_unset=True
    ).items():
        if key == "availability_schedule" and value is not None:
            value = [
                entry if isinstance(entry, dict) else entry
                for entry in value
            ]
        setattr(service, key, value)

    db.commit()
    db.refresh(service)

    return service


def delete_service(
    db: Session,
    service
):
    service.service_status = False

    db.commit()
    db.refresh(service)

    return service