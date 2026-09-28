from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

from app.db.database import Base, get_db
from app.core.dependencies import get_current_user
from app.models.training_form_config_model import TrainingFormConfiguration, TrainingFormConfigurationVersion, TrainingFormAudit
from app.schemas.training_form_config_schema import TrainingFormConfigurationCreate, TrainingFormConfigurationUpdate, PublishConfigurationResponse, FormFieldResponse
from app.schemas.training_schema import TrainingUpdate
from app.services import training_form_config_service as service
from app.services.training_form_registry import build_default_sections, normalize_sections
from app.services.training_form_rules import validate_settings, visibility
from app.services.training_form_media import validate_media_bytes, validate_media_value, resolve_upload_field


def sections():
    result = build_default_sections()
    result[0]["fields"].append({
        "id": "handout", "stable_key": "handout", "source": "custom", "label": "Handout",
        "renderer": "document", "value_type": "string", "required": True,
        "composite_config": {"future_extension": {"kept": True}, "frontend_settings": {
            "visibility": {"field_key": "delivery_mode", "operator": "equals", "value": "online"},
            "upload": {"allowed_mime_types": ["application/pdf"], "max_file_size_mb": 20}}},
    })
    return normalize_sections(result)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda *a, **kw: "JSON", raising=False)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine, tables=[m.__table__ for m in (TrainingFormConfiguration, TrainingFormConfigurationVersion, TrainingFormAudit)])
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_configuration_full_lifecycle_preserves_metadata(db, monkeypatch):
    actor = {"id": str(uuid4()), "role": "super_admin"}
    raw = sections()
    created = service.create_configuration_service(db, TrainingFormConfigurationCreate(name="Conditional", sections=raw), actor)
    cid = created["id"]
    def metadata(data):
        return data[0]["fields"][-1]["composite_config"]
    assert metadata(created["draft_version"]["sections"]) == metadata(raw)
    published = PublishConfigurationResponse.model_validate(service.publish_configuration_service(db, cid, actor)).model_dump()
    assert metadata(published["sections"]) == metadata(raw)
    vid = published["version_id"]
    service.activate_configuration_service(db, cid, actor)
    monkeypatch.setattr(service, "resolve_auth_tenant_id_with_db", lambda *a, **k: None)
    assert metadata(service.get_active_form_configuration_service(db, {"role": "admin"})["sections"]) == metadata(raw)
    changed = deepcopy(raw)
    changed[0]["fields"][-1]["composite_config"]["frontend_settings"]["upload"]["max_file_size_mb"] = 5
    updated = service.update_configuration_service(db, cid, TrainingFormConfigurationUpdate(sections=changed), actor)
    assert metadata(updated["draft_version"]["sections"]) == metadata(changed)
    old = service.get_version_service(db, cid, vid)
    assert metadata(old["sections"]) == metadata(raw)
    monkeypatch.setattr("app.repository.training_repo.get_training_by_id", lambda *a: SimpleNamespace(form_configuration_version_id=vid))
    assert metadata(service.get_training_form_configuration_service(db, uuid4(), {"role": "admin"})["sections"]) == metadata(raw)
    assert FormFieldResponse.model_validate(old["sections"][0]["fields"][-1]).composite_config == metadata(raw)


@pytest.mark.parametrize("operator,value,expected", [("equals", "online", True), ("equals", "physical", False),
    ("not_equals", "physical", True), ("has_value", False, True), ("is_empty", "", True), ("is_empty", 0, False)])
def test_operators(operator, value, expected):
    raw = sections()
    raw[0]["fields"][-1]["composite_config"]["frontend_settings"]["visibility"]["operator"] = operator
    assert visibility(raw, {"delivery_mode": value})["handout"] is expected


@pytest.mark.parametrize("mutation", ["missing", "self", "cycle", "operator", "size", "mime", "purpose", "required_core"])
def test_invalid_rules_rejected_at_create(db, mutation):
    raw = sections()
    config = raw[0]["fields"][-1]["composite_config"]["frontend_settings"]
    if mutation in ("missing", "self"):
        config["visibility"]["field_key"] = "missing" if mutation == "missing" else "handout"
    elif mutation == "cycle":
        controller = next(f for f in raw[0]["fields"] if f.get("core_key") == "delivery_mode")
        controller["composite_config"] = {"frontend_settings": {"visibility": {"field_key": "handout", "operator": "has_value"}}}
    elif mutation == "operator":
        config["visibility"]["operator"] = "contains"
    elif mutation == "size":
        config["upload"]["max_file_size_mb"] = -1
    elif mutation == "mime":
        config["upload"]["allowed_mime_types"] = ["image/png"]
    elif mutation == "purpose":
        raw[0]["fields"][-1]["renderer"] = "text"
    else:
        raw[0]["fields"][0]["composite_config"] = {"frontend_settings": {"visibility": config["visibility"]}}
    with pytest.raises(HTTPException) as exc:
        service.create_configuration_service(db, TrainingFormConfigurationCreate(name="bad", sections=raw), {"id": "admin"})
    assert exc.value.status_code == 400
    assert db.query(TrainingFormConfiguration).count() == 0


