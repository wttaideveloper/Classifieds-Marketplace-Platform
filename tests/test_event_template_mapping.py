from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.sql.elements import Null

from app.models.event_model import Event
from app.services import event_service, event_form_config_service as forms
from app.services.event_template_mapping import map_template_values, template_provenance, validate_event_submission


def core(key, **extra):
    return {"id": key, "source": "core", "core_key": key, **extra}


def custom(fid, key, **extra):
    return {"id": fid, "source": "custom", "stable_key": key, "renderer": "text", "value_type": "string", **extra}


def sections(*fields):
    return [{"id": "section", "stable_key": "section", "fields": list(fields)}]


def test_stable_identity_type_changes_removed_and_new_fields():
    source = sections(core("title"), core("description"), custom("old", "diet"), custom("number", "age"))
    target = sections(core("title"), core("organiser_name", required=True), custom("new", "diet"),
                      custom("number", "age", value_type="number"), custom("added", "added", required=True))
    template = {"core_values": {"title": "Summit", "description": "Removed", "status": "published", "id": str(uuid4())},
                "custom_values": [{"field_id": "old", "value": "Vegan"}, {"field_id": "number", "value": "10"}]}
    original = deepcopy(template)
    mapped = map_template_values(template, target, source)
    assert mapped == {"title": "Summit", "custom_values": [{"field_id": "new", "value": "Vegan"}]}
    assert template == original


def test_legacy_flat_template_and_disabled_section():
    target = sections(core("title"), core("description", is_enabled=False))
    target.append({"is_enabled": False, "fields": [core("organiser_name")]})
    mapped = map_template_values({"title": "Legacy", "description": "Gone", "organiser_name": "Hidden",
                                  "is_deleted": True, "last_admin_notes": "Private"}, target)
    assert mapped == {"title": "Legacy", "custom_values": []}


def test_composites_keep_only_intersecting_structure():
    source = sections(core("venue", composite_config={"enabled_fields": ["name", "coordinates.lat", "coordinates.lng"]}),
                      core("sessions", composite_config={"enabled_fields": ["title", "speaker"]}))
    target = sections(core("venue", composite_config={"enabled_fields": ["name", "coordinates.lat", "city"]}),
                      core("sessions", composite_config={"enabled_fields": ["title", "location"]}))
    mapped = map_template_values({"composites": {
        "venue": {"name": "Hall", "coordinates": {"lat": 12, "lng": 77}, "city": "Not in source"},
        "sessions": [{"id": "old", "title": "Welcome", "speaker": "Removed", "location": "Not in source"}],
    }}, target, source)
    assert mapped["venue"] == {"name": "Hall", "coordinates": {"lat": 12}}
    assert mapped["sessions"] == [{"title": "Welcome"}]


def test_changed_select_options_are_ignored():
    target = sections(custom("choice", "choice", renderer="select", options=[{"value": "new"}]))
    assert map_template_values({"custom_values": [{"field_id": "choice", "value": "old"}]}, target)["custom_values"] == []


def test_provenance_retained_and_mismatch_rejected():
    db = MagicMock()
    cid, vid = uuid4(), uuid4()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(configuration_id=cid)
    assert template_provenance(db, {"configuration_version_id": vid}) == {"configuration_id": cid, "configuration_version_id": vid}
    with pytest.raises(HTTPException) as exc:
        template_provenance(db, {"configuration_id": uuid4(), "configuration_version_id": vid})
    assert exc.value.status_code == 400


def test_apply_uses_current_version_and_resets_lifecycle(monkeypatch):
    cid, vid, old_vid, tid = uuid4(), uuid4(), uuid4(), uuid4()
    tmpl = SimpleNamespace(tenant_id=tid, enterprise_id=None, configuration_version_id=old_vid,
                           template_data={"core_values": {"title": "Summit", "status": "published", "id": str(uuid4())},
                                          "composites": {"sessions": [{"id": "old", "title": "Welcome"}]}})
    definition = sections(core("title"), core("sessions"), core("description", required=True))
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [tmpl, SimpleNamespace(sections=definition)]
    resolve = MagicMock(return_value=(SimpleNamespace(id=cid), SimpleNamespace(id=vid, sections=definition)))
    monkeypatch.setattr(forms, "_resolve_active_form_configuration", resolve)
    event = event_service.apply_template_service(db, uuid4(), {}, {"role": "admin", "tenant_id": str(tid)})
    assert event.form_configuration_version_id == vid
    assert event.form_configuration_id == cid
    assert event.status == "draft"
    assert event.id is None  # Database generates a fresh ID on INSERT.
    assert isinstance(event.description, Null)
    assert event.sessions[0]["id"] != "old"
    assert tmpl.template_data["composites"]["sessions"][0]["id"] == "old"
    resolve.assert_called_once_with(db, tid)


def test_no_active_form_does_not_create_event(monkeypatch):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(
        tenant_id=None, enterprise_id=None, template_data={"title": "Legacy"})
    monkeypatch.setattr(forms, "_resolve_active_form_configuration", MagicMock(side_effect=HTTPException(404, "No active Event form configuration found")))
    with pytest.raises(HTTPException) as exc:
        event_service.apply_template_service(db, uuid4(), {}, {})
    assert exc.value.status_code == 404
    db.add.assert_not_called()


def ready_event(**extra):
    values = dict(title="Event", description="Description", category="Category", start_date=datetime(2026, 1, 1),
                  end_date=datetime(2026, 1, 2), form_configuration_version_id=uuid4(), status="draft", custom_values=[])
    return Event(**{**values, **extra})


def test_submission_requires_new_custom_fields_and_composite_parts():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(sections=sections(
        custom("required", "required", required=True, label="Diet"),
        core("venue", composite_config={"enabled_fields": ["name", "city"], "required_fields": ["city"]})))
    event = ready_event(venue={"name": "Hall"})
    with pytest.raises(HTTPException):
        validate_event_submission(db, event)
    event.custom_values = [{"field_id": "required", "value": "Vegan"}]
    with pytest.raises(HTTPException):
        validate_event_submission(db, event)
    event.venue = {"name": "Hall", "city": "Chennai"}
    validate_event_submission(db, event)


def test_missing_domain_fields_cannot_be_submitted():
    with pytest.raises(HTTPException) as exc:
        validate_event_submission(MagicMock(), ready_event(title=None))
    assert exc.value.detail["code"] == "EVENT_FORM_INCOMPLETE"


def test_status_submission_runs_guard_before_commit(monkeypatch):
    event = ready_event(title=None, is_deleted=False)
    db = MagicMock()
    monkeypatch.setattr(event_service, "get_event_by_id", lambda *args, **kwargs: event)
    with pytest.raises(HTTPException):
        event_service.update_event_status_service(db, uuid4(), "pending_approval", {"role": "admin"})
    assert event.status == "draft"
    db.commit.assert_not_called()
