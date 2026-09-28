"""Training form conditions and field upload policies (server-owned rules)."""
from __future__ import annotations

import math
import re
from fastapi import HTTPException


def empty(value):
    return value is None or value == "" or value == [] or value == {}


def fields(sections):
    return [f for s in sections for f in s.get("fields", [])]


def settings(field):
    return (field.get("composite_config") or {}).get("frontend_settings") or {}


def media_kind(field):
    key = field.get("core_key")
    if key in ("primary_image", "gallery_images"):
        return "image"
    if key in ("promotional_video", "videos"):
        return "video"
    if key in ("documents", "notes_pdf_url"):
        return "document"
    return field.get("renderer") if field.get("renderer") in ("image", "document", "video") else None


def field_index(sections):
    index = {}
    for field in fields(sections):
        for key in {field.get("id"), field.get("core_key"), field.get("stable_key")} - {None, ""}:
            if key in index and index[key] is not field:
                raise HTTPException(400, f"Ambiguous form field key: {key}")
            index[key] = field
    return index


def validate_settings(sections):
    """Called on writes/publish, never silently normalize invalid policies away."""
    if not any((f.get("composite_config") or {}).get("frontend_settings") is not None for f in fields(sections)):
        return
    index = field_index(sections)
    edges = {}
    for field in fields(sections):
        composite = field.get("composite_config")
        if composite is not None and not isinstance(composite, dict):
            raise HTTPException(400, "composite_config must be an object")
        raw = (composite or {}).get("frontend_settings")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            raise HTTPException(400, "frontend_settings must be an object")
        rule = raw.get("visibility")
        if rule is not None:
            if not isinstance(rule, dict) or rule.get("operator") not in ("equals", "not_equals", "has_value", "is_empty"):
                raise HTTPException(400, "Invalid visibility operator")
            key = rule.get("field_key")
            if not isinstance(key, str) or key not in index:
                raise HTTPException(400, f"Unknown visibility field_key: {key}")
            if index[key] is field:
                raise HTTPException(400, "Visibility cannot reference its own field")
            if field.get("core_key") in ("title", "category"):
                raise HTTPException(400, "Domain-required title/category cannot be conditionally hidden")
            if rule["operator"] in ("equals", "not_equals") and "value" not in rule:
                raise HTTPException(400, "Visibility equals/not_equals requires value")
            edges[field["id"]] = index[key]["id"]
        upload = raw.get("upload")
        if upload is not None:
            if not isinstance(upload, dict) or not media_kind(field):
                raise HTTPException(400, "Upload policy requires an image, document or video field")
            allowed = upload.get("allowed_mime_types")
            if allowed is not None:
                from app.services.training_form_media import SUPPORTED_MIMES
                if not isinstance(allowed, list) or not allowed or any(not isinstance(m, str) or m not in SUPPORTED_MIMES for m in allowed):
                    raise HTTPException(400, "allowed_mime_types must be a nonempty list of supported exact MIME types")
                if any(SUPPORTED_MIMES[m] != media_kind(field) for m in allowed):
                    raise HTTPException(400, "Upload MIME types do not match the field media type")
            cap = upload.get("max_file_size_mb")
            if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (float, int)) or not math.isfinite(cap) or cap <= 0):
                raise HTTPException(400, "max_file_size_mb must be a positive finite number")
    done = set()
    for start in edges:
        path = set()
        node = start
        while node in edges and node not in done:
            if node in path:
                raise HTTPException(400, "Circular visibility conditions are not allowed")
            path.add(node)
            node = edges[node]
        done.update(path)


def custom_map(raw):
    if isinstance(raw, dict):
        return raw
    result = {}
    for item in raw or []:
        if not isinstance(item, dict):
            if not hasattr(item, "model_dump"):
                raise HTTPException(400, "custom_values entries must be objects")
            item = item.model_dump()
        result[str(item.get("field_id") or item.get("id") or item.get("key") or "")] = item.get("value")
    return result


