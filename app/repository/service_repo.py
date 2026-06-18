from uuid import UUID

from sqlalchemy.orm import Session

from app.models.service_model import Service


def create_service(
    db: Session,
    service_data
):
    service = Service(
        **service_data.dict()
    )

    db.add(service)
    db.commit()
    db.refresh(service)

    return service


def get_services(db: Session):
    return db.query(Service).all()


def get_service_by_id(
    db: Session,
    service_id: UUID
):
    return (
        db.query(Service)
        .filter(Service.id == service_id)
        .first()
    )


def update_service(
    db: Session,
    service,
    update_data
):
    for key, value in (
        update_data.dict(
            exclude_unset=True
        ).items()
    ):
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