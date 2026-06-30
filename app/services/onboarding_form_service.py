from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.onboarding_form_model import OnboardingForm
from app.repository.onboarding_form_repo import (
    count_sections_and_fields,
    create_form,
    deactivate_form,
    get_form_by_id,
    get_forms,
    replace_form_sections,
    save_form,
)
from app.repository.query_utils import build_pagination_meta
from app.schemas.onboarding_form_schema import (
    OPTION_FIELD_TYPES,
    OnboardingFieldInput,
    OnboardingFormCreate,
    OnboardingFormDuplicateResponse,
    OnboardingFormListItemResponse,
    OnboardingFormPaginatedResponse,
    OnboardingFormPublishResponse,
    OnboardingFormResponse,
    OnboardingFormUnpublishResponse,
    OnboardingFormUpdate,
    OnboardingFormUpdateResponse,
    OnboardingFieldResponse,
    OnboardingSectionInput,
    OnboardingSectionResponse,
)

ENTERPRISE_LOCKED_FIELD_KEYS = {
    "business_legal_name",
    "business_short_name",
}


def _validate_sections_structure(sections: list, entity_type: str) -> None:
    section_orders: set[int] = set()
    all_field_keys: set[str] = set()

    for section in sections:
        if section.order in section_orders:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Duplicate section order: {section.order}",
            )
        section_orders.add(section.order)

        field_orders: set[int] = set()
        for field in section.fields:
            if field.order in field_orders:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Duplicate field order {field.order} "
                        f"in section '{section.title}'"
                    ),
                )
            field_orders.add(field.order)

            if field.field_key in all_field_keys:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Duplicate field_key: {field.field_key}",
                )
            all_field_keys.add(field.field_key)

            if field.field_type in OPTION_FIELD_TYPES and not field.options:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Field '{field.field_key}' of type '{field.field_type}' "
                        "requires at least one option"
                    ),
                )

    if entity_type == "enterprise":
        missing_locked = ENTERPRISE_LOCKED_FIELD_KEYS - all_field_keys
        if missing_locked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Enterprise onboarding forms must include locked fields: "
                    + ", ".join(sorted(missing_locked))
                ),
            )

        for section in sections:
            for field in section.fields:
                if field.field_key in ENTERPRISE_LOCKED_FIELD_KEYS and not field.locked:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Field '{field.field_key}' must remain locked",
                    )


def _map_field(field) -> dict:
    options = field.options if isinstance(field.options, list) else []
    return {
        "id": field.id,
        "label": field.label,
        "field_key": field.field_key,
        "field_type": field.field_type,
        "placeholder": field.placeholder,
        "help_text": field.help_text,
        "required": field.required,
        "locked": field.locked,
        "visible": field.visible,
        "order": field.order,
        "options": [str(option) for option in options],
    }


def _map_section(section) -> dict:
    return {
        "id": section.id,
        "title": section.title,
        "order": section.order,
        "fields": [
            OnboardingFieldResponse.model_validate(_map_field(field)).model_dump()
            for field in sorted(section.fields, key=lambda item: item.order)
        ],
    }


def _map_form_detail(form: OnboardingForm) -> dict:
    return {
        "id": form.id,
        "name": form.name,
        "description": form.description,
        "entity_type": form.entity_type,
        "status": form.status,
        "sections": [
            OnboardingSectionResponse.model_validate(_map_section(section)).model_dump()
            for section in sorted(form.sections, key=lambda item: item.order)
        ],
        "created_at": form.created_at,
        "updated_at": form.updated_at,
        "published_at": form.published_at,
    }


def _map_form_list_item(form: OnboardingForm) -> dict:
    sections_count, fields_count = count_sections_and_fields(form)
    return {
        "id": form.id,
        "name": form.name,
        "description": form.description,
        "entity_type": form.entity_type,
        "status": form.status,
        "sections_count": sections_count,
        "fields_count": fields_count,
        "assigned_count": 0,
        "created_at": form.created_at,
        "updated_at": form.updated_at,
    }


def _get_form_or_404(db: Session, form_id: UUID) -> OnboardingForm:
    form = get_form_by_id(db, form_id, with_structure=True)
    if not form:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Onboarding form not found",
        )
    return form


