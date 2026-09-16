"""Shared authoring and read normalization for Training/Course curricula."""
from copy import deepcopy
from uuid import uuid4
from urllib.parse import urlparse

from fastapi import HTTPException

ITEM_TYPES = {"topic", "video", "live", "venue", "pdf", "notes", "quiz", "assignment"}
LEGACY_TYPES = {"text", "exam", "audio", "webpage", "presentation", "worksheet", "document"}
STATE_FIELDS = {"is_locked", "is_completed", "completed_at", "is_submitted", "score", "score_percent", "passed", "feedback"}


def real_content_url(value):
    if not isinstance(value, str):
        return None
    url = urlparse(value)
    if (url.hostname or "").lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"} and url.path == "/results":
        return None
    return value


def normalize_assessment(raw, *, strict=False):
    value = deepcopy(raw)
    value.setdefault("id", str(uuid4()))
    for key in STATE_FIELDS:
        value.pop(key, None)
    value.setdefault("type", "quiz")
    if strict:
        value["score_unit"] = "points"
    for question in value.get("questions") or []:
        question.setdefault("id", str(uuid4()))
        if question.get("question_type") in ("mcq", "multiple_choice"):
            question["question_type"] = "single_choice"
        options = question.get("options") or []
        question["options"] = [
            {"id": str(opt.get("id") or chr(97 + i)), "label": str(opt.get("label", opt.get("value", "")))}
            if isinstance(opt, dict) else {"id": chr(97 + i), "label": str(opt)}
            for i, opt in enumerate(options)
        ]
        if strict and question.get("question_type") in ("single_choice", "multiple_select") and len(options) < 2:
            raise HTTPException(400, "Choice questions require at least two real options")
    return value


def normalize_curriculum(sections, assessments=None, assignments=None, *, strict=False):
    exams, tasks = {}, {}
    for raw in assessments or []:
        if isinstance(raw, dict):
            assessment = normalize_assessment(raw, strict=strict)
            exams[str(assessment["id"])] = assessment
    for raw in assignments or []:
        if isinstance(raw, dict):
            task = {k: deepcopy(v) for k, v in raw.items() if k not in STATE_FIELDS}
            task.setdefault("id", str(uuid4()))
            if "due_at" in task and "due_date" not in task:
                task["due_date"] = task["due_at"]
            tasks[str(task["id"])] = task
    result = []
    seen = set()
    for index, raw in enumerate(sections or [], 1):
        if not isinstance(raw, dict):
            raise HTTPException(400, "Sections must be objects")
        section = deepcopy(raw)
        section.setdefault("id", str(uuid4()))
        section["type"] = section.get("type") or "section"
        section["order"] = index
        section.pop("is_unlocked", None)
        section.pop("unlock_hint", None)
        section.setdefault("schedule", None)
        for key in ("venue", "address", "meeting_link"):
            section.setdefault(key, None)
        if strict and "items" in raw and "lessons" in raw and raw["items"] != raw["lessons"]:
            raise HTTPException(400, "items and lessons must agree when both are supplied")
        items = section.pop("items", section.get("lessons", [])) or []
        normalized = []
        for order, raw_item in enumerate(items, 1):
            item = deepcopy(raw_item)
            if not isinstance(item, dict):
                raise HTTPException(400, "Curriculum items must be objects")
            item.setdefault("id", str(uuid4()))
            if strict and item["id"] in seen:
                raise HTTPException(400, "Curriculum item IDs must be unique")
            seen.add(item["id"])
            item["order"] = order
            for key in STATE_FIELDS:
                item.pop(key, None)
            kind = item.get("type")
            if not kind or kind == "other":
                if item.get("assessment_id") or item.get("assessment"):
                    kind = "quiz"
                elif item.get("assignment_id") or item.get("assignment"):
                    kind = "assignment"
                elif item.get("video_url"):
                    kind = "video"
                elif item.get("meeting_link") or item.get("join_url"):
                    kind = "live"
                elif item.get("venue"):
                    kind = "venue"
                elif str(item.get("content_url") or "").split("?")[0].lower().endswith(".pdf"):
                    kind = "pdf"
                else:
                    kind = "topic"
            if strict and kind not in ITEM_TYPES | LEGACY_TYPES:
                raise HTTPException(400, f"Unsupported curriculum item type: {kind}")
            item["type"] = kind
            item["detail"] = item.get("detail") or item.get("content")
            item["content"] = item.get("content") or item.get("detail")
            item["duration"] = item.get("duration") or item.get("duration_minutes")
            item["meeting_link"] = item.get("meeting_link") or item.get("join_url")
            item["join_url"] = item["meeting_link"]
            url = item.get("content_url") or item.get("video_url")
            if strict and url and not real_content_url(url):
                raise HTTPException(400, "Use a video/content URL, not a YouTube search URL")
            item["content_url"] = real_content_url(url)
            if kind == "video":
                item["video_url"] = item["content_url"]
            for flag in ("is_preview", "is_mandatory", "is_downloadable"):
                item[flag] = bool(item.get(flag, False))
            if isinstance(item.get("assessment"), dict):
                assessment = normalize_assessment(item["assessment"], strict=strict)
                item["assessment_id"] = assessment["id"]
                exams[str(assessment["id"])] = assessment
            if isinstance(item.get("assignment"), dict):
                assignment = {k: v for k, v in item["assignment"].items() if k not in STATE_FIELDS}
                assignment.setdefault("id", str(uuid4()))
                assignment.setdefault("title", item.get("title"))
                if "due_at" in assignment and "due_date" not in assignment:
                    assignment["due_date"] = assignment["due_at"]
                item["assignment_id"] = assignment["id"]
                tasks[str(assignment["id"])] = assignment
            if kind == "assignment" and not item.get("assignment_id"):
                match = next((a for a in tasks.values() if a.get("lesson_id") == item["id"]), None)
                if match:
                    item["assignment_id"] = match["id"]
                elif strict:
                    task_id = str(uuid4())
                    tasks[task_id] = {"id": task_id, "title": item.get("title"), "kind": "text",
                                      "instructions": item.get("content"), "lesson_id": item["id"], "section_id": section["id"]}
                    item["assignment_id"] = task_id
            item["assessment"] = deepcopy(exams.get(str(item.get("assessment_id"))))
            item["assignment"] = deepcopy(tasks.get(str(item.get("assignment_id"))))
            normalized.append(item)
        section["lessons"] = normalized
        quiz_count = sum(i["type"] in ("quiz", "exam") for i in normalized)
        content_count = len(normalized) - quiz_count
        parts = [f"{content_count} lesson{'s' if content_count != 1 else ''}"]
        if quiz_count:
            parts.append(f"{quiz_count} quiz{'zes' if quiz_count != 1 else ''}")
        section["summary"] = section.get("summary") or " · ".join(parts)
        section["items"] = deepcopy(normalized)
        result.append(section)
    return {"sections": result, "assessments": list(exams.values()), "assignments": list(tasks.values())}


