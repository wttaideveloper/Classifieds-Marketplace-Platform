"""End-to-end (create_training_service/search/summary) coverage for the
Training "Other (custom value)" contract — complements the unit-level checks
in test_training_category_subcategory_linkage.py by exercising the real
create/search/summary pipeline with a form config that mirrors what a Super
Admin would actually configure (select renderer + allow_custom_value +
parent_value-linked subcategory options)."""
from uuid import uuid4

import pytest
from pydantic import ValidationError
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
from app.models.training_model import Training, TrainingEnrolment, TrainingReview
from app.schemas.training_form_config_schema import TrainingFormConfigurationCreate
from app.schemas.training_schema import TrainingCreate, TrainingUpdate
from app.services import training_form_config_service as form_service
from app.services.search_service import search_trainings_service
from app.services.training_form_registry import build_default_sections
from app.services.training_form_rules import OTHER_OPTION_SENTINEL
from app.services.training_service import create_training_service, get_training_summary_service, update_training_service


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        Enterprise.__table__, Training.__table__, TrainingEnrolment.__table__, TrainingReview.__table__,
        TrainingFormConfiguration.__table__, TrainingFormConfigurationVersion.__table__,
        TrainingFormAssignment.__table__, TrainingFormAudit.__table__,
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


def _admin(ent):
    return {"id": str(uuid4()), "role": "admin", "tenant_id": str(ent.tenant_id)}


def _activate_custom_category_form_config(db):
    """A Super Admin-configured taxonomy with select category/subcategory,
    parent_value linkage, and allow_custom_value on both — the exact shape
    this ticket is about, not the plain-text default."""
    sections = build_default_sections()
    basic_fields = sections[0]["fields"]
    for field in basic_fields:
        if field.get("core_key") == "category":
            field["renderer"] = "select"
            field["required"] = True
            field["options"] = [
                {"value": "wellness", "label": "Wellness", "position": 1},
                {"value": "safety", "label": "Safety", "position": 2},
            ]
            field["composite_config"] = {"frontend_settings": {"allow_custom_value": True}}
        if field.get("core_key") == "subcategory":
            field["renderer"] = "select"
            field["options"] = [
                {"value": "yoga", "label": "Yoga", "position": 1, "parent_value": "wellness"},
                {"value": "fire_safety", "label": "Fire Safety", "position": 1, "parent_value": "safety"},
            ]
            field["composite_config"] = {"frontend_settings": {"allow_custom_value": True}}

    super_admin = {"id": str(uuid4()), "role": "super_admin"}
    created = form_service.create_configuration_service(
        db, TrainingFormConfigurationCreate(name="Custom category config", sections=sections), super_admin,
    )
    form_service.publish_configuration_service(db, created["id"], super_admin)
    form_service.activate_configuration_service(db, created["id"], super_admin)


# --- point 1: non-empty category / any subcategory, no taxonomy check ---

def test_create_accepts_custom_category_not_in_predefined_options(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="Leadership Offsite", category="Corporate Offsite",
    ), current_user=_admin(ent))
    assert created.category == "Corporate Offsite"


def test_create_accepts_custom_subcategory_not_in_predefined_options(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="Prenatal Yoga", category="wellness", subcategory="Prenatal Care",
    ), current_user=_admin(ent))
    assert created.category == "wellness"
    assert created.subcategory == "Prenatal Care"


def test_create_rejects_empty_category_at_schema_level():
    with pytest.raises(ValidationError):
        TrainingCreate(enterprise_id=uuid4(), title="T", category="")


# --- point 2: max length enforced as a clean validation error, not a DB blow-up ---

def test_create_rejects_over_100_char_category_at_schema_level():
    with pytest.raises(ValidationError):
        TrainingCreate(enterprise_id=uuid4(), title="T", category="x" * 101)


def test_create_rejects_over_100_char_subcategory_at_schema_level():
    with pytest.raises(ValidationError):
        TrainingCreate(enterprise_id=uuid4(), title="T", category="Wellness", subcategory="x" * 101)


def test_create_accepts_exactly_100_char_category(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    long_category = "x" * 100
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="T", category=long_category,
    ), current_user=_admin(ent))
    assert created.category == long_category


def test_update_rejects_empty_category_but_allows_omitting():
    with pytest.raises(ValidationError):
        TrainingUpdate(category="")
    TrainingUpdate()  # omitted entirely — must not raise


# --- point 3: custom subcategory under predefined category, and vice versa ---

def test_predefined_subcategory_under_custom_category_accepted_end_to_end(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="T", category="Corporate Offsite", subcategory="yoga",
    ), current_user=_admin(ent))
    assert created.category == "Corporate Offsite"
    assert created.subcategory == "yoga"


def test_custom_subcategory_under_predefined_category_accepted_end_to_end(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="T", category="wellness", subcategory="Prenatal Care",
    ), current_user=_admin(ent))
    assert created.subcategory == "Prenatal Care"


def test_predefined_subcategory_under_mismatched_predefined_category_still_rejected(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        create_training_service(db, TrainingCreate(
            enterprise_id=ent.id, title="T", category="safety", subcategory="yoga",
        ), current_user=_admin(ent))
    assert exc.value.status_code == 400


# --- point 4: list/detail/search/by_category return stored strings verbatim ---

def test_custom_category_returned_verbatim_and_searchable(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="Leadership Offsite", category="Corporate Offsite Retreat",
    ), current_user=_admin(ent))

    reloaded = db.get(Training, created.id)
    assert reloaded.category == "Corporate Offsite Retreat"

    found = search_trainings_service(db, query="Offsite Retreat")
    assert created.id in {i.id for i in found.items}


def test_custom_category_appears_in_summary_by_category_uncategorized_bucket(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="T", category="Corporate Offsite", status="published",
    ), current_user=_admin(ent))

    summary = get_training_summary_service(db, _admin(ent))
    assert summary["by_category"].get("Corporate Offsite") == 1


# --- point 5: the "Other" sentinel is rejected end-to-end ---

def test_sentinel_rejected_on_create(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        create_training_service(db, TrainingCreate(
            enterprise_id=ent.id, title="T", category=OTHER_OPTION_SENTINEL,
        ), current_user=_admin(ent))
    assert exc.value.status_code == 400
    assert OTHER_OPTION_SENTINEL in exc.value.detail


def test_sentinel_rejected_on_update(db):
    _activate_custom_category_form_config(db)
    ent = _make_enterprise(db)
    admin = _admin(ent)
    created = create_training_service(db, TrainingCreate(
        enterprise_id=ent.id, title="T", category="wellness",
    ), current_user=admin)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        update_training_service(db, created.id, TrainingUpdate(subcategory=OTHER_OPTION_SENTINEL), current_user=admin)
    assert exc.value.status_code == 400
