from uuid import UUID

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.core.dependencies import require_event_form_builder_admin
from app.db.database import get_db
from app.schemas.training_form_config_schema import (
    ActivationResponse,
    AssignmentPutRequest,
    AssignmentResponse,
    TrainingFormAuditEntry,
    TrainingFormConfigurationCreate,
    TrainingFormConfigurationCreateResponse,
    TrainingFormConfigurationDetail,
    TrainingFormConfigurationSummary,
    TrainingFormConfigurationUpdate,
    ConfigurationVersionResponse,
    FieldRegistryEntry,
    PublishConfigurationResponse,
)
from app.services.training_form_config_service import (
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
from app.services.training_form_registry import get_field_registry

router = APIRouter(tags=["Training Form Configuration (Super Admin)"])

_BUILDER_AUTH = Depends(require_event_form_builder_admin)


@router.get(
    "/field-registry",
    response_model=list[FieldRegistryEntry],
    summary="Authoritative Training core field registry",
)
def field_registry(_: dict = _BUILDER_AUTH):
    return get_field_registry()


@router.get(
    "/",
    response_model=list[TrainingFormConfigurationSummary],
    summary="List Training form configurations",
)
def list_configurations(db: Session = Depends(get_db), _: dict = _BUILDER_AUTH):
    return list_configurations_service(db)


@router.post(
    "/",
    response_model=TrainingFormConfigurationCreateResponse,
    status_code=201,
    summary="Create Training form configuration (draft v1)",
)
def create_configuration(
    payload: TrainingFormConfigurationCreate,
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return create_configuration_service(db, payload, current_user)


@router.get(
    "/{config_id}",
    response_model=TrainingFormConfigurationDetail,
    summary="Get configuration with draft/published versions",
)
def get_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = _BUILDER_AUTH,
):
    return get_configuration_service(db, config_id)


@router.patch(
    "/{config_id}",
    response_model=TrainingFormConfigurationDetail,
    summary="Update draft configuration (forks new draft if editing published)",
)
def update_configuration(
    payload: TrainingFormConfigurationUpdate,
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return update_configuration_service(db, config_id, payload, current_user)


@router.delete(
    "/{config_id}",
    summary="Delete unused draft configuration",
)
def delete_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return delete_configuration_service(db, config_id, current_user)


@router.get(
    "/{config_id}/versions",
    response_model=list[ConfigurationVersionResponse],
    summary="List all configuration versions",
)
def list_versions(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = _BUILDER_AUTH,
):
    return list_versions_service(db, config_id)


@router.get(
    "/{config_id}/versions/{version_id}",
    response_model=ConfigurationVersionResponse,
    summary="Get specific configuration version",
)
def get_version(
    config_id: UUID = Path(...),
    version_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = _BUILDER_AUTH,
):
    return get_version_service(db, config_id, version_id)


@router.post(
    "/{config_id}/publish",
    response_model=PublishConfigurationResponse,
    summary="Publish draft version",
)
def publish_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return publish_configuration_service(db, config_id, current_user)


@router.post(
    "/{config_id}/activate",
    response_model=ActivationResponse,
    summary="Activate published configuration",
)
def activate_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return activate_configuration_service(db, config_id, current_user)


@router.post(
    "/{config_id}/deactivate",
    response_model=ActivationResponse,
    summary="Deactivate configuration",
)
def deactivate_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return deactivate_configuration_service(db, config_id, current_user)


@router.post(
    "/{config_id}/retire",
    summary="Retire configuration",
)
def retire_configuration(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return retire_configuration_service(db, config_id, current_user)


@router.get(
    "/{config_id}/assignments",
    response_model=AssignmentResponse,
    summary="List enterprise/tenant assignments",
)
def get_assignments(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = _BUILDER_AUTH,
):
    return list_assignments_service(db, config_id)


@router.put(
    "/{config_id}/assignments",
    response_model=AssignmentResponse,
    summary="Replace assignments",
    description=(
        "PUT {enterprise_ids: []} makes the config global. "
        "Non-empty enterprise_ids (or tenant_ids / tenant_slugs / assignments) makes it selective."
    ),
)
def put_assignments(
    payload: AssignmentPutRequest,
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    current_user: dict = _BUILDER_AUTH,
):
    return put_assignments_service(db, config_id, payload, current_user)


@router.get(
    "/{config_id}/audit",
    response_model=list[TrainingFormAuditEntry],
    summary="Configuration audit history",
)
def list_audit(
    config_id: UUID = Path(...),
    db: Session = Depends(get_db),
    _: dict = _BUILDER_AUTH,
):
    return list_audit_service(db, config_id)