def normalize_authoring(payload, existing=None):
    data = dict(payload)
    if any(k in data for k in ("sections", "assessments", "assignments")):
        curriculum = normalize_curriculum(*[
            data.get(k, getattr(existing, k, None) if existing is not None else None)
            for k in ("sections", "assessments", "assignments")
        ], strict=True)
        # Store one authoritative list. Read responses expose both aliases.
        for section in curriculum["sections"]:
            section.pop("items", None)
            for item in section["lessons"]:
                item.pop("assessment", None)
                item.pop("assignment", None)
        data.update(curriculum)
    mode = data.get("delivery_mode", getattr(existing, "delivery_mode", None))
    if mode in ("physical", "hybrid"):
        data["check_in"] = True
    elif mode in ("online", "recorded", "self_paced"):
        data.update(check_in=False, pass_code=None, qr_payload=None, venue=None, address=None)
        if mode in ("recorded", "self_paced"):
            data.update(meeting_link=None, meeting_provider=None, meeting_id=None, meeting_passcode=None,
                        start_date=None, end_date=None, start_time=None, end_time=None)
    if mode == "physical":
        data.update(meeting_link=None, meeting_provider=None, meeting_id=None, meeting_passcode=None)
    if mode in ("recorded", "self_paced") and "sections" in data:
        for section in data["sections"]:
            section["schedule"] = None
            if any(item["type"] in ("live", "venue") for item in section["lessons"]):
                raise HTTPException(400, "Recorded training cannot contain live or venue items")
    return data


def save_builder_curriculum(db, training, section_id, item_id=None):
    """Persist inline builder assessments/tasks with the same full-write contract."""
    from sqlalchemy.orm.attributes import flag_modified

    data = normalize_authoring({"sections": training.sections}, training)
    for key, value in data.items():
        setattr(training, key, value)
        # Callers edit nested dictionaries in place. MutableList only tracks
        # list operations; assigning an equal normalized copy may still look
        # unchanged to SQLAlchemy. Explicitly mark the JSON columns as dirty.
        if key in ("sections", "assessments", "assignments"):
            flag_modified(training, key)
    db.commit()
    db.refresh(training)
    section = next(s for s in normalize_curriculum(training.sections, training.assessments, training.assignments)["sections"] if s["id"] == section_id)
    return next(i for i in section["lessons"] if i["id"] == item_id) if item_id else section


def curriculum_preview(sections):
    """Expose curriculum metadata and explicit preview content without join credentials."""
    result = deepcopy(sections)
    for section in result:
        if isinstance(section.get("assessment"), dict):
            section["assessment"] = {**section["assessment"], "questions": []}
        for key in ("meeting_link", "join_url", "join_meta", "pass_code", "qr_code", "content_url", "content"):
            section[key] = None
        items = section.get("lessons", [])
        for item in items:
            preview = item.get("is_preview") and not item.get("is_draft")
            for key in ("meeting_link", "join_url", "join_meta", "pass_code", "qr_code"):
                item[key] = None
            if not preview:
                for key in ("content", "detail", "content_url", "video_url", "topics", "assignment", "videos", "documents", "notes"):
                    item[key] = None
            item["is_locked"] = not preview
            if item.get("assessment"):
                item["assessment"] = {**item["assessment"], "questions": []}
        section["items"] = deepcopy(items)
    return result


