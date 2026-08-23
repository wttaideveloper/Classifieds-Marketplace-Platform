from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from app.models.training_model import Training
from app.repository.query_utils import apply_ilike_search, apply_soft_delete_filter, paginate_query


def create_training(db: Session, data):
    payload = data.to_model_data() if hasattr(data, "to_model_data") else data.model_dump()
    obj = Training(**payload)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def get_trainings(db: Session, *, search=None, category=None, tenant_id=None, enterprise_id=None, location_id=None, status=None, delivery_mode=None, page=1, page_size=20, include_deleted=False):
    q = db.query(Training).options(joinedload(Training.enterprise))
    q = apply_soft_delete_filter(q, Training, include_deleted)
    if tenant_id: q = q.filter(Training.tenant_id == tenant_id)
    if enterprise_id: q = q.filter(Training.enterprise_id == enterprise_id)
    if location_id: q = q.filter(Training.location_id == location_id)
    if category: q = q.filter(Training.category == category)
    if status: q = q.filter(Training.status == status)
    if delivery_mode: q = q.filter(Training.delivery_mode == delivery_mode)
    if search: q = apply_ilike_search(q, [Training.title, Training.description, Training.category], search)
    q = q.order_by(Training.created_at.desc())
    return paginate_query(q, page, page_size)


def get_training_by_id(db: Session, tid: UUID, include_deleted=False):
    q = db.query(Training).options(joinedload(Training.enterprise)).filter(Training.id == tid)
    if not include_deleted: q = apply_soft_delete_filter(q, Training, include_deleted)
    return q.first()


def update_training(db: Session, obj, data):
    payload = data.to_model_data() if hasattr(data, "to_model_data") else data.model_dump(exclude_unset=True)
    for k, v in payload.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj


def delete_training(db: Session, obj):
    obj.is_deleted = True; obj.status = "inactive"; db.commit(); db.refresh(obj); return obj
