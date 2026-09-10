"""GET /api/v1/trainings/ was reported to return duplicate records.

Root cause: the list query ordered by `created_at` alone
(`order_by(Training.created_at.desc())`), with no secondary tiebreaker.
Rows that share the same created_at (bulk-seeded or rapidly created
back-to-back records are common) have no guaranteed stable order across two
separate paginated queries — the same row can land on two different pages
(a "duplicate" from the client's perspective once pages are concatenated),
or a row can be skipped entirely.
"""

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

# JSONB has no SQLite dialect support; Training's JSONB columns aren't
# exercised by this test, so rendering them as plain JSON is sufficient.
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"

from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.training_model import Training
from app.repository.training_repo import get_trainings


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Enterprise.__table__, Training.__table__])
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _seed_tied_trainings(db, count: int) -> list:
    tenant_id = uuid.uuid4()
    ent = Enterprise(
        id=uuid.uuid4(), tenant_id=tenant_id, business_short_name="Shop",
        business_legal_name="Shop LLC", business_email="shop@example.com", status="active",
    )
    db.add(ent)
    db.flush()

    same_ts = datetime(2026, 1, 1, 12, 0, 0)
    ids = []
    for i in range(count):
        tid = uuid.uuid4()
        ids.append(tid)
        db.add(Training(
            id=tid, tenant_id=tenant_id, enterprise_id=ent.id, title=f"Training {i}",
            category="General", status="published", created_at=same_ts,
        ))
    db.commit()
    return ids


def test_list_query_orders_by_id_as_a_tiebreaker():
    """Guards against the fix regressing: the ORDER BY must include a
    deterministic secondary key alongside created_at."""
    import inspect

    source = inspect.getsource(get_trainings)
    assert "Training.id" in source


def test_paginated_trainings_have_no_duplicates_when_created_at_ties(db):
    """Every record must appear on exactly one page — page_size=1 across
    every row is the strictest possible check for tie-related overlap."""
    ids = _seed_tied_trainings(db, count=6)

    seen: list = []
    for page in range(1, 7):
        items, total = get_trainings(db, page=page, page_size=1)
        assert total == 6
        seen.extend(item.id for item in items)

    assert len(seen) == 6
    assert len(set(seen)) == 6
    assert set(seen) == set(ids)


def test_paginated_trainings_stable_with_larger_page_size(db):
    _seed_tied_trainings(db, count=9)

    page1, _ = get_trainings(db, page=1, page_size=4)
    page2, _ = get_trainings(db, page=2, page_size=4)
    page3, _ = get_trainings(db, page=3, page_size=4)

    all_ids = [i.id for page in (page1, page2, page3) for i in page]
    assert len(all_ids) == 9
    assert len(set(all_ids)) == 9
