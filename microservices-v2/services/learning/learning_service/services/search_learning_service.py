"""GET /api/v1/search/trainings and /search/programs, served by learning (it owns the data). Copied from
app/services/search_service.py."""
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from learning_service.models.location_model import EnterpriseLocation  # read-only replica
from learning_service.repository.query_utils import apply_ilike_search, apply_soft_delete_filter, build_pagination_meta, paginate_query


def search_programs_service(db: Session, *, query=None, tenant_id=None, enterprise_id=None, category=None, city=None, status_filter=None, page=1, page_size=20):
    from learning_service.models.program_model import Program
    from learning_service.schemas.program_schema import ProgramListItemResponse, ProgramPaginatedResponse
    from learning_service.services.response_mappers import map_program_list_item
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
    from learning_service.models.training_model import Training
    from learning_service.schemas.training_schema import TrainingListItemResponse, TrainingPaginatedResponse
    from learning_service.services.response_mappers import map_training_list_item

    db_query = db.query(Training).options(joinedload(Training.enterprise))
    db_query = apply_soft_delete_filter(db_query, Training, False)
    if tenant_id: db_query = db_query.filter(Training.tenant_id == tenant_id)
    if enterprise_id: db_query = db_query.filter(Training.enterprise_id == enterprise_id)
    if category: db_query = db_query.filter(Training.category == category)
    if status_filter: db_query = db_query.filter(Training.status == status_filter)
    if city:
        db_query = db_query.outerjoin(EnterpriseLocation, Training.location_id == EnterpriseLocation.id).filter(EnterpriseLocation.city.ilike(f"%{city.strip()}%"))
    if query:
        from sqlalchemy import String, cast
        # Training.tags is a JSONB string array (["yoga", "wellness"]). Casting
        # to text and reusing the same ILIKE substring match as the other
        # fields is dialect-portable (Postgres in prod, SQLite in tests) and
        # correctly satisfies "word/phrase contained in a tag" — any substring
        # of a tag's characters is also a substring of the serialized array.
        db_query = apply_ilike_search(
            db_query,
            [Training.title, Training.description, Training.category, cast(Training.tags, String)],
            query,
        )
    db_query = db_query.order_by(Training.created_at.desc())
    items, total = paginate_query(db_query, page, page_size)
    return TrainingPaginatedResponse(items=[TrainingListItemResponse.model_validate(map_training_list_item(i)) for i in items], pagination=build_pagination_meta(total, page, page_size))

