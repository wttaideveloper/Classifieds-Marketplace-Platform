from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_admin
from app.db.database import get_db
from app.schemas.health_schema import (
    AlertConfigCreate,
    AlertConfigResponse,
    ApiHealthResponse,
    ErrorSummaryResponse,
    HealthCheckResponse,
    MetricsResponse,
)
from app.services.health_service import (
    create_alert_config_service,
    get_api_health_service,
    get_database_health_service,
    get_error_summary_service,
    get_metrics_service,
    get_queue_health_service,
    get_system_health_service,
)

router = APIRouter(dependencies=[Depends(get_current_admin)])


@router.get("/system", response_model=HealthCheckResponse, status_code=status.HTTP_200_OK)
def get_system_health(db: Session = Depends(get_db)):
    return get_system_health_service(db=db)


@router.get("/database", response_model=HealthCheckResponse, status_code=status.HTTP_200_OK)
def get_database_health(db: Session = Depends(get_db)):
    return get_database_health_service(db=db)


@router.get("/apis", response_model=ApiHealthResponse, status_code=status.HTTP_200_OK)
def get_api_health(db: Session = Depends(get_db)):
    return get_api_health_service(db=db)


@router.get("/queues", response_model=HealthCheckResponse, status_code=status.HTTP_200_OK)
def get_queue_health(db: Session = Depends(get_db)):
    return get_queue_health_service(db=db)


@router.get("/errors", response_model=ErrorSummaryResponse, status_code=status.HTTP_200_OK)
def get_error_summary(db: Session = Depends(get_db)):
    return get_error_summary_service(db=db)


@router.post("/alerts/config", response_model=AlertConfigResponse, status_code=status.HTTP_201_CREATED)
def create_alert_config(payload: AlertConfigCreate, db: Session = Depends(get_db)):
    return create_alert_config_service(db=db, payload=payload)


@router.get("/metrics", response_model=MetricsResponse, status_code=status.HTTP_200_OK)
def get_metrics(db: Session = Depends(get_db)):
    return get_metrics_service(db=db)
