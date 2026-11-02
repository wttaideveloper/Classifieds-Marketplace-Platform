"""Blog governance: which modules (events, trainings, products, services, general) have Blogs & News switched on.

Platform-wide, one setting per module; a module with no stored row is enabled. Switching a module off only stops new posts for it
(409 MODULE_DISABLED); existing posts, drafts, categories, forms and revisions are never touched, and published posts stay readable.
Each real change writes an audit row; a request that changes nothing writes nothing.
"""
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.blog_access import BlogActor
from app.exceptions.problem import problem
from app.models.blog_model import BlogGovernance, BlogGovernanceAudit
from app.repository.query_utils import build_pagination_meta

MODULES = ("events", "trainings", "products", "services", "general")


def check_module(value: str) -> str:
    module = (value or "").strip().lower()
    if module not in MODULES:
        raise problem(422, f"module must be one of: {', '.join(MODULES)}", "INVALID_MODULE", field="module", allowed=list(MODULES))
    return module


def _rows(db: Session, *, lock: bool = False) -> dict[str, BlogGovernance]:
    query = db.query(BlogGovernance)
    return {r.module: r for r in (query.with_for_update() if lock else query).all()}


def is_enabled(db: Session, module: str) -> bool:
    row = db.get(BlogGovernance, module)
    return True if row is None else bool(row.enabled)


def assert_enabled(db: Session, module: str) -> None:
    if not is_enabled(db, module):
        raise problem(409, f"Blogs & News is switched off for {module}, so new posts cannot be created for it", "MODULE_DISABLED", module=module)


def snapshot(db: Session) -> dict:
    rows = _rows(db)
    changed = [r for r in rows.values() if r.updated_at]
    latest = max(changed, key=lambda r: r.updated_at) if changed else None
    return {
        "modules": {m: (True if m not in rows else bool(rows[m].enabled)) for m in MODULES},
        "updated_at": latest.updated_at if latest else None,
        "updated_by": {"id": latest.updated_by, "name": latest.updated_by_name} if latest and latest.updated_by else None,
    }


def _set(db: Session, rows: dict, module: str, enabled: bool, actor: BlogActor, action: str) -> bool:
    row = rows.get(module)
    previous = True if row is None else bool(row.enabled)
    if previous == enabled:
        return False
    now = datetime.utcnow()
    if row is None:
        row = BlogGovernance(module=module, enabled=enabled)
        db.add(row)
        rows[module] = row
    row.enabled, row.updated_by, row.updated_by_name, row.updated_at = enabled, actor.id, actor.name, now
    db.add(BlogGovernanceAudit(module=module, previous_value=previous, new_value=enabled, action=action,
                               actor_id=actor.id, actor_name=actor.name, actor_role=actor.role, created_at=now))
    return True


def _save(db: Session) -> None:
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise problem(500, "The setting could not be saved. Nothing was changed. Try again", "SAVE_FAILED")


def update_module(db: Session, actor: BlogActor, module: str, enabled: bool) -> dict:
    module = check_module(module)
    try:
        _set(db, _rows(db, lock=True), module, enabled, actor, "enable" if enabled else "disable")
    except SQLAlchemyError:
        db.rollback()
        raise problem(500, "The setting could not be saved. Nothing was changed. Try again", "SAVE_FAILED")
    _save(db)
    return snapshot(db)


def reset(db: Session, actor: BlogActor) -> dict:
    try:
        rows = _rows(db, lock=True)
        for module in MODULES:
            _set(db, rows, module, True, actor, "reset")
    except SQLAlchemyError:
        db.rollback()
        raise problem(500, "The settings could not be saved. Nothing was changed. Try again", "SAVE_FAILED")
    _save(db)
    return snapshot(db)


def audit(db: Session, *, module: str | None, page: int, page_size: int) -> dict:
    query = db.query(BlogGovernanceAudit)
    if module:
        query = query.filter(BlogGovernanceAudit.module == check_module(module))
    total = query.count()
    rows = query.order_by(BlogGovernanceAudit.created_at.desc(), BlogGovernanceAudit.id).offset((page - 1) * page_size).limit(page_size).all()
    items = [{"id": r.id, "module": r.module, "previous_value": r.previous_value, "new_value": r.new_value, "action": r.action,
              "actor_id": r.actor_id, "actor_name": r.actor_name, "actor_role": r.actor_role, "created_at": r.created_at} for r in rows]
    return {"items": items, "pagination": build_pagination_meta(total, page, page_size)}