def test_hidden_custom_retained_visible_required_enforced():
    raw = sections()
    assert service.validate_custom_values({"handout": 123}, raw, {"delivery_mode": "physical"}) == [{"field_id": "handout", "value": 123}]
    assert service.validate_custom_values([], raw, {"delivery_mode": "physical"}) == []
    with pytest.raises(HTTPException):
        service.validate_custom_values([], raw, {"delivery_mode": "online"})


def test_core_rules_and_historical_update_use_merged_state():
    raw = sections()
    field = next(f for f in raw[0]["fields"] if f.get("core_key") == "description")
    field.update(required=True, validation={"min_length": 5}, composite_config={"frontend_settings": {
        "visibility": {"field_key": "delivery_mode", "operator": "equals", "value": "online"}}})
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(sections=raw)
    training = SimpleNamespace(form_configuration_version_id=uuid4(), title="Course", category="Safety", delivery_mode="physical", description="x", custom_values=[])
    assert service.apply_form_configuration_to_training_update(db, training, TrainingUpdate(title="Renamed"), {}) == {"custom_values": []}
    with pytest.raises(HTTPException):
        service.apply_form_configuration_to_training_update(db, training, TrainingUpdate(delivery_mode="online"), {})


def test_hidden_controller_does_not_activate_dependant():
    raw = sections()
    raw[0]["fields"].append({"id": "child", "source": "custom", "stable_key": "child", "composite_config": {
        "frontend_settings": {"visibility": {"field_key": "handout", "operator": "has_value"}}}})
    assert visibility(raw, {"delivery_mode": "physical"}, {"handout": "retained"})["child"] is False


def test_media_restrictions_and_generic_url_bypass(tmp_path, monkeypatch):
    field = sections()[0]["fields"][-1]
    assert validate_media_bytes(field, b"%PDF-1.7\nfile", "application/pdf", "lesson_pdf") == "application/pdf"
    for data, mime, purpose, status in [(b"not a pdf", "application/pdf", "lesson_pdf", 415),
                                       (b"%PDF-1.7", "image/png", "lesson_pdf", 415),
                                       (b"%PDF-1.7", "application/pdf", "lesson_video", 400)]:
        with pytest.raises(HTTPException) as exc:
            validate_media_bytes(field, data, mime, purpose)
        assert exc.value.status_code == status
    field["composite_config"]["frontend_settings"]["upload"]["max_file_size_mb"] = 0.00001
    with pytest.raises(HTTPException) as exc:
        validate_media_bytes(field, b"%PDF-1.7" + b"x" * 20)
    assert exc.value.status_code == 413
    monkeypatch.setattr("app.services.generic_upload_service.generic_upload_root", lambda: tmp_path)
    (tmp_path / "general").mkdir()
    (tmp_path / "general" / "fake.pdf").write_bytes(b"not pdf")
    with pytest.raises(HTTPException) as exc:
        validate_media_value(field, {"url": "/api/v1/uploads/general/fake.pdf", "size": 1, "type": "application/pdf"})
    assert exc.value.status_code == 415
    with pytest.raises(HTTPException):
        validate_media_value(field, "https://untrusted.example/file.pdf")


def test_upload_http_policy_and_resolution(monkeypatch, tmp_path):
    from app.api.v1.endpoints.training import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/trainings")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: {"role": "admin", "id": str(uuid4())}
    monkeypatch.setattr(service, "get_active_form_configuration_service", lambda *a: {"sections": sections()})
    monkeypatch.setattr("app.services.training_upload_service.training_upload_root", lambda: tmp_path)
    with TestClient(app) as client:
        file = {"file": ("handout.pdf", b"%PDF-1.7", "application/pdf")}
        assert client.post("/api/v1/trainings/upload", files=file).status_code == 400
        ok = client.post("/api/v1/trainings/upload", files=file, data={"field_key": "handout", "purpose": "lesson_pdf"})
        assert ok.status_code == 201, ok.text
        assert ok.json()["size"] == 8
        bad = client.post("/api/v1/trainings/upload", files={"file": ("fake.pdf", b"not pdf", "application/pdf")}, data={"field_key": "handout"})
        assert bad.status_code == 415, bad.text
    assert len(list(tmp_path.iterdir())) == 1
    def forbidden(*a):
        raise HTTPException(403, "Not authorized for this tenant")
    monkeypatch.setattr("app.repository.training_repo.require_training_owner", forbidden)
    with pytest.raises(HTTPException) as exc:
        resolve_upload_field(MagicMock(), {"role": "admin"}, "handout", uuid4())
    assert exc.value.status_code == 403


def test_legacy_configuration_without_settings_still_works():
    raw = build_default_sections()
    validate_settings(raw)
    service.validate_form_required_core_fields({"title": "Course", "category": "Safety"}, raw)
    assert service.validate_custom_values([], raw) == []


