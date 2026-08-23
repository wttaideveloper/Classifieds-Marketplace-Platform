from uuid import UUID
from sqlalchemy.orm import Session, joinedload
from app.models.program_model import Program
from app.repository.query_utils import apply_ilike_search, apply_soft_delete_filter, paginate_query

def create_program(db: Session, data):
    payload = data.to_model_data() if hasattr(data, "to_model_data") else data.model_dump()
    obj = Program(**payload); db.add(obj); db.commit(); db.refresh(obj); return obj

def get_programs(db: Session, *, search=None, category=None, tenant_id=None, enterprise_id=None, location_id=None, status=None, delivery_mode=None, page=1, page_size=20, include_deleted=False):
    q = db.query(Program).options(joinedload(Program.enterprise))
    q = apply_soft_delete_filter(q, Program, include_deleted)
    if tenant_id: q = q.filter(Program.tenant_id == tenant_id)
    if enterprise_id: q = q.filter(Program.enterprise_id == enterprise_id)
    if location_id: q = q.filter(Program.location_id == location_id)
    if category: q = q.filter(Program.category == category)
    if status: q = q.filter(Program.status == status)
    if delivery_mode: q = q.filter(Program.delivery_mode == delivery_mode)
    if search: q = apply_ilike_search(q, [Program.title, Program.description, Program.category], search)
    q = q.order_by(Program.created_at.desc())
    return paginate_query(q, page, page_size)

def get_program_by_id(db: Session, pid: UUID, include_deleted=False):
    q = db.query(Program).options(joinedload(Program.enterprise)).filter(Program.id == pid)
    if not include_deleted: q = apply_soft_delete_filter(q, Program, include_deleted)
    return q.first()

def update_program(db: Session, obj, data):
    payload = data.to_model_data() if hasattr(data, "to_model_data") else data.model_dump(exclude_unset=True)
    for k, v in payload.items(): setattr(obj, k, v)
    db.commit(); db.refresh(obj); return obj

def delete_program(db: Session, obj):
    obj.is_deleted = True; obj.status = "inactive"; db.commit(); db.refresh(obj); return obj
