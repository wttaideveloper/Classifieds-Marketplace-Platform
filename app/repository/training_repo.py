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


def get_trainings(db: Session, *, search=None, category=None, tenant_id=None, enterprise_id=None, location_id=None, status=None, delivery_mode=None, provider_id=None, min_price=None, max_price=None, duration=None, date_from=None, date_to=None, page=1, page_size=20, include_deleted=False):
    q = db.query(Training).options(joinedload(Training.enterprise)).distinct()
    q = apply_soft_delete_filter(q, Training, include_deleted)
    if tenant_id: q = q.filter(Training.tenant_id == tenant_id)
    if enterprise_id: q = q.filter(Training.enterprise_id == enterprise_id)
    if location_id: q = q.filter(Training.location_id == location_id)
    if category: q = q.filter(Training.category == category)
    if status: q = q.filter(Training.status == status)
    if delivery_mode: q = q.filter(Training.delivery_mode == delivery_mode)
    if provider_id: q = q.filter(Training.instructor_id == provider_id)
    if min_price is not None:
        try:
            from sqlalchemy import cast, Float
            q = q.filter(cast(Training.price, Float) >= float(min_price))
        except: pass
    if max_price is not None:
        try:
            from sqlalchemy import cast, Float
            q = q.filter(cast(Training.price, Float) <= float(max_price))
        except: pass
    if duration: q = q.filter(Training.course_type == str(duration))
    if date_from:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(date_from).replace('Z',''))
            q = q.filter(Training.start_date >= dt)
        except: pass
    if date_to:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(str(date_to).replace('Z',''))
            q = q.filter(Training.end_date <= dt)
        except: pass
    if search: q = apply_ilike_search(q, [Training.title, Training.description, Training.category], search)
    # A secondary tiebreaker is required: rows sharing the same created_at
    # (bulk-seeded/rapidly-created records are common) have no guaranteed
    # stable order across separate paginated queries without one — Postgres
    # can return the same row on two different pages, or skip one, when the
    # sort key alone doesn't uniquely order the result set.
    q = q.order_by(Training.created_at.desc(), Training.id.desc())
    return paginate_query(q, page, page_size)


def get_training_by_id(db: Session, tid: UUID, include_deleted=False):
    q = db.query(Training).options(joinedload(Training.enterprise)).filter(Training.id == tid)
    if not include_deleted: q = apply_soft_delete_filter(q, Training, include_deleted)
    return q.first()


def update_training(db: Session, obj, data):
    payload = data.to_model_data() if hasattr(data, "to_model_data") else data.model_dump(exclude_unset=True)
    from app.services.training_curriculum import normalize_authoring
    payload = normalize_authoring(payload, obj)
    for k, v in payload.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj


def delete_training(db: Session, obj):
    obj.is_deleted = True; obj.status = "archived"; db.commit(); db.refresh(obj); return obj


def require_training_owner(db: Session, tid: UUID, current_user: dict, *, access_token=None, include_deleted=False):
    """Management lookup; public/learner lookups intentionally remain unscoped."""
    from fastapi import HTTPException
    from app.core.auth_context import resolve_auth_tenant_id_with_db
    from app.services.super_admin_identity import profile_status_is_active

    training = get_training_by_id(db, tid, include_deleted=include_deleted)
    if training is None:
        raise HTTPException(404, "Training not found")
    if current_user.get("role") == "super_admin" and profile_status_is_active(current_user):
        return training
    if current_user.get("role") not in ("admin", "provider"):
        raise HTTPException(403, "Not authorized")
    tenant_id = resolve_auth_tenant_id_with_db(db, current_user, access_token=access_token)
    # Legacy trainings may predate the denormalized Training.tenant_id column.
    owner = (training.enterprise.tenant_id if training.enterprise else None) or training.tenant_id
    if not tenant_id or not owner or str(tenant_id) != str(owner):
        raise HTTPException(403, "Not authorized for this tenant")
    if training.tenant_id and str(training.tenant_id) != str(tenant_id):
        raise HTTPException(403, "Not authorized for this tenant")
    return training