def test_generic_training_upload_enforces_field_policy(monkeypatch, tmp_path):
    from app.api.v1.endpoints.uploads import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/uploads")
    app.dependency_overrides[get_db] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: {"role": "admin"}
    raw = sections()
    raw[0]["fields"][-1]["composite_config"]["frontend_settings"]["upload"]["max_file_size_mb"] = 10 / (1024 * 1024)
    monkeypatch.setattr(service, "get_active_form_configuration_service", lambda *a: {"sections": raw})
    monkeypatch.setattr("app.services.generic_upload_service.generic_upload_root", lambda: tmp_path)
    with TestClient(app) as client:
        parts = {"folder": "trainings", "field_key": "handout", "purpose": "lesson_pdf"}
        assert client.post("/api/v1/uploads/", data={"folder": "trainings"}, files={"file": ("x.pdf", b"%PDF-1.7", "application/pdf")}).status_code == 400
        too_big = client.post("/api/v1/uploads/", data=parts, files={"file": ("x.pdf", b"%PDF-1.7xxx", "application/pdf")})
        assert too_big.status_code == 413, too_big.text
        assert not list(tmp_path.iterdir())
        good = client.post("/api/v1/uploads/", data=parts, files={"file": ("x.pdf", b"%PDF-1.7xx", "application/pdf")})
        assert good.status_code == 200, good.text


def test_publish_revalidates_persisted_draft(db):
    actor = {"id": "admin"}
    created = service.create_configuration_service(db, TrainingFormConfigurationCreate(name="rules", sections=sections()), actor)
    draft = db.get(TrainingFormConfigurationVersion, created["draft_version"]["id"])
    raw = deepcopy(draft.sections)
    raw[0]["fields"][-1]["composite_config"]["frontend_settings"]["visibility"]["field_key"] = "removed"
    draft.sections = raw
    db.commit()
    with pytest.raises(HTTPException):
        service.publish_configuration_service(db, created["id"], actor)
    assert draft.status == "draft"


def test_core_media_policy_enforced_on_update_and_hidden_retained(tmp_path, monkeypatch):
    raw = build_default_sections()
    raw[0]["fields"].append({"id": "primary_image", "core_key": "primary_image", "source": "core",
        "label": "Image", "renderer": "url", "composite_config": {"frontend_settings": {
            "visibility": {"field_key": "delivery_mode", "operator": "equals", "value": "online"},
            "upload": {"allowed_mime_types": ["image/png"], "max_file_size_mb": 2}}}})
    raw = normalize_sections(raw)
    validate_settings(raw)
    training = SimpleNamespace(form_configuration_version_id=uuid4(), title="Course", category="Safety", delivery_mode="physical", custom_values=[], primary_image="https://example.org/old.png")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(sections=raw)
    service.apply_form_configuration_to_training_update(db, training, TrainingUpdate(title="New"), {})
    with pytest.raises(HTTPException) as exc:
        service.apply_form_configuration_to_training_update(db, training, TrainingUpdate(delivery_mode="online"), {})
    assert exc.value.status_code == 400


def test_historical_upload_uses_owned_training_version(monkeypatch):
    raw = sections()
    raw[0]["fields"][-1]["composite_config"]["frontend_settings"]["upload"]["max_file_size_mb"] = 3
    owner = MagicMock()
    historical = MagicMock(return_value={"sections": raw})
    active = MagicMock(side_effect=AssertionError("Must not resolve active on edit"))
    monkeypatch.setattr("app.repository.training_repo.require_training_owner", owner)
    monkeypatch.setattr(service, "get_training_form_configuration_service", historical)
    monkeypatch.setattr(service, "get_active_form_configuration_service", active)
    tid, user, db = uuid4(), {"role": "admin"}, MagicMock()
    field = resolve_upload_field(db, user, "handout", tid)
    assert field["composite_config"]["frontend_settings"]["upload"]["max_file_size_mb"] == 3
    owner.assert_called_once_with(db, tid, user)
    historical.assert_called_once_with(db, tid, user)

def test_visible_numeric_core_constraints_and_null_upload_policy():
    raw = sections()
    price = next(f for f in raw[1]["fields"] if f.get("core_key") == "price")
    price["validation"] = {"min": 10, "max": 100}
    price["composite_config"] = {"frontend_settings": {"visibility": {
        "field_key": "delivery_mode", "operator": "equals", "value": "online"}}}
    core = {"title": "Course", "category": "Safety", "delivery_mode": "physical", "price": "5"}
    service.validate_form_required_core_fields(core, raw)
    with pytest.raises(HTTPException):
        service.validate_form_required_core_fields({**core, "delivery_mode": "online"}, raw)
    field = raw[0]["fields"][-1]
    field["composite_config"]["frontend_settings"]["upload"] = None
    validate_settings(raw)
    validate_media_value(field, "https://example.org/legacy.pdf")
