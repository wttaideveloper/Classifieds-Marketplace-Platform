from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.training_repo import create_training, delete_training, get_training_by_id, get_trainings, update_training
from app.repository.query_utils import build_pagination_meta
from app.schemas.training_schema import TrainingDetailResponse, TrainingListItemResponse, TrainingPaginatedResponse, TrainingResponse
from app.services.response_mappers import map_training_detail, map_training_list_item, map_training_write

def _validate(db: Session, eid: UUID, lid: UUID | None):
    ent = db.query(Enterprise).filter(Enterprise.id==eid, Enterprise.is_deleted.is_(False)).first()
    if not ent: raise HTTPException(status_code=404, detail="Enterprise not found")
    if lid:
        loc = db.query(EnterpriseLocation).filter(EnterpriseLocation.id==lid, EnterpriseLocation.enterprise_id==eid, EnterpriseLocation.is_deleted.is_(False)).first()
        if not loc: raise HTTPException(status_code=404, detail="Location not found for this enterprise")

def create_training_service(db: Session, data):
    _validate(db, data.enterprise_id, data.location_id)
    return TrainingResponse.model_validate(map_training_write(create_training(db, data)))

def get_trainings_service(db: Session, **kw):
    items, total = get_trainings(db, **kw)
    return TrainingPaginatedResponse(items=[TrainingListItemResponse.model_validate(map_training_list_item(i)) for i in items], pagination=build_pagination_meta(total, kw.get("page",1), kw.get("page_size",20)))

def get_training_service(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    return TrainingDetailResponse.model_validate(map_training_detail(obj))

def update_training_service(db: Session, tid: UUID, data):
    obj = get_training_by_id(db, tid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail="Training not found")
    lid = data.location_id if getattr(data,"location_id",None) is not None else obj.location_id
    _validate(db, obj.enterprise_id, lid)
    return TrainingResponse.model_validate(map_training_write(update_training(db, obj, data)))

def delete_training_service(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    return delete_training(db, obj)

def duplicate_training_service(db: Session, tid: UUID):
    obj = get_training_by_id(db, tid)
    if not obj: raise HTTPException(status_code=404, detail="Training not found")
    payload = {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key not in ("id","created_at","updated_at")}
    payload["status"]="draft"; payload["is_deleted"]=False
    from app.models.training_model import Training
    clone=Training(**payload); db.add(clone); db.commit(); db.refresh(clone)
    return TrainingResponse.model_validate(map_training_write(clone))

def update_training_status_service(db: Session, tid: UUID, st: str):
    obj = get_training_by_id(db, tid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(status_code=404, detail="Training not found")
    obj.status=st; db.commit(); db.refresh(obj); return TrainingResponse.model_validate(map_training_write(obj))
