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
        "total_apis": 22,
        "apis": [
            {
                "method": "POST",
                "endpoint": "/api/enterprises",
                "description": "Create Enterprise"
            },
            {
                "method": "GET",
                "endpoint": "/api/enterprises",
                "description": "Get Enterprise List"
            },
            {
                "method": "GET",
                "endpoint": "/api/enterprises/{id}",
                "description": "Get Enterprise Details"
            },
            {
                "method": "PUT",
                "endpoint": "/api/enterprises/{id}",
                "description": "Update Enterprise"
            },
            {
                "method": "DELETE",
                "endpoint": "/api/enterprises/{id}",
                "description": "Mark Enterprise Inactive"
            },

            {
                "method": "POST",
                "endpoint": "/api/products",
                "description": "Create Product"
            },
            {
                "method": "GET",
                "endpoint": "/api/products",
                "description": "Get Product List"
            },
            {
                "method": "GET",
                "endpoint": "/api/products/{id}",
                "description": "Get Product Details"
            },
            {
                "method": "PUT",
                "endpoint": "/api/products/{id}",
                "description": "Update Product"
            },
            {
                "method": "DELETE",
                "endpoint": "/api/products/{id}",
                "description": "Mark Product Inactive"
            },

            {
                "method": "POST",
                "endpoint": "/api/services",
                "description": "Create Service"
            },
            {
                "method": "GET",
                "endpoint": "/api/services",
                "description": "Get Service List"
            },
            {
                "method": "GET",
                "endpoint": "/api/services/{id}",
                "description": "Get Service Details"
            },
            {
                "method": "PUT",
                "endpoint": "/api/services/{id}",
                "description": "Update Service"
            },
            {
                "method": "DELETE",
                "endpoint": "/api/services/{id}",
                "description": "Mark Service Inactive"
            },

            {
                "method": "POST",
                "endpoint": "/api/attributes",
                "description": "Create Dynamic Attribute"
            },
            {
                "method": "GET",
                "endpoint": "/api/attributes",
                "description": "Get Attributes"
            },
            {
                "method": "PUT",
                "endpoint": "/api/attributes/{id}",
                "description": "Update Attribute"
            },
            {
                "method": "DELETE",
                "endpoint": "/api/attributes/{id}",
                "description": "Delete Attribute"
            },

            {
                "method": "GET",
                "endpoint": "/api/health",
                "description": "Health Check"
            },
            {
                "method": "GET",
                "endpoint": "/api/inventory",
                "description": "API Inventory"
            }
        ]
    }