from fastapi import (
    APIRouter,
    Depends,
    status
)

from sqlalchemy.orm import Session

from app.db.database import get_db

from app.services.system_service import (
    health_check_service,
    inventory_service
)

router = APIRouter(
    tags=["System"]
)


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="System Health Check",
    description="""
Check the health status of the application and database connection.

This endpoint is commonly used by:
- Monitoring tools
- Load balancers
- DevOps health checks
- Kubernetes readiness/liveness probes

Returns the current operational status of the system.
""",
    responses={
        200: {"description": "System is healthy"},
        500: {"description": "Database or system unavailable"}
    }
)
def health_check(
    db: Session = Depends(get_db)
):
    return health_check_service(db)


@router.get(
    "/inventory",
    status_code=status.HTTP_200_OK,
    summary="Inventory Summary",
    description="""
Retrieve inventory statistics for the marketplace.

The response may include:
- Total enterprises
- Total products
- Total services
- Active records
- Inactive records

Useful for administrative dashboards and reporting.
""",
    responses={
        200: {"description": "Inventory summary retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
def inventory():
    return inventory_service()