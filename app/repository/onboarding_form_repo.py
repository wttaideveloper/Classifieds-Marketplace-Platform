from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.onboarding_form_model import (
    OnboardingForm,
    OnboardingFormField,
    OnboardingFormSection,
)
from app.repository.query_utils import (
    apply_ilike_search,
    apply_soft_delete_filter,
)


def _touch_form(form: OnboardingForm) -> None:
    form.updated_at = datetime.utcnow()


def create_form(db: Session, form: OnboardingForm) -> OnboardingForm:
    db.add(form)
    db.commit()
    db.refresh(form)
    return form


def get_forms(
    db: Session,
    *,
    entity_type: str | None = None,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 20,
    include_inactive: bool = False,
):
    query = db.query(OnboardingForm)
    query = apply_soft_delete_filter(query, OnboardingForm, False)

    if entity_type:
        query = query.filter(OnboardingForm.entity_type == entity_type)
    if status:
        query = query.filter(OnboardingForm.status == status)
    elif not include_inactive:
        query = query.filter(OnboardingForm.status.in_(["draft", "published"]))

    if search:
        query = apply_ilike_search(
            query,
            [OnboardingForm.name, OnboardingForm.description],
            search,
        )

    query = query.order_by(OnboardingForm.updated_at.desc())
    total = query.count()
    items = (
        query.options(
            joinedload(OnboardingForm.sections).joinedload(OnboardingFormSection.fields),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_form_by_id(
    db: Session,
    form_id: UUID,
    *,
    include_deleted: bool = False,
    with_structure: bool = True,
) -> OnboardingForm | None:
    query = db.query(OnboardingForm).filter(OnboardingForm.id == form_id)
    if not include_deleted:
        query = apply_soft_delete_filter(query, OnboardingForm, False)

    if with_structure:
        query = query.options(
            joinedload(OnboardingForm.sections).joinedload(OnboardingFormSection.fields),
        )

    return query.first()


def save_form(db: Session, form: OnboardingForm) -> OnboardingForm:
    _touch_form(form)
    db.commit()
    db.refresh(form)
    return form


def replace_form_sections(
    db: Session,
    form: OnboardingForm,
    sections_data: list,
) -> OnboardingForm:
    form.sections.clear()
    db.flush()

    for section_data in sections_data:
        section = OnboardingFormSection(
            form_id=form.id,
            title=section_data.title,
            order=section_data.order,
        )
        for field_data in section_data.fields:
            section.fields.append(
                OnboardingFormField(
                    label=field_data.label,
                    field_key=field_data.field_key,
                    field_type=field_data.field_type,
                    placeholder=field_data.placeholder,
                    help_text=field_data.help_text,
                    required=field_data.required,
                    locked=field_data.locked,
                    visible=field_data.visible,
                    order=field_data.order,
                    options=field_data.options,
                )
            )
        form.sections.append(section)

    _touch_form(form)
    db.commit()
    db.refresh(form)
    return get_form_by_id(db, form.id)


def deactivate_form(db: Session, form: OnboardingForm) -> OnboardingForm:
    form.status = "inactive"
    form.is_deleted = True
    _touch_form(form)
    db.commit()
    db.refresh(form)
    return form


def count_sections_and_fields(form: OnboardingForm) -> tuple[int, int]:
    sections_count = len(form.sections)
    fields_count = sum(len(section.fields) for section in form.sections)
    return sections_count, fields_count