def visibility(sections, payload, custom_values=None):
    index = field_index(sections) if any(settings(f).get("visibility") for f in fields(sections)) else {}
    custom = custom_map(custom_values)
    enabled = {f["id"]: s.get("is_enabled", True) and f.get("is_enabled", True)
               for s in sections for f in s.get("fields", [])}
    result = {}
    visiting = set()

    def value(field):
        if field.get("source") == "core":
            return payload.get(field.get("core_key"), custom.get(field.get("core_key")))
        return custom.get(field["id"], custom.get(field.get("stable_key")))

    def visible(field):
        fid = field["id"]
        if fid in result:
            return result[fid]
        if fid in visiting:
            raise HTTPException(400, "Circular visibility conditions are not allowed")
        visiting.add(fid)
        show = bool(enabled[fid])
        rule = settings(field).get("visibility")
        if show and rule:
            ref = index.get(rule["field_key"])
            if ref is None:
                raise HTTPException(400, "Unknown visibility field_key")
            # A hidden controller cannot activate its dependants via retained data.
            show = visible(ref)
            if show:
                actual, op = value(ref), rule["operator"]
                show = {"equals": lambda: type(actual) is type(rule.get("value")) and actual == rule.get("value"),
                        "not_equals": lambda: not (type(actual) is type(rule.get("value")) and actual == rule.get("value")),
                        "has_value": lambda: not empty(actual), "is_empty": lambda: empty(actual)}[op]()
        visiting.remove(fid)
        result[fid] = show
        return show

    for field in fields(sections):
        visible(field)
    return result


def validate_constraints(field, value):
    if empty(value):
        if field.get("required"):
            raise HTTPException(400, f"Required field '{field.get('label')}' is missing")
        return
    rules = field.get("validation") or {}
    label = field.get("label")
    if isinstance(value, str):
        for key, invalid in (("min_length", lambda n: len(value) < n), ("max_length", lambda n: len(value) > n)):
            if rules.get(key) is not None and invalid(int(rules[key])):
                raise HTTPException(400, f"Field '{label}' violates {key}")
        if rules.get("pattern") and not re.match(str(rules["pattern"]), value):
            raise HTTPException(400, f"Field '{label}' format invalid")
    if any(rules.get(k) is not None for k in ("min", "max")):
        try:
            number = float(value)
            if isinstance(value, bool) or not math.isfinite(number):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"Field '{label}' requires a finite numeric value")
        for key, invalid in (("min", lambda n: number < n), ("max", lambda n: number > n)):
            if rules.get(key) is not None and invalid(float(rules[key])):
                raise HTTPException(400, f"Field '{label}' violates {key}")
    if field.get("renderer") in ("select", "multi_select"):
        allow_custom = bool(settings(field).get("allow_custom_value"))
        allowed = [o.get("value") for o in field.get("options") or []]
        values = value if field["renderer"] == "multi_select" and isinstance(value, list) else [value]
        if allowed and not allow_custom and any(v not in allowed for v in values):
            raise HTTPException(400, f"Invalid select value for '{label}'")


def validate_category_subcategory_linkage(payload: dict, subcategory_field: dict | None) -> None:
    """When the subcategory field is a select whose options declare parent_value,
    a submitted subcategory that matches a known option must belong to the
    submitted category. A value not found in options is a custom ("Other")
    entry — those aren't tied to a category and skip this check. Caller is
    responsible for passing None when the field is disabled/hidden/not core."""
    if not subcategory_field or subcategory_field.get("renderer") not in ("select", "multi_select"):
        return
    options = subcategory_field.get("options") or []
    if not any(o.get("parent_value") for o in options):
        return
    sub_value = payload.get("subcategory")
    if empty(sub_value):
        return
    sub_values = sub_value if isinstance(sub_value, list) else [sub_value]
    category_value = payload.get("category")
    by_value = {o.get("value"): o for o in options}
    for v in sub_values:
        option = by_value.get(v)
        if option is None:
            continue  # custom/"Other" entry — not tied to a category
        parent = option.get("parent_value")
        if parent and parent != category_value:
            raise HTTPException(400, f"Subcategory '{v}' does not belong to category '{category_value}'")