def apply_mode_to_response(result):
    mode = result.get("delivery_mode")
    venue_mode = mode in ("physical", "hybrid", "blended", "instructor_led")
    online_mode = mode in ("online", "hybrid", "blended", "instructor_led")
    if not venue_mode:
        result.update(venue=None, address=None, check_in=False, qr_payload=None, qr_image_base64=None, pass_code=None)
    if not online_mode:
        result.update(meeting_link=None, meeting_provider=None, meeting_id=None, meeting_passcode=None)
    for section in result.get("sections") or []:
        if mode in ("recorded", "self_paced"):
            section["schedule"] = None
        for item in section.get("lessons") or []:
            if not online_mode or item["type"] != "live":
                item.update(meeting_link=None, join_url=None, join_meta=None)
            if not venue_mode or item["type"] != "venue":
                item.update(venue=None, address=None, pass_code=None, qr_code=None)
        section["items"] = deepcopy(section.get("lessons") or [])
    return result


def learning_response(result, curriculum, training, enrolment, assignment_submissions):
    mode = training.delivery_mode
    venue_mode = mode in ("physical", "hybrid", "blended", "instructor_led")
    online_mode = mode in ("online", "hybrid", "blended", "instructor_led")
    recorded = mode in ("recorded", "self_paced")
    qr = enrolment.qr_code if enrolment and venue_mode else None
    source_sections = {s["id"]: s for s in curriculum["sections"]}
    flat = []
    for section in result["sections"]:
        source = source_sections[section["id"]]
        source_items = {item["id"]: item for item in source["lessons"]}
        section["schedule"] = None if recorded else section["schedule"]
        section["summary"] = source.get("summary") or section["summary"]
        section["venue"] = source.get("venue") if venue_mode else None
        section["address"] = source.get("address") if venue_mode else None
        section["meeting_link"] = source.get("meeting_link") if online_mode and section["is_unlocked"] else None
        for item in section["lessons"]:
            raw = source_items[item["id"]]
            kind, locked = item["type"], item["is_locked"]
            item["qr_code"] = qr if kind == "venue" and not locked else None
            item["schedule"] = None if recorded else raw.get("schedule") or section["schedule"]
            if kind == "venue" and venue_mode:
                item["venue"] = raw.get("venue") or source.get("venue") or getattr(training, "venue", None)
                item["address"] = raw.get("address") or source.get("address") or getattr(training, "address", None)
                section["venue"] = section["venue"] or item["venue"]
                section["address"] = section["address"] or item["address"]
            if kind != "venue" or not venue_mode:
                item.update(venue=None, address=None, pass_code=None, qr_code=None, check_in_window=None)
            if kind != "live" or not online_mode or locked:
                item.update(meeting_link=None, join_url=None, join_meta=None)
            elif not item.get("meeting_link"):
                link = source.get("meeting_link") or getattr(training, "meeting_link", None)
                item.update(meeting_link=link, join_url=link)
            if kind == "live" and not locked and online_mode:
                section["meeting_link"] = section["meeting_link"] or item.get("meeting_link")
            item.setdefault("assessment", None)
            item["assignment"] = None
            if kind == "assignment" and raw.get("assignment") and not locked:
                assignment = raw["assignment"]
                submitted = assignment_submissions.get(assignment["id"])
                item["assignment"] = {
                    "id": assignment["id"], "title": assignment.get("title"),
                    "kind": assignment.get("kind", "text"), "instructions": assignment.get("instructions"),
                    "due_at": assignment.get("due_at") or assignment.get("due_date"),
                    "max_score": assignment.get("max_score"), "is_submitted": submitted is not None,
                    "score": submitted.grade if submitted else None, "feedback": submitted.feedback if submitted else None,
                    "files": [dict(f) for f in (submitted.files or [])] if submitted else [],
                }
                item["completed_at"] = submitted.submitted_at.isoformat() if submitted else None
            flat.append(item)
        section["items"] = deepcopy(section["lessons"])
    result["qr_code"] = qr
    result["notes_pdf_url"] = getattr(training, "notes_pdf_url", None) or f"/api/v1/trainings/{training.id}/notes.pdf"
    documents = getattr(training, "documents", None)
    result["documents"] = [deepcopy(d) for d in documents if isinstance(d, dict) and d.get("visibility", "enrolled") in ("enrolled", "public")] if isinstance(documents, list) else []
    result["completed_items"] = sum(bool(i["is_completed"]) for i in flat)
    result["total_items"] = len(flat)
    required = [i for i in flat if i["is_mandatory"]]
    result["total_required_items"] = len(required)
    result["completed_required_items"] = sum(bool(i["is_completed"]) for i in required)
    if required:
        result["progress_percent"] = round(100 * result["completed_required_items"] / len(required), 2)
    else:
        result["progress_percent"] = round(100 * result["completed_items"] / len(flat), 2) if flat else 0
    result["assessments"] = [i["assessment"] for i in flat if i.get("assessment")]
    result["assignments"] = [i["assignment"] for i in flat if i.get("assignment")]
    return result
