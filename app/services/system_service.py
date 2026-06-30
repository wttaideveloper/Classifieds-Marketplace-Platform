from sqlalchemy import text
from sqlalchemy.orm import Session
from fastapi import HTTPException
from fastapi import status


def health_check_service(db: Session):
    try:
        db.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "database": "connected",
            "message": "Application is running successfully"
        }

    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed"
        )


def inventory_service():
    return {
        "total_apis": 41,
        "apis": [
            {"method": "POST", "endpoint": "/api/v1/enterprises", "description": "Create Enterprise"},
            {"method": "GET", "endpoint": "/api/v1/enterprises", "description": "Get Enterprise List"},
            {"method": "GET", "endpoint": "/api/v1/enterprises/{id}", "description": "Get Enterprise Details"},
            {"method": "PUT", "endpoint": "/api/v1/enterprises/{id}", "description": "Update Enterprise"},
            {"method": "DELETE", "endpoint": "/api/v1/enterprises/{id}", "description": "Soft Delete Enterprise"},
            {"method": "POST", "endpoint": "/api/v1/enterprises/{id}/locations", "description": "Create Location"},
            {"method": "GET", "endpoint": "/api/v1/enterprises/{id}/locations", "description": "Get Enterprise Locations"},
            {"method": "GET", "endpoint": "/api/v1/locations/{id}", "description": "Get Location Details"},
            {"method": "PUT", "endpoint": "/api/v1/locations/{id}", "description": "Update Location"},
            {"method": "DELETE", "endpoint": "/api/v1/locations/{id}", "description": "Soft Delete Location"},
            {"method": "POST", "endpoint": "/api/v1/products", "description": "Create Product"},
            {"method": "GET", "endpoint": "/api/v1/products", "description": "Get Product List"},
            {"method": "GET", "endpoint": "/api/v1/products/{id}", "description": "Get Product Details"},
            {"method": "PUT", "endpoint": "/api/v1/products/{id}", "description": "Update Product"},
            {"method": "DELETE", "endpoint": "/api/v1/products/{id}", "description": "Soft Delete Product"},
            {"method": "POST", "endpoint": "/api/v1/services", "description": "Create Service"},
            {"method": "GET", "endpoint": "/api/v1/services", "description": "Get Service List"},
            {"method": "GET", "endpoint": "/api/v1/services/{id}", "description": "Get Service Details"},
            {"method": "PUT", "endpoint": "/api/v1/services/{id}", "description": "Update Service"},
            {"method": "DELETE", "endpoint": "/api/v1/services/{id}", "description": "Soft Delete Service"},
            {"method": "POST", "endpoint": "/api/v1/dynamic-attributes", "description": "Create Dynamic Attribute"},
            {"method": "GET", "endpoint": "/api/v1/dynamic-attributes", "description": "Get Dynamic Attributes"},
            {"method": "GET", "endpoint": "/api/v1/dynamic-attributes/{id}", "description": "Get Dynamic Attribute"},
            {"method": "PUT", "endpoint": "/api/v1/dynamic-attributes/{id}", "description": "Update Dynamic Attribute"},
            {"method": "DELETE", "endpoint": "/api/v1/dynamic-attributes/{id}", "description": "Soft Delete Dynamic Attribute"},
            {"method": "GET", "endpoint": "/api/v1/search/enterprises", "description": "Search Enterprises"},
            {"method": "GET", "endpoint": "/api/v1/search/products", "description": "Search Products"},
            {"method": "GET", "endpoint": "/api/v1/search/services", "description": "Search Services"},
            {"method": "POST", "endpoint": "/api/v1/onboarding-forms", "description": "Create Onboarding Form"},
            {"method": "GET", "endpoint": "/api/v1/onboarding-forms", "description": "List Onboarding Forms"},
            {"method": "GET", "endpoint": "/api/v1/onboarding-forms/{id}", "description": "Get Onboarding Form"},
            {"method": "PUT", "endpoint": "/api/v1/onboarding-forms/{id}", "description": "Update Onboarding Form"},
            {"method": "PUT", "endpoint": "/api/v1/onboarding-forms/{id}/publish", "description": "Publish Onboarding Form"},
            {"method": "PUT", "endpoint": "/api/v1/onboarding-forms/{id}/unpublish", "description": "Unpublish Onboarding Form"},
            {"method": "POST", "endpoint": "/api/v1/onboarding-forms/{id}/duplicate", "description": "Duplicate Onboarding Form"},
            {"method": "DELETE", "endpoint": "/api/v1/onboarding-forms/{id}", "description": "Deactivate Onboarding Form"},
            {"method": "GET", "endpoint": "/api/v1/health", "description": "Health Check"},
            {"method": "GET", "endpoint": "/api/v1/inventory", "description": "API Inventory"},
        ]
    }
