"""The finance audit trail: who changed a tax rule, an exchange rate, a settlement setting or decided a refund, and when."""
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.payment_model import FinanceAuditLog


def jsonable(value: Any) -> Any:
    """Plain JSON from rows / dicts holding Decimals, UUIDs and dates, so before / after snapshots can be stored."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def snapshot(row, *fields: str) -> dict:
    return {f: jsonable(getattr(row, f, None)) for f in fields}


def record(db: Session, actor, action: str, entity_type: str, entity_id: Any, *, before: dict | None = None,
           after: dict | None = None, tenant_id: UUID | None = None, reason: str | None = None) -> FinanceAuditLog:
    """Adds the entry to the session; the caller's commit saves it together with the change itself."""
    row = FinanceAuditLog(
        actor_id=getattr(actor, "id", None), actor_name=getattr(actor, "name", None), actor_role=getattr(actor, "role", None) or "system",
        action=action, entity_type=entity_type, entity_id=str(entity_id) if entity_id is not None else None,
        tenant_id=tenant_id, before=jsonable(before) if before is not None else None,
        after=jsonable(after) if after is not None else None, reason=(reason or None) and reason[:500],
    )
    db.add(row)
    return row