def create_onboarding_form_service(db: Session, data: OnboardingFormCreate):
    _validate_sections_structure(data.sections, data.entity_type)

    form = OnboardingForm(
        name=data.name,
        description=data.description,
        entity_type=data.entity_type,
        status=data.status,
    )
    create_form(db, form)
    form = replace_form_sections(db, form, data.sections)
    return OnboardingFormResponse.model_validate(_map_form_detail(form))


def list_onboarding_forms_service(
    db: Session,
    *,
    entity_type: str | None = None,
    status_filter: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> OnboardingFormPaginatedResponse:
    items, total = get_forms(
        db,
        entity_type=entity_type,
        status=status_filter,
        search=search,
        page=page,
        page_size=page_size,
        include_inactive=status_filter == "inactive",
    )

    hydrated_items: list[OnboardingFormListItemResponse] = []
    for item in items:
        hydrated_items.append(
            OnboardingFormListItemResponse.model_validate(_map_form_list_item(item))
        )

    return OnboardingFormPaginatedResponse(
        items=hydrated_items,
        pagination=build_pagination_meta(total, page, page_size),
    )


def get_onboarding_form_service(db: Session, form_id: UUID):
    form = _get_form_or_404(db, form_id)
    return OnboardingFormResponse.model_validate(_map_form_detail(form))


def update_onboarding_form_service(
    db: Session,
    form_id: UUID,
    data: OnboardingFormUpdate,
):
    form = _get_form_or_404(db, form_id)
    _validate_sections_structure(data.sections, data.entity_type)

    form.name = data.name
    form.description = data.description
    form.entity_type = data.entity_type
    form.status = data.status
    form = replace_form_sections(db, form, data.sections)

    sections_count, fields_count = count_sections_and_fields(form)
    return OnboardingFormUpdateResponse(
        id=form.id,
        name=form.name,
        status=form.status,
        sections_count=sections_count,
        fields_count=fields_count,
        updated_at=form.updated_at,
    )


def publish_onboarding_form_service(db: Session, form_id: UUID):
    form = _get_form_or_404(db, form_id)
    if form.status == "inactive":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive onboarding forms cannot be published",
        )

    form.status = "published"
    form.published_at = datetime.utcnow()
    save_form(db, form)

    return OnboardingFormPublishResponse(
        id=form.id,
        status=form.status,
        published_at=form.published_at,
    )


def unpublish_onboarding_form_service(db: Session, form_id: UUID):
    form = _get_form_or_404(db, form_id)
    if form.status != "published":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only published onboarding forms can be unpublished",
        )

    form.status = "draft"
    save_form(db, form)

    return OnboardingFormUnpublishResponse(
        id=form.id,
        status=form.status,
        updated_at=form.updated_at,
    )


def duplicate_onboarding_form_service(
    db: Session,
    form_id: UUID,
    new_name: str,
):
    source = _get_form_or_404(db, form_id)

    duplicate_data = OnboardingFormCreate(
        name=new_name,
        description=source.description,
        entity_type=source.entity_type,
        status="draft",
        sections=[
            OnboardingSectionInput(
                title=section.title,
                order=section.order,
                fields=[
                    OnboardingFieldInput(
                        label=field.label,
                        field_key=field.field_key,
                        field_type=field.field_type,
                        placeholder=field.placeholder,
                        help_text=field.help_text,
                        required=field.required,
                        locked=field.locked,
                        visible=field.visible,
                        order=field.order,
                        options=field.options or [],
                    )
                    for field in sorted(section.fields, key=lambda item: item.order)
                ],
            )
            for section in sorted(source.sections, key=lambda item: item.order)
        ],
    )

    created = create_onboarding_form_service(db, duplicate_data)
    sections_count, fields_count = count_sections_and_fields(
        get_form_by_id(db, created.id, with_structure=True)
    )
    return OnboardingFormDuplicateResponse(
        id=created.id,
        name=created.name,
        status=created.status,
        sections_count=sections_count,
        fields_count=fields_count,
        created_at=created.created_at,
    )


def delete_onboarding_form_service(db: Session, form_id: UUID):
    form = _get_form_or_404(db, form_id)
    form = deactivate_form(db, form)
    return {
        "id": form.id,
        "status": form.status,
        "message": "Onboarding form deactivated successfully.",
    }
