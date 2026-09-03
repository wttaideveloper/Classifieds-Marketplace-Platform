from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_super_admin
from app.db.database import get_db
from app.schemas.event_form_config_schema import (
    ActivationResponse,
    AssignmentPutRequest,
    AssignmentResponse,
    EventFormAuditEntry,
    EventFormConfigurationCreate,
    EventFormConfigurationCreateResponse,
    EventFormConfigurationDetail,
    EventFormConfigurationSummary,
    EventFormConfigurationUpdate,
    ConfigurationVersionResponse,
    FieldRegistryEntry,
    PublishConfigurationResponse,
)
from app.services.event_form_config_service import (
    activate_configuration_service,
    create_configuration_service,
    deactivate_configuration_service,
    delete_configuration_service,
    get_configuration_service,
    get_version_service,
    list_assignments_service,
    list_audit_service,
    list_configurations_service,
    list_versions_service,
    publish_configuration_service,
    put_assignments_service,
    retire_configuration_service,
    update_configuration_service,
)
from app.services.event_form_registry import get_field_registry

router = APIRouter(tags=["Event Form Configuration (Super Admin)"])


@router.get(
    "/event-form-configurations/field-registry",
    response_model=list[FieldRegistryEntry],
    summary="Authoritative Event core field registry",
    description="Super Admin selects existing Event backend fields when building forms. Core keys are immutable.",
)
def field_registry(_: dict = Depends(get_current_super_admin)):
    return get_field_registry()


@router.get(
    "/event-form-configurations",
    response_model=list[EventFormConfigurationSummary],
    summary="List Event form configurations",
)
def list_configurations(db: Session = Depends(get_db), _: dict = Depends(get_current_super_admin)):
    return list_configurations_service(db)


@router.post(
    "/event-form-configurations",
    response_model=EventFormConfigurationCreateResponse,
    status_code=201,
    summary="Create Event form configuration (draft v1)",
)
def create_configuration(
    payload: EventFormConfigurationCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return create_configuration_service(db, payload, current_user)


@router.get(
    "/event-form-configurations/{config_id}",
    response_model=EventFormConfigurationDetail,
    summary="Get configuration with draft/published versions",
)
def get_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_super_admin),
):
    return get_configuration_service(db, config_id)


@router.patch(
    "/event-form-configurations/{config_id}",
    response_model=EventFormConfigurationDetail,
    summary="Update draft configuration (forks new draft if editing published)",
)
def update_configuration(
    payload: EventFormConfigurationUpdate,
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return update_configuration_service(db, config_id, payload, current_user)


@router.delete(
    "/event-form-configurations/{config_id}",
    summary="Delete unused draft configuration",
)
def delete_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return delete_configuration_service(db, config_id, current_user)


@router.get(
    "/event-form-configurations/{config_id}/versions",
    response_model=list[ConfigurationVersionResponse],
    summary="List all configuration versions",
)
def list_versions(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_super_admin),
):
    return list_versions_service(db, config_id)


@router.get(
    "/event-form-configurations/{config_id}/versions/{version_id}",
    response_model=ConfigurationVersionResponse,
    summary="Get specific configuration version (immutable if published)",
)
def get_version(
    config_id: UUID = Path(...),
    version_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_super_admin),
):
    return get_version_service(db, config_id, version_id)


@router.post(
    "/event-form-configurations/{config_id}/publish",
    response_model=PublishConfigurationResponse,
    summary="Publish draft version (validates domain + renderer rules)",
)
def publish_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return publish_configuration_service(db, config_id, current_user)


@router.post(
    "/event-form-configurations/{config_id}/activate",
    response_model=ActivationResponse,
    summary="Activate published configuration",
)
def activate_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return activate_configuration_service(db, config_id, current_user)


@router.post(
    "/event-form-configurations/{config_id}/deactivate",
    response_model=ActivationResponse,
    summary="Deactivate configuration (selective tenants fall back to global)",
)
def deactivate_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return deactivate_configuration_service(db, config_id, current_user)


@router.post(
    "/event-form-configurations/{config_id}/retire",
    summary="Retire configuration (no new Events; historical versions retained)",
)
def retire_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return retire_configuration_service(db, config_id, current_user)


@router.get(
    "/event-form-configurations/{config_id}/assignments",
    response_model=AssignmentResponse,
    summary="List tenant assignments (selective configurations)",
)
def get_assignments(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_super_admin),
):
    return list_assignments_service(db, config_id)


@router.put(
    "/event-form-configurations/{config_id}/assignments",
    response_model=AssignmentResponse,
    summary="Replace tenant/enterprise assignments",
    description="Canonical assignment key is tenant_id. enterprise_id is validated against Enterprise↔Tenant linkage.",
)
def put_assignments(
    payload: AssignmentPutRequest,
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_super_admin),
):
    return put_assignments_service(db, config_id, payload, current_user)


@router.get(
    "/event-form-configurations/{config_id}/audit",
    response_model=list[EventFormAuditEntry],
    summary="Configuration audit history",
)
def list_audit(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = Depends(get_current_super_admin),
):
    return list_audit_service(db, config_id)
