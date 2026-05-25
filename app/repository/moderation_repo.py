from math import ceil
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.moderation_model import ModerationLog, ReportedContent, ReportStatus


def create_moderation_log(db: Session, log: ModerationLog) -> ModerationLog:
    db.add(log)
    db.flush()
    return log


def list_reported_content(
    db: Session,
    report_status: str | None,
    page: int,
    size: int,
):
    query = db.query(ReportedContent).order_by(ReportedContent.created_at.desc())
    if report_status:
        query = query.filter(ReportedContent.report_status == report_status)
    total = query.count()
    skip = (page - 1) * size
    rows = query.offset(skip).limit(size).all()
    return total, rows, ceil(total / size) if size else 0


def get_report_by_id(db: Session, report_id: UUID) -> ReportedContent | None:
    return db.query(ReportedContent).filter(ReportedContent.id == report_id).first()
