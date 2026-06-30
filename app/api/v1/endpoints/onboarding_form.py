from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.common_schema import DEFAULT_PAGE, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.onboarding_form_schema import (
    OnboardingFormCreate,
    OnboardingFormDeleteResponse,
    OnboardingFormDuplicateRequest,
    OnboardingFormDuplicateResponse,
    OnboardingFormPaginatedResponse,
    OnboardingFormPublishRequest,
    OnboardingFormPublishResponse,
    OnboardingFormResponse,
    OnboardingFormUnpublishRequest,
    OnboardingFormUnpublishResponse,
    OnboardingFormUpdate,
    OnboardingFormUpdateResponse,
)
from app.services.onboarding_form_service import (
    create_onboarding_form_service,
    delete_onboarding_form_service,
    duplicate_onboarding_form_service,
    get_onboarding_form_service,
    list_onboarding_forms_service,
    publish_onboarding_form_service,
    unpublish_onboarding_form_service,
    update_onboarding_form_service,
)

router = APIRouter(tags=["Onboarding Forms"])


@router.post(
    "/",
    response_model=OnboardingFormResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Onboarding Form Template",
)
def create_onboarding_form(
    payload: OnboardingFormCreate = Body(...),
    db: Session = Depends(get_db),
):
    return create_onboarding_form_service(db, payload)


@router.get(
    "/",
    response_model=OnboardingFormPaginatedResponse,
    status_code=status.HTTP_200_OK,
    summary="List Onboarding Form Templates",
)
def list_onboarding_forms(
    entity_type: str | None = Query(None, description="Filter by entity type."),
    status_filter: str | None = Query(None, alias="status", description="Filter by status."),
    search: str | None = Query(None, description="Search by form name or description."),
    page: int = Query(DEFAULT_PAGE, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: Session = Depends(get_db),
):
    return list_onboarding_forms_service(
        db,
        entity_type=entity_type,
        status_filter=status_filter,
        search=search,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{form_id}",
    response_model=OnboardingFormResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Onboarding Form Template By ID",
)
def get_onboarding_form(
    form_id: UUID = Path(..., description="Onboarding form identifier"),
    db: Session = Depends(get_db),
):
    return get_onboarding_form_service(db, form_id)


@router.put(
    "/{form_id}",
    response_model=OnboardingFormUpdateResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Onboarding Form Template",
)
def update_onboarding_form(
    payload: OnboardingFormUpdate,
    form_id: UUID = Path(..., description="Onboarding form identifier"),
    db: Session = Depends(get_db),
):
    return update_onboarding_form_service(db, form_id, payload)


@router.put(
    "/{form_id}/publish",
    response_model=OnboardingFormPublishResponse,
    status_code=status.HTTP_200_OK,
    summary="Publish Onboarding Form Template",
)
def publish_onboarding_form(
    payload: OnboardingFormPublishRequest = Body(default_factory=OnboardingFormPublishRequest),
    form_id: UUID = Path(..., description="Onboarding form identifier"),
    db: Session = Depends(get_db),
):
    return publish_onboarding_form_service(db, form_id)


@router.put(
    "/{form_id}/unpublish",
    response_model=OnboardingFormUnpublishResponse,
    status_code=status.HTTP_200_OK,
    summary="Unpublish Onboarding Form Template",
)
def unpublish_onboarding_form(
    payload: OnboardingFormUnpublishRequest = Body(default_factory=OnboardingFormUnpublishRequest),
    form_id: UUID = Path(..., description="Onboarding form identifier"),
    db: Session = Depends(get_db),
):
    return unpublish_onboarding_form_service(db, form_id)


@router.post(
    "/{form_id}/duplicate",
    response_model=OnboardingFormDuplicateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate Onboarding Form Template",
)
def duplicate_onboarding_form(
    payload: OnboardingFormDuplicateRequest,
    form_id: UUID = Path(..., description="Onboarding form identifier"),
    db: Session = Depends(get_db),
):
    return duplicate_onboarding_form_service(db, form_id, payload.name)


@router.delete(
    "/{form_id}",
    response_model=OnboardingFormDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Deactivate Onboarding Form Template",
)
def delete_onboarding_form(
    form_id: UUID = Path(..., description="Onboarding form identifier"),
    db: Session = Depends(get_db),
):
    return delete_onboarding_form_service(db, form_id)
