from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.models.enterprise_model import Enterprise
from app.models.location_model import EnterpriseLocation
from app.repository.program_repo import create_program, delete_program, get_program_by_id, get_programs, update_program
from app.repository.query_utils import build_pagination_meta
from app.schemas.program_schema import ProgramDetailResponse, ProgramListItemResponse, ProgramPaginatedResponse, ProgramResponse
from app.services.response_mappers import map_program_detail, map_program_list_item, map_program_write

def _validate(db, eid, lid):
    ent=db.query(Enterprise).filter(Enterprise.id==eid, Enterprise.is_deleted.is_(False)).first()
    if not ent: raise HTTPException(404, "Enterprise not found")
    if lid:
        loc=db.query(EnterpriseLocation).filter(EnterpriseLocation.id==lid, EnterpriseLocation.enterprise_id==eid, EnterpriseLocation.is_deleted.is_(False)).first()
        if not loc: raise HTTPException(404, "Location not found")

def create_program_service(db, data):
    _validate(db, data.enterprise_id, data.location_id); return ProgramResponse.model_validate(map_program_write(create_program(db, data)))
def get_programs_service(db, **kw):
    items,total=get_programs(db, **kw); return ProgramPaginatedResponse(items=[ProgramListItemResponse.model_validate(map_program_list_item(i)) for i in items], pagination=build_pagination_meta(total, kw.get("page",1), kw.get("page_size",20)))
def get_program_service(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    return ProgramDetailResponse.model_validate(map_program_detail(obj))
def update_program_service(db, pid, data):
    obj=get_program_by_id(db, pid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(404, "Program not found")
    return ProgramResponse.model_validate(map_program_write(update_program(db, obj, data)))
def delete_program_service(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    return delete_program(db, obj)
def duplicate_program_service(db, pid):
    obj=get_program_by_id(db, pid)
    if not obj: raise HTTPException(404, "Program not found")
    payload={c.key: getattr(obj,c.key) for c in obj.__table__.columns if c.key not in ("id","created_at","updated_at")}
    payload["status"]="draft"; payload["is_deleted"]=False
    from app.models.program_model import Program
    clone=Program(**payload); db.add(clone); db.commit(); db.refresh(clone); return ProgramResponse.model_validate(map_program_write(clone))
def update_program_status_service(db, pid, st):
    obj=get_program_by_id(db, pid, include_deleted=True)
    if not obj or obj.is_deleted: raise HTTPException(404, "Program not found")
    obj.status=st; db.commit(); db.refresh(obj); return ProgramResponse.model_validate(map_program_write(obj))
