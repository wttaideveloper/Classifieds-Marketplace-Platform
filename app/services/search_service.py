from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.enterprise_model import Enterprise
from app.models.event_model import Event
from app.models.location_model import EnterpriseLocation
from app.models.product_model import Product
from app.models.service_model import Service
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
    build_pagination_meta,
    paginate_query,
)
from app.schemas.enterprise_schema import EnterpriseListItemResponse, EnterprisePaginatedResponse
from app.schemas.event_schema import EventListItemResponse, EventPaginatedResponse
from app.schemas.product_schema import ProductListItemResponse, ProductPaginatedResponse
from app.schemas.service_schema import ServiceListItemResponse, ServicePaginatedResponse
from app.services.response_mappers import (
    map_enterprise_list_item,
    map_event_list_item,
    map_product_list_item,
    map_service_list_item,
)


def search_enterprises_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> EnterprisePaginatedResponse:
    db_query = db.query(Enterprise).distinct()
    db_query = apply_soft_delete_filter(db_query, Enterprise, False)

    if enterprise_id:
        db_query = db_query.filter(Enterprise.id == enterprise_id)
    if tenant_id:
        db_query = db_query.filter(Enterprise.tenant_id == tenant_id)
    if category:
        db_query = db_query.filter(Enterprise.business_category == category)
    if status_filter:
        db_query = db_query.filter(Enterprise.status == status_filter)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            (EnterpriseLocation.enterprise_id == Enterprise.id)
            & (EnterpriseLocation.is_deleted.is_(False)),
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [
                Enterprise.business_short_name,
                Enterprise.business_legal_name,
                Enterprise.business_description,
                Enterprise.business_email,
                Enterprise.business_category,
                Enterprise.tagline,
            ],
            query,
        )

    db_query = db_query.order_by(Enterprise.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return EnterprisePaginatedResponse(
        items=[
            EnterpriseListItemResponse.model_validate(map_enterprise_list_item(item))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def search_products_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ProductPaginatedResponse:
    db_query = (
        db.query(Product)
        .options(joinedload(Product.enterprise))
    )
    db_query = apply_soft_delete_filter(db_query, Product, False)

    if tenant_id:
        db_query = db_query.filter(Product.tenant_id == tenant_id)
    if enterprise_id:
        db_query = db_query.filter(Product.enterprise_id == enterprise_id)
    if category:
        db_query = db_query.filter(Product.product_category == category)
    if status_filter:
        db_query = db_query.filter(Product.status == status_filter)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            Product.location_id == EnterpriseLocation.id,
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [
                Product.product_name,
                Product.product_description,
                Product.product_category,
                Product.sku,
            ],
            query,
        )

    db_query = db_query.order_by(Product.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return ProductPaginatedResponse(
        items=[
            ProductListItemResponse.model_validate(map_product_list_item(item))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def search_services_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> ServicePaginatedResponse:
    db_query = (
        db.query(Service)
        .options(joinedload(Service.enterprise))
    )
    db_query = apply_soft_delete_filter(db_query, Service, False)

    if tenant_id:
        db_query = db_query.filter(Service.tenant_id == tenant_id)
    if enterprise_id:
        db_query = db_query.filter(Service.enterprise_id == enterprise_id)
    if category:
        db_query = db_query.filter(Service.service_category == category)
    if status_filter:
        db_query = db_query.filter(Service.status == status_filter)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            Service.location_id == EnterpriseLocation.id,
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [
                Service.service_name,
                Service.service_description,
                Service.service_category,
                Service.provider_name,
                Service.instructor_name,
            ],
            query,
        )

    db_query = db_query.order_by(Service.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return ServicePaginatedResponse(
        items=[
            ServiceListItemResponse.model_validate(map_service_list_item(item))
            for item in items
        ],
        pagination=build_pagination_meta(total, page, page_size),
    )


def search_events_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    delivery_mode: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> EventPaginatedResponse:
    db_query = db.query(Event).options(joinedload(Event.enterprise))
    db_query = apply_soft_delete_filter(db_query, Event, False)

    if tenant_id:
        db_query = db_query.filter(Event.tenant_id == tenant_id)
    if enterprise_id:
        db_query = db_query.filter(Event.enterprise_id == enterprise_id)
    if category:
        db_query = db_query.filter(Event.category == category)
    if status_filter:
        db_query = db_query.filter(Event.status == status_filter)
    if delivery_mode:
        db_query = db_query.filter(Event.delivery_mode == delivery_mode)
    if city:
        db_query = db_query.outerjoin(
            EnterpriseLocation,
            Event.location_id == EnterpriseLocation.id,
        ).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(
            db_query,
            [Event.title, Event.description, Event.category, Event.subcategory],
            query,
        )

    db_query = db_query.order_by(Event.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return EventPaginatedResponse(
        items=[EventListItemResponse.model_validate(map_event_list_item(item)) for item in items],
        pagination=build_pagination_meta(total, page, page_size),
    )


def search_programs_service(db: Session, *, query=None, tenant_id=None, enterprise_id=None, category=None, city=None, status_filter=None, page=1, page_size=20):
    from app.models.program_model import Program
    from app.schemas.program_schema import ProgramListItemResponse, ProgramPaginatedResponse
    from app.services.response_mappers import map_program_list_item
    db_query=db.query(Program).options(joinedload(Program.enterprise))
    db_query=apply_soft_delete_filter(db_query, Program, False)
    if tenant_id: db_query=db_query.filter(Program.tenant_id==tenant_id)
    if enterprise_id: db_query=db_query.filter(Program.enterprise_id==enterprise_id)
    if category: db_query=db_query.filter(Program.category==category)
    if status_filter: db_query=db_query.filter(Program.status==status_filter)
    if city: db_query=db_query.outerjoin(EnterpriseLocation, Program.location_id==EnterpriseLocation.id).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query: db_query=apply_ilike_search(db_query, [Program.title, Program.description, Program.category], query)
    db_query=db_query.order_by(Program.created_at.desc())
    items,total=paginate_query(db_query, page, page_size)
    return ProgramPaginatedResponse(items=[ProgramListItemResponse.model_validate(map_program_list_item(i)) for i in items], pagination=build_pagination_meta(total, page, page_size))

def search_trainings_service(
    db: Session,
    *,
    query: str | None = None,
    tenant_id: UUID | None = None,
    enterprise_id: UUID | None = None,
    category: str | None = None,
    city: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> "TrainingPaginatedResponse":
    from app.models.training_model import Training
    from app.schemas.training_schema import TrainingListItemResponse, TrainingPaginatedResponse
    from app.services.response_mappers import map_training_list_item

    db_query = db.query(Training).options(joinedload(Training.enterprise))
    db_query = apply_soft_delete_filter(db_query, Training, False)
    if tenant_id: db_query = db_query.filter(Training.tenant_id == tenant_id)
    if enterprise_id: db_query = db_query.filter(Training.enterprise_id == enterprise_id)
    if category: db_query = db_query.filter(Training.category == category)
    if status_filter: db_query = db_query.filter(Training.status == status_filter)
    if city:
        db_query = db_query.outerjoin(EnterpriseLocation, Training.location_id == EnterpriseLocation.id).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        db_query = apply_ilike_search(db_query, [Training.title, Training.description, Training.category], query)
    db_query = db_query.order_by(Training.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return TrainingPaginatedResponse(items=[TrainingListItemResponse.model_validate(map_training_list_item(i)) for i in items], pagination=build_pagination_meta(total, page, page_size))
