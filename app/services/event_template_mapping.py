"""Map reusable values, never lifecycle/identity columns, into a current form."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException

from app.services.event_form_config_service import (
    _iter_enabled_fields, _validate_custom_value, normalize_sections,
    validate_form_required_core_fields,
)
from app.services.event_form_registry import COMPOSITE_CORE_KEYS, REGISTRY_BY_KEY


def template_provenance(db, payload, existing=None):
    """Accept explicit provenance or legacy Event JSON references; validate the pair."""
    from app.models.event_form_config_model import EventFormConfigurationVersion
    data = payload.get("template_data") or {}
    config_id = payload.get("configuration_id", data.get("form_configuration_id", getattr(existing, "configuration_id", None)))
    version_id = payload.get("configuration_version_id", data.get("form_configuration_version_id", getattr(existing, "configuration_version_id", None)))
    if version_id:
        try:
            version_id = UUID(str(version_id))
        except ValueError:
            raise HTTPException(400, "Invalid configuration_version_id")
        version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == version_id).first()
        if not version:
            raise HTTPException(400, "Source form configuration version not found")
        if config_id and str(config_id) != str(version.configuration_id):
            raise HTTPException(400, "Source configuration/version mismatch")
        config_id = version.configuration_id
    elif config_id:
        raise HTTPException(400, "configuration_version_id is required with configuration_id")
    return {"configuration_id": config_id, "configuration_version_id": version_id}


def _compatible(source, target):
    return source is None or (
        source.get("value_type") == target.get("value_type")
        and source.get("renderer") == target.get("renderer")
    )


def _get_path(value, path):
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _composite(value, field, source):
    allowed = set((field.get("composite_config") or {}).get("enabled_fields") or [])
    if source:
        allowed &= set((source.get("composite_config") or {}).get("enabled_fields") or [])
    if isinstance(value, dict):
        out = {}
        for path in allowed:
            entry = _get_path(value, path)
            if entry is None:
                continue
            target = out
            parts = path.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = deepcopy(entry)
        return out or None
    if isinstance(value, list):
        return [mapped for item in value if (mapped := _composite(item, field, source)) is not None]
    if isinstance(value, str):
        key = field.get("core_key")
        if (key == "tags" and allowed & {"freeform", "predefined_options"}) or (
            key in {"primary_image", "gallery_images", "videos", "documents"} and "url" in allowed
        ):
            return value
    return None


def map_template_values(template_data, target_sections, source_sections=None):
    target = list(_iter_enabled_fields(normalize_sections(target_sections, assign_ids=False)))
    source = list(_iter_enabled_fields(normalize_sections(source_sections or [], assign_ids=False)))
    source_core = {f["core_key"]: f for f in source if f["source"] == "core"}
    source_custom = {str(f["id"]): f for f in source if f["source"] == "custom"}
    values = template_data.get("core_values", template_data)
    if not isinstance(values, dict) or not isinstance(template_data.get("composites", {}), dict):
        raise HTTPException(400, "core_values and composites must be objects")
    values = {**values, **template_data.get("composites", {})}
    result = {}
    for field in target:
        key = field.get("core_key")
        if field["source"] != "core" or key not in REGISTRY_BY_KEY or key not in values:
            continue
        old = source_core.get(key)
        if source_sections is not None and old is None:
            continue
        if not _compatible(old, field):
            continue
        value = deepcopy(values[key])
        if key in COMPOSITE_CORE_KEYS:
            value = _composite(value, field, old)
        else:
            try:
                from app.schemas.event_schema import EventUpdate
                from pydantic import ValidationError
                value = EventUpdate.model_validate({key: value}).model_dump(exclude_unset=True)[key]
                # Dynamic select options are validated by the domain, not an empty option list.
                check = {**field, "required": False}
                if not check.get("options"):
                    check["renderer"] = None
                _validate_custom_value(check, value)
            except (HTTPException, ValidationError):
                continue
        if value is not None:
            result[key] = value

    custom = template_data.get("custom_values") or []
    if not isinstance(custom, list) or any(not isinstance(v, dict) for v in custom):
        raise HTTPException(400, "custom_values must be an array of objects")
    result["custom_values"] = []
    used = set()
    for item in custom:
        fid = str(item.get("field_id") or item.get("id") or "")
        old = source_custom.get(fid)
        if old is None and not fid and item.get("stable_key"):
            matches = [f for f in source_custom.values() if f.get("stable_key") == item["stable_key"]]
            old = matches[0] if len(matches) == 1 else None
        stable_key = old.get("stable_key") if old else item.get("stable_key")
        if source_sections is not None and old is None:
            continue
        candidates = [f for f in target if f["source"] == "custom" and (
            str(f["id"]) == fid or (stable_key and f.get("stable_key") == stable_key)
        )]
        if len(candidates) != 1:
            continue
        field = candidates[0]
        if field["id"] in used or not _compatible(old, field):
            continue
        try:
            _validate_custom_value({**field, "required": False}, item.get("value"))
        except HTTPException:
            continue
        used.add(field["id"])
        result["custom_values"].append({"field_id": field["id"], "value": deepcopy(item.get("value"))})
    return result


def validate_event_submission(db, event, overrides=None):
    """Drafts may be incomplete; submission validates their pinned historical form."""
    from app.models.event_form_config_model import EventFormConfigurationVersion
    from app.schemas.event_schema import EventCreate
    from pydantic import ValidationError

    data = {column.key: getattr(event, column.key) for column in event.__table__.columns}
    data.update(overrides or {})
    try:
        # Optional blank draft fields must not acquire defaults or become required.
        validation_data = {key: value for key, value in data.items() if value is not None}
        EventCreate.model_validate(validation_data)
    except ValidationError as exc:
        raise HTTPException(400, detail={"code": "EVENT_FORM_INCOMPLETE", "errors": exc.errors(include_context=False)})
    version_id = data.get("form_configuration_version_id")
    if not version_id:
        return  # Existing legacy Event behavior.
    version = db.query(EventFormConfigurationVersion).filter(EventFormConfigurationVersion.id == version_id).first()
    if not version:
        raise HTTPException(400, "Event linked to missing form configuration version")
    sections = normalize_sections(version.sections or [], assign_ids=False)
    validate_form_required_core_fields(SimpleNamespace(model_dump=lambda: data), sections)
    fields = list(_iter_enabled_fields(sections))
    custom = {str(v["field_id"]): v.get("value") for v in data.get("custom_values") or []}
    for field in fields:
        key = field.get("core_key")
        value = data.get(key) if field["source"] == "core" else custom.get(str(field["id"]))
        if field.get("required") and (value is None or value == "" or value == []):
            raise HTTPException(400, f"Required field '{field.get('label')}' is missing")
        if field["source"] == "custom" and value is not None:
            _validate_custom_value(field, value)
        required = (field.get("composite_config") or {}).get("required_fields") or []
        if value is not None and required:
            for item in value if isinstance(value, list) else [value]:
                for path in required:
                    if _get_path(item, path) in (None, "", []):
                        raise HTTPException(400, f"Required sub-field '{key}.{path}' is missing")
