"""Event and Training forms in the common form contract (docs/form-builder-api.md): read-only views, additive to the existing builders.

Registered before the Events and Trainings routers. Nothing here changes what is stored or what the existing routes return.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy.orm import Session

from app.form_builder.concurrency import ETagRoute
from app.core.dependencies import extract_access_token, get_current_user, get_optional_current_user, require_event_form_builder_admin
from app.db.database import get_db
from app.repository import event_repo, training_repo
from app.schemas.form_common_schema import CommonFormView, RecordFormView
from app.services import form_common_view as views

router = APIRouter(route_class=ETagRoute)

BUILDER_DOC = ("The Super Admin's form builder view in the common contract: every section and field of the draft (or of `version_id`, or the latest published version), including switched-off ones, "
               "with explicit field types, renderers, visibility and protected (`system`) flags. Same auth as the existing form configuration routes.")
ACTIVE_DOC = ("The form the signed-in Enterprise Admin writes with, in the common contract: enabled fields only, each with type, renderer, placeholder, help text, required flag, rules, options and visibility. "
              "Resolved like the existing `/form-configuration/active`.")


@router.get("/events/form-configuration/admin/{config_id}/common", response_model=CommonFormView, tags=["Event Form Configuration (Super Admin)"],
            summary="An Event form in the common contract (builder view)", description=BUILDER_DOC)
def event_builder_view(config_id: UUID = Path(...), version_id: UUID | None = Query(None), db: Session = Depends(get_db), _: dict = Depends(require_event_form_builder_admin)):
    return views.builder_view(db, "event", config_id, version_id)


PUBLIC_DOC = ("No sign-in needed. The active **published** form for public and mobile apps: the one assigned to `enterprise_id` if there is one, else the active global form (404 when none). Only enabled, `published` "
              "fields and sections, each with id, key, label, type, renderer, order and options: no placeholders, rules, help text or Enterprise Admin only content.")


@router.get("/events/form-configuration/public/active", response_model=CommonFormView, tags=["Events"], summary="The active published Event form (public, mobile)", description=PUBLIC_DOC)
def event_public_active(enterprise_id: UUID | None = Query(None), db: Session = Depends(get_db)):
    return views.public_active_view(db, "event", enterprise_id)


@router.get("/trainings/form-configuration/public/active", response_model=CommonFormView, tags=["Trainings"], summary="The active published Training form (public, mobile)", description=PUBLIC_DOC)
def training_public_active(enterprise_id: UUID | None = Query(None), db: Session = Depends(get_db)):
    return views.public_active_view(db, "training", enterprise_id)


@router.get("/events/form-configuration/active/common", response_model=CommonFormView, tags=["Events"],
            summary="The active Event form in the common contract (Enterprise Admin view)", description=ACTIVE_DOC)
def event_active_view(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
    return views.active_view(db, "event", user, extract_access_token(request))


@router.get("/events/{event_id}/form/common", response_model=RecordFormView, tags=["Events"],
            summary="The form an event was written with, and its custom values, for the caller",
            description=("Visible to whoever may see the event (published events are public). The answer depends on who asks: the public and mobile apps get only enabled `published` fields and their values; "
                         "staff of the event's tenant get the Enterprise Admin's view (including `enterprise_admin_only` fields and values); a Platform Super Admin gets everything. "
                         "`custom_fields` are the event's custom values with their label, explicit type and renderer. The event's built-in fields are in the event itself."))
def event_form_view(request: Request, event_id: UUID = Path(...), db: Session = Depends(get_db), user: dict | None = Depends(get_optional_current_user)):
    token = extract_access_token(request)
    event = event_repo.get_event_by_id(db, event_id)
    viewer = event_repo.build_event_viewer(db, user, access_token=token)
    if event is None or not event_repo.can_view_event(db, event, viewer, access_token=token):
        raise HTTPException(status_code=404, detail="Event not found")
    audience = "super_admin" if viewer.is_platform else "enterprise_admin" if event_repo.viewer_owns_event(event, viewer) else "public"
    return views.record_view(db, "event", event, audience, user)


@router.get("/trainings/form-configuration/admin/{config_id}/common", response_model=CommonFormView, tags=["Training Form Configuration (Super Admin)"],
            summary="A Training form in the common contract (builder view)", description=BUILDER_DOC)
def training_builder_view(config_id: UUID = Path(...), version_id: UUID | None = Query(None), db: Session = Depends(get_db), _: dict = Depends(require_event_form_builder_admin)):
    return views.builder_view(db, "training", config_id, version_id)


@router.get("/trainings/form-configuration/active/common", response_model=CommonFormView, tags=["Trainings"],
            summary="The active Training form in the common contract (Enterprise Admin view)", description=ACTIVE_DOC)
def training_active_view(request: Request, db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
    return views.active_view(db, "training", user, extract_access_token(request))


@router.get("/trainings/{training_id}/form/common", response_model=RecordFormView, tags=["Trainings"],
            summary="The form a training was written with, and its custom values, for the caller",
            description=("Signed in. Staff of the training's tenant get the Enterprise Admin's view, a Platform Super Admin everything, everyone else the public view of a **published** training "
                         "(404 for any other). See the event equivalent for the fields."))
def training_form_view(request: Request, training_id: UUID = Path(...), db: Session = Depends(get_db), user: dict = Depends(get_current_user)):
    token = extract_access_token(request)
    training = training_repo.get_training_by_id(db, training_id)
    if training is None:
        raise HTTPException(status_code=404, detail="Training not found")
    audience = "public"
    try:
        training_repo.require_training_owner(db, training_id, user, access_token=token)
        audience = "super_admin" if user.get("role") == "super_admin" else "enterprise_admin"
    except HTTPException:
        if training.status != "published":
            raise HTTPException(status_code=404, detail="Training not found")
    return views.record_view(db, "training", training, audience, user)


# ---------------------------------------------------------------- building (write side), the same routes for Events and Trainings

from fastapi import Body, status  # noqa: E402

from app.form_builder.fields import describe_types  # noqa: E402
from app.schemas.form_common_schema import (  # noqa: E402
    CommonFieldCreate, CommonFieldEdit, CommonFieldMove, CommonFormChange, CommonOrder, CommonReview, CommonSectionCreate, CommonSectionEdit, CommonSubmit,
)
from app.services import form_common_write as write  # noqa: E402

BUILD_DOC = (" Changes the **draft** (a published configuration forks a new draft first); events and trainings keep the version they were made with until it is published. "
             "Send `base_etag` (the `etag` of the builder view you loaded) to refuse a change over someone else's: 409 `VERSION_CONFLICT` with `current_etag`. Every change withdraws a submitted review and is written to the "
             "configuration's audit log (`GET .../{config_id}/audit`, action `common_<action>`). Same auth as the existing form configuration routes.")


def _register_builder(module: str, plural: str, tag: str) -> None:
    base = f"/{plural}/form-configuration/admin/{{config_id}}/common"
    singular = module.capitalize()

    def route(method, path, **kw):
        return getattr(router, method)(base + path, tags=[tag], **kw)

    router.get(f"/{plural}/form-configuration/admin/common/diagnostics", tags=[tag], summary=f"{singular} form builder diagnostics",
               description="Numbers for monitoring and for checking a migration or a rollback: configurations by status, active ones, drafts submitted for review, versions with Enterprise Admin only fields, records without a form version.")(
        lambda db=Depends(get_db), _=Depends(require_event_form_builder_admin): write.diagnostics(db, module))

    @route("get", "/field-types", response_model=list[dict], summary=f"The field types a {singular} form can have",
           description="Each type with its renderer, whether it takes options, the file types it can be limited to and every validation rule it accepts. Includes the module types `datetime` and `toggle`.")
    def field_types(config_id: UUID = Path(...), _: dict = Depends(require_event_form_builder_admin)):
        return describe_types(include_module_types=True)

    @route("post", "/sections", response_model=CommonFormChange, status_code=status.HTTP_201_CREATED, summary=f"Add a section to a {singular} form", description="At `position` or at the end." + BUILD_DOC)
    def add_section(config_id: UUID = Path(...), payload: CommonSectionCreate = Body(...), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.add_section(db, module, config_id, user, payload.base_etag, title=payload.title, description=payload.description, position=payload.position, visibility=payload.visibility)

    @route("put", "/sections/order", response_model=CommonFormChange, summary="Reorder the sections", description="Send every section id once, in the new order (422 `INVALID_ORDER`)." + BUILD_DOC)
    def reorder_sections(config_id: UUID = Path(...), payload: CommonOrder = Body(...), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.reorder_sections(db, module, config_id, user, payload.base_etag, payload.ids)

    @route("patch", "/sections/{section_id}", response_model=CommonFormChange, summary="Edit a section's title or description", description=BUILD_DOC.strip())
    def edit_section(section_id: str, config_id: UUID = Path(...), payload: CommonSectionEdit = Body(...), db: Session = Depends(get_db),
                     user: dict = Depends(require_event_form_builder_admin)):
        return write.edit_section(db, module, config_id, user, payload.base_etag, section_id, title=payload.title, description=payload.description, visibility=payload.visibility, enabled=payload.enabled)

    @route("delete", "/sections/{section_id}", response_model=CommonFormChange, summary="Delete a section",
           description="An empty section is deleted. One with fields needs `move_to` (409 `SECTION_NOT_EMPTY`, or `SECTION_HAS_PROTECTED_FIELDS` when it holds a protected field). The last section cannot be deleted." + BUILD_DOC)
    def delete_section(section_id: str, config_id: UUID = Path(...), move_to: str | None = Query(None), base_etag: str | None = Query(None), db: Session = Depends(get_db),
                       user: dict = Depends(require_event_form_builder_admin)):
        return write.delete_section(db, module, config_id, user, base_etag, section_id, move_to=move_to)

    @route("post", "/fields", response_model=CommonFormChange, status_code=status.HTTP_201_CREATED, summary=f"Add a field to a {singular} form",
           description="A custom field with an explicit `type` (short_text, rich_text, long_text, dropdown, radio, checkbox, number, date, time, reference_url, additional_images, supporting_document, video, tags, "
                       "datetime, toggle), its `renderer`, validation, options and `visibility` (`published` or `enterprise_admin_only`). `change.field_id` is the new field. 409 `DUPLICATE_KEY`, `DUPLICATE_LABEL` "
                       "(in the section), `KEY_RESERVED` (a built-in field's key)." + BUILD_DOC)
    def add_field(config_id: UUID = Path(...), payload: CommonFieldCreate = Body(...), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.add_field(db, module, config_id, user, payload.base_etag, section_id=payload.section_id, field=payload.field, position=payload.position)

    @route("patch", "/fields/{field_id}", response_model=CommonFormChange, summary="Edit a field",
           description="`patch` has only what changes. A custom field: label, placeholder, help_text, required, enabled, validation, options, visibility (key and type never change: `KEY_IMMUTABLE`, `TYPE_IMMUTABLE`). "
                       "A built-in field: label, placeholder, help_text, required, enabled only (409 `CORE_FIELD` otherwise); a protected one cannot be switched off or made optional (409 `PROTECTED_FIELD`)." + BUILD_DOC)
    def edit_field(field_id: str, config_id: UUID = Path(...), payload: CommonFieldEdit = Body(...), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.edit_field(db, module, config_id, user, payload.base_etag, field_id, payload.patch)

    @route("delete", "/fields/{field_id}", response_model=CommonFormChange, summary="Delete a custom field",
           description="A built-in field cannot be deleted (409 `CORE_FIELD`: switch it off; `PROTECTED_FIELD` for a domain-required one). Values already saved for the field stay with the records but are no longer shown or checked." + BUILD_DOC)
    def delete_field(field_id: str, config_id: UUID = Path(...), base_etag: str | None = Query(None), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.delete_field(db, module, config_id, user, base_etag, field_id)

    @route("post", "/fields/{field_id}/duplicate", response_model=CommonFormChange, status_code=status.HTTP_201_CREATED, summary="Duplicate a custom field",
           description="A copy right after the original: key `<key>_copy`, label `<label> (copy)`, new id. A built-in field cannot be duplicated." + BUILD_DOC)
    def duplicate_field(field_id: str, config_id: UUID = Path(...), base_etag: str | None = Query(None), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.duplicate_field(db, module, config_id, user, base_etag, field_id)

    @route("post", "/fields/{field_id}/move", response_model=CommonFormChange, summary="Move a field to another section", description="To `section_id` at `position` (1-based) or the end." + BUILD_DOC)
    def move_field(field_id: str, config_id: UUID = Path(...), payload: CommonFieldMove = Body(...), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.move_field(db, module, config_id, user, payload.base_etag, field_id, section_id=payload.section_id, position=payload.position)

    @route("put", "/sections/{section_id}/fields/order", response_model=CommonFormChange, summary="Reorder the fields of a section", description="Send every field id of the section once." + BUILD_DOC)
    def reorder_fields(section_id: str, config_id: UUID = Path(...), payload: CommonOrder = Body(...), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.reorder_fields(db, module, config_id, user, payload.base_etag, section_id, payload.ids)

    @route("get", "/review", response_model=CommonReview, summary="Review the draft before publishing",
           description="Every problem the publish check finds (`problems`), what the draft changes against the published version (`changes`: fields added, removed, changed), and whether it was submitted. Nothing is saved.")
    def review(config_id: UUID = Path(...), db: Session = Depends(get_db), _: dict = Depends(require_event_form_builder_admin)):
        return write.review(db, module, config_id)

    @route("post", "/submit", response_model=CommonReview, summary="Submit the reviewed draft for publishing",
           description="The review checkpoint (422 `FORM_NOT_READY` with `errors` if the draft has problems). Any edit afterwards withdraws it. With FORM_REQUIRE_REVIEW on, the existing publish route needs a submitted draft "
                       "(409 `REVIEW_REQUIRED`); with it off (the default) submitting is optional.")
    def submit(config_id: UUID = Path(...), payload: CommonSubmit = Body(default_factory=CommonSubmit), db: Session = Depends(get_db), user: dict = Depends(require_event_form_builder_admin)):
        return write.submit(db, module, config_id, user, payload.base_etag)


_register_builder("event", "events", "Event Form Configuration (Super Admin)")
_register_builder("training", "trainings", "Training Form Configuration (Super Admin)")
