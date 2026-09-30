"""Training tags: persisted end-to-end on create/update, and searchable via
GET /api/v1/search/trainings (query matches tags in addition to the existing
title/description/category fields, case-insensitively, alongside pagination
and tenant/enterprise isolation)."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.enterprise_model import Enterprise
from app.models.training_form_config_model import (
    TrainingFormAssignment,
    TrainingFormAudit,
    TrainingFormConfiguration,
    TrainingFormConfigurationVersion,
)
from app.models.training_model import Training
from app.schemas.training_schema import TrainingCreate, TrainingUpdate
from app.services.search_service import search_trainings_service
from app.services.training_service import create_training_service, update_training_service


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        Enterprise.__table__, Training.__table__, TrainingFormConfiguration.__table__,
        TrainingFormConfigurationVersion.__table__, TrainingFormAssignment.__table__, TrainingFormAudit.__table__,
    ])
    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        yield session
    engine.dispose()


def _make_enterprise(db, tenant_id=None):
    unique = uuid4().hex[:8]
    ent = Enterprise(
        id=uuid4(), tenant_id=tenant_id or uuid4(), business_short_name="Acme Wellness",
        business_legal_name="Acme Wellness LLC", business_email=f"acme-{unique}@example.com", status="active",
    )
    db.add(ent)
    db.commit()
    return ent


def _make_training(db, *, enterprise_id, tenant_id, title, tags, status="published", category="Wellness"):
    t = Training(
        id=uuid4(), enterprise_id=enterprise_id, tenant_id=tenant_id, title=title,
        category=category, status=status, delivery_mode="online", tags=tags,
        sections=[], assessments=[], assignments=[],
    )
    db.add(t)
    db.commit()
    return t


# --- Persistence: tags survive create/update round-trips ---

def _admin(ent):
    return {"id": str(uuid4()), "role": "admin", "tenant_id": str(ent.tenant_id)}


def _activate_global_form_config(db):
    """create_training_service requires an active/published Training form
    configuration to resolve — set up a minimal global one, once per test."""
    from app.schemas.training_form_config_schema import TrainingFormConfigurationCreate
    from app.services import training_form_config_service as form_service
    from app.services.training_form_registry import build_default_sections

    super_admin = {"id": str(uuid4()), "role": "super_admin"}
    created = form_service.create_configuration_service(
        db, TrainingFormConfigurationCreate(name="Default", sections=build_default_sections()), super_admin,
    )
    form_service.publish_configuration_service(db, created["id"], super_admin)
    form_service.activate_configuration_service(db, created["id"], super_admin)


def test_tags_persist_on_create(db):
    _activate_global_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="Intro to Yoga", category="Wellness",
        tags=["yoga", "beginner", "morning routine"],
    ), current_user=_admin(ent))
    assert created.tags == ["yoga", "beginner", "morning routine"]

    reloaded = db.get(Training, created.id)
    assert reloaded.tags == ["yoga", "beginner", "morning routine"]


def test_tags_persist_and_returned_on_update(db):
    _activate_global_form_config(db)
    ent = _make_enterprise(db)
    admin = _admin(ent)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="Intro to Yoga", category="Wellness", tags=["yoga"],
    ), current_user=admin)
    updated = update_training_service(db, created.id, TrainingUpdate(tags=["yoga", "advanced", "flexibility"]), current_user=admin)
    assert updated.tags == ["yoga", "advanced", "flexibility"]

    reloaded = db.get(Training, created.id)
    assert reloaded.tags == ["yoga", "advanced", "flexibility"]


def test_empty_tags_persist_as_empty_list(db):
    _activate_global_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(enterprise_id=ent.id, title="No tags course", category="General"), current_user=_admin(ent))
    assert created.tags == []


# --- Search: query matches tags ---

def test_search_matches_tag_substring(db):
    ent = _make_enterprise(db)
    match = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Course A", tags=["yoga", "wellness"])
    no_match = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Course B", tags=["cooking"])

    result = search_trainings_service(db, query="yoga")
    ids = {i.id for i in result.items}
    assert match.id in ids
    assert no_match.id not in ids


def test_search_tag_match_is_case_insensitive(db):
    ent = _make_enterprise(db)
    match = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Course A", tags=["Yoga", "Wellness"])

    for term in ("yoga", "YOGA", "YoGa"):
        result = search_trainings_service(db, query=term)
        assert match.id in {i.id for i in result.items}, f"case variant '{term}' failed to match"


def test_search_matches_phrase_within_tag(db):
    ent = _make_enterprise(db)
    match = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Course A", tags=["morning routine", "wellness"])

    result = search_trainings_service(db, query="morning rou")
    assert match.id in {i.id for i in result.items}


def test_search_still_matches_title_description_category(db):
    """Existing search behavior (pre-tags) must be unaffected."""
    ent = _make_enterprise(db)
    by_title = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Advanced Python", tags=[])
    by_category = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Something Else", category="Cooking Basics", tags=[])

    title_hit = search_trainings_service(db, query="Python")
    assert by_title.id in {i.id for i in title_hit.items}

    category_hit = search_trainings_service(db, query="Cooking")
    assert by_category.id in {i.id for i in category_hit.items}


def test_search_matches_tag_or_title_together(db):
    """A query can hit either the tag or another field — both returned in one search."""
    ent = _make_enterprise(db)
    tag_hit = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Unrelated Title", tags=["wellness"])
    title_hit = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="Wellness Retreat", tags=[])

    result = search_trainings_service(db, query="wellness")
    ids = {i.id for i in result.items}
    assert tag_hit.id in ids
    assert title_hit.id in ids


def test_search_no_query_returns_all_unfiltered(db):
    ent = _make_enterprise(db)
    a = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="A", tags=["x"])
    b = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title="B", tags=["y"])

    result = search_trainings_service(db)
    ids = {i.id for i in result.items}
    assert {a.id, b.id} <= ids


# --- Pagination stays correct with tag matches ---

def test_search_pagination_with_tag_matches(db):
    ent = _make_enterprise(db)
    ids = []
    for i in range(5):
        t = _make_training(db, enterprise_id=ent.id, tenant_id=ent.tenant_id, title=f"Course {i}", tags=["shared-tag"])
        ids.append(t.id)

    page1 = search_trainings_service(db, query="shared-tag", page=1, page_size=2)
    assert len(page1.items) == 2
    assert page1.pagination.total == 5
    assert page1.pagination.total_pages == 3

    page2 = search_trainings_service(db, query="shared-tag", page=2, page_size=2)
    assert len(page2.items) == 2

    page3 = search_trainings_service(db, query="shared-tag", page=3, page_size=2)
    assert len(page3.items) == 1

    seen = {i.id for i in page1.items} | {i.id for i in page2.items} | {i.id for i in page3.items}
    assert seen == set(ids)


# --- Tenant/enterprise isolation ---

def test_search_tenant_filter_excludes_other_tenants_even_on_tag_match(db):
    tenant_a = uuid4()
    tenant_b = uuid4()
    ent_a = _make_enterprise(db, tenant_id=tenant_a)
    ent_b = _make_enterprise(db, tenant_id=tenant_b)
    mine = _make_training(db, enterprise_id=ent_a.id, tenant_id=tenant_a, title="Course A", tags=["shared-tag"])
    theirs = _make_training(db, enterprise_id=ent_b.id, tenant_id=tenant_b, title="Course B", tags=["shared-tag"])

    result = search_trainings_service(db, query="shared-tag", tenant_id=tenant_a)
    ids = {i.id for i in result.items}
    assert mine.id in ids
    assert theirs.id not in ids


def test_search_enterprise_filter_excludes_other_enterprises_even_on_tag_match(db):
    tenant = uuid4()
    ent_a = _make_enterprise(db, tenant_id=tenant)
    ent_b = _make_enterprise(db, tenant_id=tenant)
    mine = _make_training(db, enterprise_id=ent_a.id, tenant_id=tenant, title="Course A", tags=["shared-tag"])
    theirs = _make_training(db, enterprise_id=ent_b.id, tenant_id=tenant, title="Course B", tags=["shared-tag"])

    result = search_trainings_service(db, query="shared-tag", enterprise_id=ent_a.id)
    ids = {i.id for i in result.items}
    assert mine.id in ids
    assert theirs.id not in ids
